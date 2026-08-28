"""Tests for build-time model config consumption (DB snapshot > env)."""

from __future__ import annotations

import asyncio

from pydantic import SecretStr

from app.core.config import Settings
from app.ingestion.embedding import (
    FakeEmbeddingProvider,
    SiliconFlowEmbeddingProvider,
    create_embedding_provider,
)
from app.ingestion.image_understanding import (
    FakeImageUnderstandingProvider,
    OpenAiCompatibleImageProvider,
    UnavailableImageUnderstandingProvider,
    create_ocr_provider,
    create_vision_provider,
)
from app.rag.models import ModelSnapshot


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, app_env="test", **overrides)  # type: ignore[arg-type]


def _snapshot(
    provider: str, model_name: str, *, api_key: str | None = None, base_url: str | None = None
) -> ModelSnapshot:
    return ModelSnapshot(
        provider=provider,
        model_name=model_name,
        parameters={},
        base_url=base_url,
        api_key=SecretStr(api_key) if api_key else None,
    )


class TestEmbeddingProviderConfig:
    def test_snapshot_model_and_key_win(self) -> None:
        settings = _settings(siliconflow_api_key=SecretStr("sk-env"))
        snapshot = _snapshot(
            "siliconflow",
            "custom-embed",
            api_key="sk-db",
            base_url="https://db.example.com/v1/",
        )
        provider = SiliconFlowEmbeddingProvider(settings, snapshot=snapshot)
        try:
            assert provider.model_name == "custom-embed"
            assert str(provider.client.base_url) == "https://db.example.com/v1/"
            assert provider.client.headers["Authorization"] == "Bearer sk-db"
        finally:
            asyncio.run(provider.aclose())

    def test_auto_mode_uses_snapshot_key_without_env(self) -> None:
        settings = _settings()  # no env key
        snapshot = _snapshot("siliconflow", "custom-embed", api_key="sk-db")
        provider = create_embedding_provider(settings, snapshot)
        assert isinstance(provider, SiliconFlowEmbeddingProvider)

    def test_zhipu_snapshot_uses_zhipu_fallbacks(self) -> None:
        settings = _settings(zhipu_api_key=SecretStr("sk-zhipu"))
        provider = create_embedding_provider(
            settings,
            _snapshot("zhipu", "embedding-3", base_url="https://open.bigmodel.cn/api/paas/v4/"),
        )
        assert isinstance(provider, SiliconFlowEmbeddingProvider)
        try:
            assert provider.provider == "zhipu"
            assert provider.client.headers["Authorization"] == "Bearer sk-zhipu"
        finally:
            asyncio.run(provider.aclose())

    def test_auto_mode_falls_back_to_fake_without_any_key(self) -> None:
        settings = _settings()
        provider = create_embedding_provider(settings, _snapshot("siliconflow", "m"))
        assert isinstance(provider, FakeEmbeddingProvider)

    def test_no_snapshot_uses_env(self) -> None:
        settings = _settings(siliconflow_api_key=SecretStr("sk-env"))
        provider = create_embedding_provider(settings, None)
        assert isinstance(provider, SiliconFlowEmbeddingProvider)
        assert provider.model_name == settings.siliconflow_embedding_model
        asyncio.run(provider.aclose())


class TestImageProviderConfig:
    def test_ocr_snapshot_wins(self) -> None:
        settings = _settings(siliconflow_api_key=SecretStr("sk-env"))
        snapshot = _snapshot(
            "siliconflow",
            "custom-ocr",
            api_key="sk-db",
            base_url="https://db.example.com/v1/",
        )
        provider = create_ocr_provider(settings, snapshot)
        assert isinstance(provider, OpenAiCompatibleImageProvider)
        assert provider.model_name == "custom-ocr"
        assert provider.client.base_url == "https://db.example.com/v1/"
        assert provider.client.headers["Authorization"] == "Bearer sk-db"

    def test_vision_snapshot_key_from_zhipu(self) -> None:
        settings = _settings(zhipu_api_key=SecretStr("sk-env"))
        snapshot = _snapshot("zhipu", "custom-vision", api_key="sk-db")
        provider = create_vision_provider(settings, snapshot)
        assert isinstance(provider, OpenAiCompatibleImageProvider)
        assert provider.model_name == "custom-vision"
        assert provider.client.headers["Authorization"] == "Bearer sk-db"

    def test_image_capabilities_accept_the_other_provider(self) -> None:
        settings = _settings(
            zhipu_api_key=SecretStr("sk-zhipu"),
            siliconflow_api_key=SecretStr("sk-siliconflow"),
        )
        ocr = create_ocr_provider(settings, _snapshot("zhipu", "glm-4.6v"))
        vision = create_vision_provider(
            settings, _snapshot("siliconflow", "Qwen/Qwen3-VL-8B-Instruct")
        )
        assert isinstance(ocr, OpenAiCompatibleImageProvider)
        assert isinstance(vision, OpenAiCompatibleImageProvider)
        try:
            assert ocr.provider == "zhipu"
            assert vision.provider == "siliconflow"
            assert ocr.client.headers["Authorization"] == "Bearer sk-zhipu"
            assert vision.client.headers["Authorization"] == "Bearer sk-siliconflow"
        finally:
            asyncio.run(ocr.aclose())
            asyncio.run(vision.aclose())

    def test_ocr_auto_without_any_key_is_fake(self) -> None:
        settings = _settings()
        provider = create_ocr_provider(settings, _snapshot("siliconflow", "m"))
        assert isinstance(provider, FakeImageUnderstandingProvider)

    def test_vision_real_mode_without_key_is_unavailable(self) -> None:
        settings = _settings(m4_vision_provider="zhipu")
        provider = create_vision_provider(settings, None)
        assert isinstance(provider, UnavailableImageUnderstandingProvider)

    def test_vision_real_mode_with_snapshot_key_is_available(self) -> None:
        settings = _settings(m4_vision_provider="zhipu")
        provider = create_vision_provider(settings, _snapshot("zhipu", "v", api_key="sk-db"))
        assert isinstance(provider, OpenAiCompatibleImageProvider)


class TestBuildModelConfigModule:
    def test_expected_provider_map_covers_all_build_types(self) -> None:
        from app.ingestion.model_config import BUILD_MODEL_TYPES
        from app.rag.model_providers import supported_providers

        assert all(
            set(supported_providers(model_type)) == {"zhipu", "siliconflow"}
            for model_type in BUILD_MODEL_TYPES
        )
