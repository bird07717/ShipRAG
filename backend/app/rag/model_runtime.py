"""Admin view of which model each type will actually run with.

The decision itself lives in ``resolve_provider_kind`` and is shared with the
runtime factories, so this view reports the same outcome a request or index
build would get.  Stored keys are reported by presence only (never decrypted
here); a stored ciphertext counts as a key exactly as the readiness check does.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.ingestion.embedding import FakeEmbeddingProvider
from app.rag.model_providers import (
    environment_api_key,
    is_supported_provider,
    normalize_provider,
    resolve_provider_kind,
)

MODEL_TYPES = ("LLM", "EMBEDDING", "RERANK", "OCR", "VISION")

# Provider/model a factory falls back to when no enabled row exists.  LLM and
# Rerank have no fallback: the answer path rejects the turn instead.
_ENV_FALLBACK: dict[str, tuple[str, str]] = {
    "EMBEDDING": ("siliconflow", "siliconflow_embedding_model"),
    "OCR": ("siliconflow", "siliconflow_ocr_model"),
    "VISION": ("zhipu", "zhipu_vision_model"),
}


def resolve_model_runtime(
    settings: Settings, model_type: str, row: dict[str, Any] | None
) -> dict[str, Any]:
    """Resolve one model type from its enabled ``model_config`` row (or none)."""
    source = "DATABASE" if row is not None else "ENV"
    if row is not None and not is_supported_provider(model_type, str(row["provider"])):
        if model_type in _ENV_FALLBACK:
            # Same as ingestion/model_config.load_build_models: an unsupported
            # row is ignored and the build runs on the environment provider.
            row = None
            source = "ENV_FALLBACK"
        else:
            # LLM / Rerank have no fallback; the provider cannot be called.
            return {
                "model_type": model_type,
                "source": "DATABASE",
                "provider": normalize_provider(str(row["provider"])),
                "model_name": str(row["model_name"]),
                "stored_key_configured": bool(row["stored_key_configured"]),
                "environment_key_configured": False,
                "kind": "UNAVAILABLE",
                "reason": "UNSUPPORTED_PROVIDER",
            }
    if row is None:
        fallback = _ENV_FALLBACK.get(model_type)
        if fallback is None:
            return {
                "model_type": model_type,
                "source": "NONE",
                "provider": None,
                "model_name": None,
                "stored_key_configured": False,
                "environment_key_configured": False,
                "kind": "NOT_CONFIGURED",
                "reason": "MODEL_NOT_ENABLED",
            }
        provider, model_attribute = fallback
        model_name: str | None = str(getattr(settings, model_attribute))
        stored_key = False
    else:
        provider = normalize_provider(str(row["provider"]))
        model_name = str(row["model_name"])
        stored_key = bool(row["stored_key_configured"])
    environment_key = settings.has_secret(environment_api_key(settings, model_type, provider))
    resolution = resolve_provider_kind(
        settings, model_type, provider, has_key=stored_key or environment_key
    )
    return {
        "model_type": model_type,
        "source": source,
        "provider": provider,
        "model_name": model_name,
        "stored_key_configured": stored_key,
        "environment_key_configured": environment_key,
        "kind": resolution.kind,
        "reason": resolution.reason,
    }


async def model_runtime_status(engine: AsyncEngine, settings: Settings) -> dict[str, Any]:
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    SELECT model_type, provider, model_name,
                           api_key_ciphertext IS NOT NULL AS stored_key_configured
                    FROM model_config
                    WHERE enabled
                    ORDER BY updated_at DESC
                    """
                )
            )
        ).mappings()
        enabled: dict[str, dict[str, Any]] = {}
        for raw in rows:
            # Runtime readers expect at most one enabled row per type; keep
            # the most recently updated one if that invariant is ever broken.
            enabled.setdefault(str(raw["model_type"]), dict(raw))
        simulated_indexes = int(
            (
                await connection.execute(
                    text(
                        """
                        SELECT count(*) FROM knowledge_index
                        WHERE status = 'ACTIVE' AND embedding_model_name = :name
                        """
                    ),
                    {"name": FakeEmbeddingProvider.model_name},
                )
            ).scalar_one()
        )
    models = [
        resolve_model_runtime(settings, model_type, enabled.get(model_type))
        for model_type in MODEL_TYPES
    ]
    return {
        "environment": settings.app_env,
        "models": models,
        "simulated_active_index_count": simulated_indexes,
        "simulated": simulated_indexes > 0 or any(item["kind"] == "FAKE" for item in models),
    }
