from __future__ import annotations

import os
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.ingestion.embedding import (
    EmbeddingError,
    FakeEmbeddingProvider,
    create_embedding_provider,
)
from app.ingestion.image_understanding import create_ocr_provider, create_vision_provider
from app.rag.llm import FakeLlmProvider, LlmError, create_llm_provider
from app.rag.model_providers import resolve_provider_kind
from app.rag.model_runtime import model_runtime_status, resolve_model_runtime
from app.rag.models import ModelSnapshot
from app.rag.rerank import FakeRerankProvider, RerankError, create_rerank_provider

_NO_KEYS: dict[str, Any] = {
    "siliconflow_api_key": None,
    "zhipu_api_key": None,
    "dashscope_api_key": None,
}


def _settings(**overrides: Any) -> Settings:
    return Settings(**{**_NO_KEYS, **overrides})


def _snapshot(provider: str = "siliconflow", key: str | None = None) -> ModelSnapshot:
    return ModelSnapshot(
        provider=provider,
        model_name="m",
        parameters={},
        api_key=SecretStr(key) if key else None,
    )


@pytest.mark.parametrize(
    ("model_type", "env", "mode", "has_key", "expected"),
    [
        # LLM: fake is forbidden when deployed; auto without key only fakes in test.
        ("LLM", "development", "fake", False, ("FAKE", "FAKE_MODE")),
        ("LLM", "production", "fake", True, ("NOT_CONFIGURED", "FAKE_FORBIDDEN")),
        ("LLM", "development", "auto", False, ("NOT_CONFIGURED", "NO_API_KEY")),
        ("LLM", "test", "auto", False, ("FAKE", "AUTO_NO_KEY")),
        ("LLM", "production", "auto", True, ("REAL", "CONFIGURED")),
        ("LLM", "development", "zhipu", False, ("REAL", "EXPLICIT_PROVIDER")),
        # ADR-0034: auto without a key never simulates silently — only in the
        # automated test environment. Explicit fake is allowed outside deployments.
        ("EMBEDDING", "development", "auto", False, ("NOT_CONFIGURED", "NO_API_KEY")),
        ("EMBEDDING", "test", "auto", False, ("FAKE", "AUTO_NO_KEY")),
        ("EMBEDDING", "production", "auto", False, ("NOT_CONFIGURED", "NO_API_KEY")),
        ("EMBEDDING", "production", "fake", True, ("NOT_CONFIGURED", "FAKE_FORBIDDEN")),
        ("EMBEDDING", "development", "auto", True, ("REAL", "CONFIGURED")),
        ("RERANK", "development", "auto", False, ("NOT_CONFIGURED", "NO_API_KEY")),
        ("RERANK", "staging", "auto", False, ("NOT_CONFIGURED", "NO_API_KEY")),
        ("RERANK", "development", "fake", True, ("FAKE", "FAKE_MODE")),
        # OCR / Vision degrade per image, so "cannot run" is UNAVAILABLE.
        ("OCR", "production", "auto", False, ("UNAVAILABLE", "NO_API_KEY")),
        ("OCR", "development", "auto", False, ("UNAVAILABLE", "NO_API_KEY")),
        ("OCR", "test", "auto", False, ("FAKE", "AUTO_NO_KEY")),
        ("OCR", "production", "fake", True, ("UNAVAILABLE", "FAKE_FORBIDDEN")),
        ("OCR", "development", "fake", False, ("FAKE", "FAKE_MODE")),
        ("OCR", "production", "disabled", True, ("DISABLED", "MODE_DISABLED")),
        ("OCR", "development", "siliconflow", False, ("UNAVAILABLE", "NO_API_KEY")),
        ("VISION", "development", "auto", False, ("UNAVAILABLE", "NO_API_KEY")),
        ("VISION", "production", "auto", True, ("REAL", "CONFIGURED")),
    ],
)
def test_resolution_table(
    model_type: str, env: str, mode: str, has_key: bool, expected: tuple[str, str]
) -> None:
    field = {
        "LLM": "m3_llm_provider",
        "EMBEDDING": "m2_embedding_provider",
        "RERANK": "m5_rerank_provider",
        "OCR": "m4_ocr_provider",
        "VISION": "m4_vision_provider",
    }[model_type]
    # Bypass the deployed-environment validator so forbidden combinations
    # can still be resolved (they are rejected at startup in real configs).
    settings = Settings.model_construct(**{**_settings().model_dump(), "app_env": env, field: mode})
    provider = "zhipu" if model_type == "VISION" else "siliconflow"
    result = resolve_provider_kind(settings, model_type, provider, has_key=has_key)
    assert (result.kind, result.reason) == expected


def test_ocr_unsupported_provider_is_unavailable() -> None:
    result = resolve_provider_kind(_settings(), "OCR", "nonexistent", has_key=True)
    assert (result.kind, result.reason) == ("UNAVAILABLE", "UNSUPPORTED_PROVIDER")


@pytest.mark.parametrize("app_env", ["development", "test"])
def test_factories_agree_with_the_status_view(app_env: str) -> None:
    """Whatever the console reports must be what the factory builds."""
    settings = Settings.model_construct(**{**_settings().model_dump(), "app_env": app_env})
    view = {
        model_type: resolve_model_runtime(
            settings,
            model_type,
            {"provider": "siliconflow", "model_name": "m", "stored_key_configured": False},
        )
        for model_type in ("LLM", "EMBEDDING", "RERANK")
    }
    if app_env == "test":
        # Automated tests keep the deterministic fallback for every type.
        assert {item["kind"] for item in view.values()} == {"FAKE"}
        assert isinstance(create_embedding_provider(settings, _snapshot()), FakeEmbeddingProvider)
        assert isinstance(create_rerank_provider(settings, _snapshot()), FakeRerankProvider)
        assert isinstance(create_llm_provider(settings, _snapshot()), FakeLlmProvider)
        return
    # Development without keys fails loudly instead of simulating.
    assert {item["kind"] for item in view.values()} == {"NOT_CONFIGURED"}
    with pytest.raises(EmbeddingError, match="未配置 API Key"):
        create_embedding_provider(settings, _snapshot())
    with pytest.raises(RerankError) as rerank_error:
        create_rerank_provider(settings, _snapshot())
    assert rerank_error.value.code == "MODEL_NOT_CONFIGURED"
    with pytest.raises(LlmError) as raised:
        create_llm_provider(settings, _snapshot())
    assert raised.value.code == "MODEL_NOT_CONFIGURED"


