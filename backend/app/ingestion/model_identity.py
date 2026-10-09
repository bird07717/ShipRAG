"""Non-secret identities of the models that actually produce index content."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx

from app.core.config import Settings
from app.rag.model_providers import environment_base_url, normalize_provider, resolve_provider_kind


def normalized_base_url(value: str | None) -> str | None:
    return str(httpx.URL(value)).rstrip("/") if value else None


def provider_identity(provider: Any, model_type: str, settings: Settings) -> dict[str, Any]:
    client = getattr(provider, "client", None)
    base_url = str(client.base_url) if client is not None else None
    identity: dict[str, Any] = {
        "provider": normalize_provider(str(provider.provider)),
        "model_name": str(provider.model_name),
        "base_url": normalized_base_url(base_url),
    }
    if model_type == "EMBEDDING":
        identity["dimension"] = int(getattr(provider, "dimension", settings.embedding_dimension))
    elif identity["provider"] not in {"fake", "disabled", "unavailable"}:
        # These affect real image output, but are not required by the
        # ImageUnderstandingProvider protocol (e.g. injected adapters).
        for field in ("max_tokens", "max_output_chars"):
            value = getattr(provider, field, None)
            if value is not None:
                identity[field] = int(value)
    return identity


def configured_embedding_identity(
    settings: Settings, *, provider: str, model_name: str, base_url: str | None, has_key: bool
) -> dict[str, Any]:
    """Same effective identity as the factory, without creating an HTTP client."""
    provider = normalize_provider(provider)
    resolution = resolve_provider_kind(settings, "EMBEDDING", provider, has_key=has_key)
    if resolution.kind == "FAKE":
        return {
            "provider": "fake",
            "model_name": "deterministic-shake256",
            "base_url": None,
            "dimension": settings.embedding_dimension,
        }
    return {
        "provider": provider,
        "model_name": model_name,
        "base_url": normalized_base_url(
            base_url or environment_base_url(settings, "EMBEDDING", provider)
        ),
        "dimension": settings.embedding_dimension,
    }


def model_fingerprint(models: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(models, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def index_embedding_matches(
    build_models: dict[str, Any],
    index_model_name: str,
    current: dict[str, Any],
    *,
    legacy_unchanged: bool = True,
) -> bool:
    if build_models:
        return build_models.get("version") == 1 and build_models.get("EMBEDDING") == current
    # Legacy indexes cannot reconstruct a past endpoint. Only retain the
    # unchanged audited config; never synthesize a historical snapshot.
    return legacy_unchanged and index_model_name == current["model_name"]


def legacy_embedding_unchanged_sql(alias: str) -> str:
    """Internal aliases only; callers also check the original model row ID."""
    return f"""NOT EXISTS (
        SELECT 1 FROM model_config_history h
        WHERE h.model_config_id = {alias}.embedding_model_id
          AND h.created_at >= {alias}.created_at
          AND h.changed_fields ?| ARRAY['provider', 'model_name', 'base_url']
    ) AS legacy_embedding_unchanged"""
