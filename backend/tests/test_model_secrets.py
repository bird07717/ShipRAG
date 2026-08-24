"""Tests for API key envelope encryption, key precedence and probe wiring."""

from __future__ import annotations

import asyncio
import base64
import os
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.common.errors import ApiError
from app.core.secrets import ModelSecretBox
from app.rag.llm import ZhipuLlmProvider, create_llm_provider
from app.rag.model_probe import probe_model
from app.rag.models import ModelSnapshot
from app.rag.rerank import SiliconFlowRerankProvider, create_rerank_provider


def _raw_key() -> bytes:
    return os.urandom(32)


def _secret(raw: bytes) -> SecretStr:
    return SecretStr(base64.b64encode(raw).decode("ascii"))


class TestModelSecretBox:
    def test_roundtrip(self) -> None:
        box = ModelSecretBox(_secret(_raw_key()))
        ciphertext, nonce = box.encrypt("sk-live-123")
        assert ciphertext != b"sk-live-123"
        decrypted = box.decrypt(ciphertext, nonce, box.version)
        assert decrypted.get_secret_value() == "sk-live-123"

    def test_wrong_key_fails(self) -> None:
        box = ModelSecretBox(_secret(_raw_key()))
        ciphertext, nonce = box.encrypt("sk-live-123")
        other = ModelSecretBox(_secret(_raw_key()))
        with pytest.raises(ApiError) as excinfo:
            other.decrypt(ciphertext, nonce, box.version)
        assert excinfo.value.code == "API_KEY_DECRYPT_FAILED"

    def test_previous_version_decrypts(self) -> None:
        old_key, new_key = _raw_key(), _raw_key()
        old_box = ModelSecretBox(_secret(old_key), version=1)
        ciphertext, nonce = old_box.encrypt("sk-live-123")
        new_box = ModelSecretBox(
            _secret(new_key),
            version=2,
            previous_key=_secret(old_key),
            previous_version=1,
        )
        assert new_box.decrypt(ciphertext, nonce, 1).get_secret_value() == "sk-live-123"
        fresh, fresh_nonce = new_box.encrypt("sk-new")
        assert new_box.decrypt(fresh, fresh_nonce, 2).get_secret_value() == "sk-new"

    def test_unknown_version_rejected(self) -> None:
        box = ModelSecretBox(_secret(_raw_key()))
        ciphertext, nonce = box.encrypt("sk-live-123")
        with pytest.raises(ApiError) as excinfo:
            box.decrypt(ciphertext, nonce, 99)
        assert excinfo.value.code == "MODEL_SECRET_NOT_CONFIGURED"

    def test_invalid_key_material_rejected(self) -> None:
        with pytest.raises(ApiError) as excinfo:
            ModelSecretBox(SecretStr("not-base64!!"))
        assert excinfo.value.code == "MODEL_SECRET_NOT_CONFIGURED"
        short = base64.b64encode(b"too-short").decode("ascii")
        with pytest.raises(ApiError):
            ModelSecretBox(SecretStr(short))


class TestProviderKeyPrecedence:
    def _settings(self, monkeypatch: pytest.MonkeyPatch) -> Any:
        from app.core.config import Settings

        monkeypatch.setenv("ZHIPU_API_KEY", "")
        monkeypatch.setenv("SILICONFLOW_API_KEY", "")
        return Settings(
            _env_file=None,
            app_env="test",
            m3_llm_provider="zhipu",
            m5_rerank_provider="siliconflow",
        )

    def test_snapshot_key_wins_over_missing_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = self._settings(monkeypatch)
        snapshot = ModelSnapshot(
            provider="zhipu",
            model_name="glm-5.2",
            parameters={},
            api_key=SecretStr("sk-from-db"),
        )
        provider = ZhipuLlmProvider(settings, snapshot)
        try:
            assert provider.client.headers["Authorization"] == "Bearer sk-from-db"
        finally:
            asyncio.run(provider.aclose())

    def test_env_key_used_when_snapshot_has_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.core.config import Settings

        monkeypatch.setenv("ZHIPU_API_KEY", "")
        settings = Settings(
            _env_file=None,
            app_env="test",
            m3_llm_provider="zhipu",
            zhipu_api_key=SecretStr("sk-from-env"),
        )
        snapshot = ModelSnapshot(provider="zhipu", model_name="glm-5.2", parameters={})
        provider = ZhipuLlmProvider(settings, snapshot)
        assert provider.client.headers["Authorization"] == "Bearer sk-from-env"

    def test_create_llm_provider_auto_uses_snapshot_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = self._settings(monkeypatch)
        snapshot = ModelSnapshot(
            provider="zhipu",
            model_name="glm-5.2",
            parameters={},
            api_key=SecretStr("sk-from-db"),
        )
        assert isinstance(create_llm_provider(settings, snapshot), ZhipuLlmProvider)

    def test_create_rerank_provider_auto_uses_snapshot_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core.config import Settings

        monkeypatch.setenv("SILICONFLOW_API_KEY", "")
        settings = Settings(
            _env_file=None,
            app_env="test",
            m5_rerank_provider="siliconflow",
        )
        snapshot = ModelSnapshot(
            provider="siliconflow",
            model_name="Qwen3-VL-Reranker-8B",
            parameters={},
            api_key=SecretStr("sk-from-db"),
        )
        assert isinstance(create_rerank_provider(settings, snapshot), SiliconFlowRerankProvider)