def test_deployed_factories_still_raise_with_original_errors() -> None:
    settings = Settings.model_construct(**{**_settings().model_dump(), "app_env": "production"})
    with pytest.raises(EmbeddingError, match="禁止回退到 fake"):
        create_embedding_provider(settings, _snapshot())
    with pytest.raises(RerankError) as rerank_error:
        create_rerank_provider(settings, _snapshot())
    assert rerank_error.value.code == "MODEL_NOT_CONFIGURED"
    fake_llm = Settings.model_construct(
        **{**_settings().model_dump(), "app_env": "production", "m3_llm_provider": "fake"}
    )
    with pytest.raises(LlmError) as llm_error:
        create_llm_provider(fake_llm, _snapshot(key="k"))
    assert llm_error.value.code == "FAKE_PROVIDER_FORBIDDEN"


def test_image_factories_follow_the_shared_table() -> None:
    settings = Settings.model_construct(**{**_settings().model_dump(), "app_env": "development"})
    # No key outside automated tests: per-image failure, never a fake caption.
    assert type(create_ocr_provider(settings, _snapshot())).__name__ == (
        "UnavailableImageUnderstandingProvider"
    )
    production = Settings.model_construct(**{**_settings().model_dump(), "app_env": "production"})
    assert type(create_vision_provider(production, _snapshot("zhipu"))).__name__ == (
        "UnavailableImageUnderstandingProvider"
    )
    automated = Settings.model_construct(**{**_settings().model_dump(), "app_env": "test"})
    assert type(create_ocr_provider(automated, _snapshot())).__name__ == (
        "FakeImageUnderstandingProvider"
    )
    disabled = _settings(m4_vision_provider="disabled")
    assert type(create_vision_provider(disabled, _snapshot("zhipu"))).__name__ == (
        "DisabledImageUnderstandingProvider"
    )
    unsupported = create_ocr_provider(settings, _snapshot("nonexistent", key="k"))
    assert type(unsupported).__name__ == "UnavailableImageUnderstandingProvider"


def test_view_uses_environment_fallbacks_and_flags_missing_required_models() -> None:
    settings = _settings(siliconflow_api_key=SecretStr("env-key"))
    embedding = resolve_model_runtime(settings, "EMBEDDING", None)
    assert embedding["source"] == "ENV"
    assert embedding["provider"] == "siliconflow"
    assert embedding["environment_key_configured"] is True
    assert embedding["kind"] == "REAL"
    llm = resolve_model_runtime(settings, "LLM", None)
    assert (llm["kind"], llm["reason"], llm["source"]) == (
        "NOT_CONFIGURED",
        "MODEL_NOT_ENABLED",
        "NONE",
    )
    stored = resolve_model_runtime(
        settings,
        "RERANK",
        {"provider": "DashScope", "model_name": "gte-rerank", "stored_key_configured": True},
    )
    assert (stored["provider"], stored["kind"], stored["environment_key_configured"]) == (
        "dashscope",
        "REAL",
        False,
    )


def test_view_mirrors_build_fallback_for_unsupported_provider_rows() -> None:
    settings = _settings(siliconflow_api_key=SecretStr("env-key"))
    legacy = {"provider": "legacy-vendor", "model_name": "x", "stored_key_configured": True}
    ocr = resolve_model_runtime(settings, "OCR", legacy)
    # Index builds ignore the row and run on the environment provider.
    assert (ocr["source"], ocr["provider"], ocr["kind"], ocr["stored_key_configured"]) == (
        "ENV_FALLBACK",
        "siliconflow",
        "REAL",
        False,
    )
    no_key = Settings.model_construct(**{**_settings().model_dump(), "app_env": "development"})
    embedding = resolve_model_runtime(no_key, "EMBEDDING", legacy)
    assert (embedding["source"], embedding["kind"]) == ("ENV_FALLBACK", "NOT_CONFIGURED")
    llm = resolve_model_runtime(settings, "LLM", legacy)
    assert (llm["kind"], llm["reason"], llm["provider"]) == (
        "UNAVAILABLE",
        "UNSUPPORTED_PROVIDER",
        "legacy-vendor",
    )


async def test_status_reads_the_real_database() -> None:
    database_url = os.getenv("PHASE4_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PHASE4_TEST_DATABASE_URL is not configured")
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        status = await model_runtime_status(engine, _settings(app_env="development"))
    finally:
        await engine.dispose()
    assert [item["model_type"] for item in status["models"]] == [
        "LLM",
        "EMBEDDING",
        "RERANK",
        "OCR",
        "VISION",
    ]
    assert status["simulated_active_index_count"] >= 0
    # Without any environment keys, every non-stored model in development is
    # either simulated or unconfigured — never reported as REAL by accident.
    for item in status["models"]:
        if not item["stored_key_configured"] and item["kind"] == "REAL":
            assert item["reason"] == "EXPLICIT_PROVIDER"
    assert status["simulated"] == (
        status["simulated_active_index_count"] > 0
        or any(item["kind"] == "FAKE" for item in status["models"])
    )
