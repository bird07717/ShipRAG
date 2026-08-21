"""Unit tests for the admin model/rag-config write path (fake engine)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.schemas import ModelConfigUpdate, RagConfigUpdate
from app.common.errors import ApiError
from app.core.config import Settings
from app.rag.repository import RagRepository


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _FakeResult:
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def one(self) -> dict[str, Any]:
        return self._rows[0]


class _FakeConnection:
    def __init__(self, results: list[Any]) -> None:
        self.results = list(results)
        self.executed: list[tuple[str, dict[str, Any] | None]] = []

    async def __aenter__(self) -> _FakeConnection:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def execute(
        self, statement: Any, params: dict[str, Any] | None = None
    ) -> _FakeResult:
        self.executed.append((str(statement), params))
        if not self.results:
            return _FakeResult([])
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResult(item)


class _FakeEngine:
    def __init__(self, results: list[Any]) -> None:
        self.connection = _FakeConnection(results)

    def begin(self) -> _FakeConnection:
        return self.connection

    def connect(self) -> _FakeConnection:
        return self.connection


def _settings() -> Settings:
    return Settings(_env_file=None, app_env="test")


def _updated_row() -> dict[str, Any]:
    return {
        "id": uuid4(),
        "name": "default-llm",
        "model_type": "LLM",
        "provider": "zhipu",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "model_name": "glm-5.2",
        "parameters": {"temperature": 0.1, "max_tokens": 4096},
        "enabled": True,
        "stored_key_configured": False,
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
    }


@pytest.mark.asyncio
async def test_update_model_config_writes_llm_fields() -> None:
    model_id = uuid4()
    engine = _FakeEngine([[{"id": model_id, "model_type": "LLM"}], [_updated_row()]])
    repository = RagRepository(cast(AsyncEngine, engine), _settings())

    result = await repository.update_model_config(
        model_id,
        model_name="glm-5.2",
        base_url="https://open.bigmodel.cn/api/paas/v4/",
        parameters={"temperature": 0.2, "thinking": {"type": "enabled"}},
        enabled=True,
    )

    assert result["api_key_configured"] is False
    update_sql = engine.connection.executed[1][0]
    assert "model_name = :model_name" in update_sql
    assert "base_url = :base_url" in update_sql
    assert "parameters = CAST(:parameters AS jsonb)" in update_sql
    assert (engine.connection.executed[1][1] or {}).get("parameters") == (
        '{"temperature": 0.2, "thinking": {"type": "enabled"}}'
    )


@pytest.mark.asyncio
async def test_update_model_config_rejects_unknown_model() -> None:
    engine = _FakeEngine([[]])
    repository = RagRepository(cast(AsyncEngine, engine), _settings())

    with pytest.raises(ApiError) as exc_info:
        await repository.update_model_config(uuid4(), model_name="glm-5.2")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_update_model_config_rejects_non_chat_models() -> None:
    engine = _FakeEngine([[{"id": uuid4(), "model_type": "EMBEDDING"}]])
    repository = RagRepository(cast(AsyncEngine, engine), _settings())

    with pytest.raises(ApiError) as exc_info:
        await repository.update_model_config(uuid4(), model_name="other-embedding")
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_update_model_config_maps_unique_violation_to_conflict() -> None:
    model_id = uuid4()
    engine = _FakeEngine(
        [[{"id": model_id, "model_type": "LLM"}], IntegrityError("", None, Exception())]
    )
    repository = RagRepository(cast(AsyncEngine, engine), _settings())

    with pytest.raises(ApiError) as exc_info:
        await repository.update_model_config(model_id, enabled=True)
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_get_rag_config_returns_stored_row() -> None:
    stored = {
        "vector_top_k": 5,
        "bm25_top_k": 6,
        "fusion_top_k": 7,
        "rerank_top_n": 4,
        "context_max_chunks": 3,
        "updated_at": datetime.now(),
    }
    engine = _FakeEngine([[stored]])
    repository = RagRepository(cast(AsyncEngine, engine), _settings())

    assert await repository.get_rag_config() == stored


@pytest.mark.asyncio
async def test_get_rag_config_falls_back_to_settings_defaults() -> None:
    engine = _FakeEngine([[]])
    repository = RagRepository(cast(AsyncEngine, engine), _settings())

    config = await repository.get_rag_config()
    assert config == {
        "vector_top_k": 10,
        "bm25_top_k": 10,
        "fusion_top_k": 20,
        "rerank_top_n": 10,
        "context_max_chunks": 8,
        "updated_at": None,
    }


@pytest.mark.asyncio
async def test_update_rag_config_builds_partial_update() -> None:
    updated = {
        "vector_top_k": 12,
        "bm25_top_k": 10,
        "fusion_top_k": 20,
        "rerank_top_n": 10,
        "context_max_chunks": 8,
        "updated_at": datetime.now(),
    }
    engine = _FakeEngine([[], [updated]])
    repository = RagRepository(cast(AsyncEngine, engine), _settings())

    result = await repository.update_rag_config(vector_top_k=12)
    assert result["vector_top_k"] == 12
    statements = engine.connection.executed
    assert "INSERT INTO rag_config" in statements[0][0]
    assert "vector_top_k = :vector_top_k" in statements[1][0]
    assert "bm25_top_k" not in statements[1][0].split("RETURNING")[0].split(", ")[1]


@pytest.mark.asyncio
async def test_update_rag_config_requires_a_field() -> None:
    engine = _FakeEngine([])
    repository = RagRepository(cast(AsyncEngine, engine), _settings())

    with pytest.raises(ApiError) as exc_info:
        await repository.update_rag_config()
    assert exc_info.value.status_code == 422


def test_model_config_update_validates_base_url() -> None:
    with pytest.raises(ValidationError):
        ModelConfigUpdate(base_url="https://example.com/api")
    with pytest.raises(ValidationError):
        ModelConfigUpdate(base_url="ftp://example.com/")
    assert ModelConfigUpdate(base_url="https://example.com/api/").base_url is not None


def test_model_config_update_validates_parameters() -> None:
    with pytest.raises(ValidationError):
        ModelConfigUpdate(parameters={"temperature": 5})
    with pytest.raises(ValidationError):
        ModelConfigUpdate(parameters={"max_tokens": 10})
    with pytest.raises(ValidationError):
        ModelConfigUpdate(parameters={"thinking": {"type": "maybe"}})
    with pytest.raises(ValidationError):
        ModelConfigUpdate()
    update = ModelConfigUpdate(
        parameters={"temperature": 0.3, "max_tokens": 8192, "thinking": {"type": "disabled"}}
    )
    assert update.parameters is not None


def test_rag_config_update_requires_a_field() -> None:
    with pytest.raises(ValidationError):
        RagConfigUpdate()
    assert RagConfigUpdate(vector_top_k=15).vector_top_k == 15