class TestProbeModel:
    async def test_embedding_probe_reports_dimension(self) -> None:
        from app.core.config import Settings

        settings = Settings(
            _env_file=None,
            app_env="test",
        )
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": [{"embedding": [0.1] * 1024}]})
        )
        result = await probe_model(
            settings,
            model_type="EMBEDDING",
            provider="siliconflow",
            model_name="qwen",
            base_url=None,
            api_key=SecretStr("sk-test"),
            client=httpx.AsyncClient(transport=transport),
        )
        assert result["ok"] is True
        assert result["dimension"] == 1024

    async def test_embedding_dimension_mismatch_reported(self) -> None:
        from app.core.config import Settings

        settings = Settings(_env_file=None, app_env="test")
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})
        )
        result = await probe_model(
            settings,
            model_type="EMBEDDING",
            provider="siliconflow",
            model_name="qwen",
            base_url=None,
            api_key=SecretStr("sk-test"),
            client=httpx.AsyncClient(transport=transport),
        )
        assert result["ok"] is False
        assert "维度" in result["detail"]

    async def test_upstream_error_reported(self) -> None:
        from app.core.config import Settings

        settings = Settings(_env_file=None, app_env="test")
        transport = httpx.MockTransport(lambda request: httpx.Response(503))
        result = await probe_model(
            settings,
            model_type="LLM",
            provider="zhipu",
            model_name="glm-5.2",
            base_url=None,
            api_key=SecretStr("sk-test"),
            client=httpx.AsyncClient(transport=transport),
        )
        assert result["ok"] is False
        assert "503" in result["detail"]

    async def test_missing_key_reported(self) -> None:
        from app.core.config import Settings

        settings = Settings(_env_file=None, app_env="test")
        result = await probe_model(
            settings,
            model_type="LLM",
            provider="zhipu",
            model_name="glm-5.2",
            base_url=None,
            api_key=None,
        )
        assert result["ok"] is False
        assert result["detail"] == "未配置 API 密钥"


class _StubRepository:
    """Backs the key-management endpoints without a real database."""

    def __init__(self, model_secret_key: SecretStr | None = None) -> None:
        self.model_secret_key = model_secret_key
        self.api_key_calls: list[tuple[str, str]] = []

    async def set_api_key(self, model_id: Any, api_key: str, *, actor: str) -> dict[str, Any]:
        self.api_key_calls.append(("set", actor))
        return self._row()

    async def clear_api_key(self, model_id: Any, *, actor: str) -> dict[str, Any]:
        self.api_key_calls.append(("clear", actor))
        return self._row()

    async def read_model_with_key(self, model_id: Any) -> dict[str, Any]:
        row = self._row()
        row["api_key"] = None
        return row

    def _row(self) -> dict[str, Any]:
        return {
            "id": str(uuid4()),
            "name": "llm-primary",
            "model_type": "LLM",
            "provider": "zhipu",
            "base_url": "https://open.bigmodel.cn/api/paas/v4/",
            "model_name": "glm-5.2",
            "parameters": {},
            "enabled": True,
            "api_key_configured": True,
            "api_key_source": "DATABASE",
            "created_at": "2026-08-21T00:00:00Z",
            "updated_at": "2026-08-21T00:00:00Z",
        }


class _StubEmbeddingRepository(_StubRepository):
    def __init__(self) -> None:
        super().__init__()
        self.update_calls: list[dict[str, Any]] = []

    async def read_model_with_key(self, model_id: Any) -> dict[str, Any]:
        row = self._row()
        row.update(
            {
                "name": "default-embedding",
                "model_type": "EMBEDDING",
                "provider": "siliconflow",
                "base_url": "https://api.siliconflow.cn/v1/",
                "model_name": "Qwen/Qwen3-VL-Embedding-8B",
            }
        )
        row["api_key"] = None
        return row

    async def update_model_config(self, model_id: Any, **payload: Any) -> dict[str, Any]:
        self.update_calls.append(payload)
        return self._row()


def _probe_result(ok: bool, detail: str, dimension: int | None = None) -> dict[str, Any]:
    return {
        "ok": ok,
        "model_type": "EMBEDDING",
        "provider": "siliconflow",
        "model_name": "candidate",
        "latency_ms": 1,
        "detail": detail,
        "dimension": dimension,
    }


