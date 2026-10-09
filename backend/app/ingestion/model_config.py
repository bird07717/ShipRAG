"""Build-time model configuration: DB model_config rows win over env settings.

Index builds snapshot the enabled EMBEDDING / OCR / VISION rows once at build
start, so later admin edits never leak into a running build. Rows whose
provider is unsupported fall back to env settings (source=ENV_FALLBACK) so
builds keep working while the misconfiguration stays visible in task metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.common.errors import ApiError
from app.core.config import Settings
from app.core.secrets import ModelSecretBox
from app.rag.model_providers import is_supported_provider
from app.rag.models import ModelSnapshot

logger = structlog.get_logger(__name__)

BUILD_MODEL_TYPES = ("EMBEDDING", "OCR", "VISION")


@dataclass(frozen=True, slots=True)
class BuildModelConfig:
    snapshot: ModelSnapshot | None
    source: str  # "DATABASE" | "ENV" | "ENV_FALLBACK"
    model_id: UUID | None = None


def _secret_box(settings: Settings) -> ModelSecretBox:
    if settings.model_secret_key is None:
        raise ApiError(
            "MODEL_SECRET_NOT_CONFIGURED",
            "数据库密钥存在但未配置 MODEL_SECRET_KEY，无法构建索引",
            409,
        )
    return ModelSecretBox(
        settings.model_secret_key,
        version=settings.model_secret_key_version,
        previous_key=settings.model_secret_key_previous,
        previous_version=settings.model_secret_key_previous_version,
    )


async def load_build_models(engine: AsyncEngine, settings: Settings) -> dict[str, BuildModelConfig]:
    """Reads enabled build-bound rows once; returns per-type config snapshot."""
    configs: dict[str, BuildModelConfig] = {
        model_type: BuildModelConfig(None, "ENV") for model_type in BUILD_MODEL_TYPES
    }
    async with engine.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT id, model_type, provider, model_name, parameters, base_url,"
                " api_key_ciphertext, api_key_nonce, api_key_key_version"
                " FROM model_config WHERE model_type = ANY(:types) AND enabled"
            ),
            {"types": list(BUILD_MODEL_TYPES)},
        )
        for raw in rows.mappings():
            row = dict(raw)
            model_type = str(row["model_type"])
            if model_type not in BUILD_MODEL_TYPES:
                continue
            if not is_supported_provider(model_type, str(row["provider"])):
                # Unsupported provider would break provider construction;
                # keep the build alive on env settings and surface the
                # misconfiguration through task metadata.
                configs[model_type] = BuildModelConfig(None, "ENV_FALLBACK")
                logger.warning(
                    "build_model_provider_mismatch",
                    model_type=model_type,
                    provider=str(row["provider"]),
                    supported="all configured providers",
                )
                continue
            api_key = None
            if row["api_key_ciphertext"] is not None:
                box = _secret_box(settings)
                api_key = box.decrypt(
                    bytes(row["api_key_ciphertext"]),
                    bytes(row["api_key_nonce"]),
                    int(row["api_key_key_version"] or 1),
                )
            parameters = row["parameters"] if isinstance(row["parameters"], dict) else {}
            configs[model_type] = BuildModelConfig(
                ModelSnapshot(
                    provider=str(row["provider"]),
                    model_name=str(row["model_name"]),
                    parameters=dict(parameters),
                    base_url=(str(row["base_url"]) if row["base_url"] is not None else None),
                    api_key=api_key,
                ),
                "DATABASE",
                UUID(str(row["id"])) if row.get("id") is not None else None,
            )
    return configs