class TestEmbeddingDimensionGate:
    def _patch(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        probe_result: dict[str, Any],
    ) -> _StubEmbeddingRepository:
        from app.api import rag as rag_api
        from app.api.rag import get_app_settings, get_rag_repository
        from app.core.config import Settings

        stub = _StubEmbeddingRepository()
        client.app.dependency_overrides[get_rag_repository] = lambda: stub
        client.app.dependency_overrides[get_app_settings] = lambda: Settings(
            _env_file=None, app_env="test"
        )
        monkeypatch.setattr(
            rag_api, "probe_model", lambda *args, **kwargs: _async_value(probe_result)
        )
        return stub

    def test_dimension_mismatch_rejected(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = self._patch(
            client,
            monkeypatch,
            _probe_result(False, "输出维度 768 与冻结维度 1024 不一致", 768),
        )
        try:
            response = client.patch(
                "/api/v1/models/00000000-0000-0000-0000-000000000001",
                json={"model_name": "other-embedding"},
                headers={"Authorization": "Bearer test-token"},
            )
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "EMBEDDING_DIMENSION_MISMATCH"
            assert stub.update_calls == []
        finally:
            client.app.dependency_overrides.clear()

    def test_probe_unreachable_rejected(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = self._patch(client, monkeypatch, _probe_result(False, "上游网络异常或超时"))
        try:
            response = client.patch(
                "/api/v1/models/00000000-0000-0000-0000-000000000001",
                json={"base_url": "https://other.example.com/v1/"},
                headers={"Authorization": "Bearer test-token"},
            )
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "MODEL_PROBE_FAILED"
            assert stub.update_calls == []
        finally:
            client.app.dependency_overrides.clear()

    def test_probe_pass_allows_update(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = self._patch(
            client,
            monkeypatch,
            _probe_result(True, "连通正常", 1024),
        )
        try:
            response = client.patch(
                "/api/v1/models/00000000-0000-0000-0000-000000000001",
                json={"model_name": "other-embedding"},
                headers={"Authorization": "Bearer test-token"},
            )
            assert response.status_code == 200
            assert stub.update_calls != []
        finally:
            client.app.dependency_overrides.clear()

    def test_unchanged_values_skip_probe(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = self._patch(
            client,
            monkeypatch,
            _probe_result(False, "不应被调用"),
        )
        try:
            response = client.patch(
                "/api/v1/models/00000000-0000-0000-0000-000000000001",
                json={"model_name": "Qwen/Qwen3-VL-Embedding-8B"},  # same as stored
                headers={"Authorization": "Bearer test-token"},
            )
            assert response.status_code == 200
            assert stub.update_calls != []
        finally:
            client.app.dependency_overrides.clear()

    def test_parameter_only_change_skips_probe(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = self._patch(
            client,
            monkeypatch,
            _probe_result(False, "不应被调用"),
        )
        try:
            response = client.patch(
                "/api/v1/models/00000000-0000-0000-0000-000000000001",
                json={"parameters": {"batch_size": 16}, "enabled": True},
                headers={"Authorization": "Bearer test-token"},
            )
            assert response.status_code == 200
            assert stub.update_calls != []
        finally:
            client.app.dependency_overrides.clear()


async def _async_value(value: Any) -> Any:
    return value


class TestApiKeyEndpoints:
    def test_put_and_delete_api_key(self, client: TestClient) -> None:
        from app.api.rag import get_rag_repository

        stub = _StubRepository()
        client.app.dependency_overrides[get_rag_repository] = lambda: stub
        try:
            response = client.put(
                "/api/v1/models/00000000-0000-0000-0000-000000000001/api-key",
                json={"api_key": "sk-live-123"},
                headers={"Authorization": "Bearer test-token"},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["api_key_source"] == "DATABASE"
            # No key material may ever appear in the response.
            assert "sk-live-123" not in response.text
            assert "api_key_ciphertext" not in body
            assert "api_key" not in body

            deleted = client.delete(
                "/api/v1/models/00000000-0000-0000-0000-000000000001/api-key",
                headers={"Authorization": "Bearer test-token"},
            )
            assert deleted.status_code == 200
            assert [call[0] for call in stub.api_key_calls] == ["set", "clear"]
            assert all(call[1] for call in stub.api_key_calls)  # actor = request id
        finally:
            client.app.dependency_overrides.pop(get_rag_repository, None)

    def test_probe_endpoint(self, client: TestClient) -> None:
        from app.api.rag import get_app_settings, get_rag_repository
        from app.core.config import Settings

        stub = _StubRepository()
        probe_settings = Settings(_env_file=None, app_env="test")
        client.app.dependency_overrides[get_rag_repository] = lambda: stub
        client.app.dependency_overrides[get_app_settings] = lambda: probe_settings
        try:
            response = client.post(
                "/api/v1/models/00000000-0000-0000-0000-000000000001/probe",
                headers={"Authorization": "Bearer test-token"},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["ok"] is False
            assert body["detail"] == "未配置 API 密钥"
        finally:
            client.app.dependency_overrides.pop(get_rag_repository, None)
            client.app.dependency_overrides.pop(get_app_settings, None)
