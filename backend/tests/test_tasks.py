from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.ingestion import get_ingestion_repository
from app.core.config import Settings
from app.ingestion.repository import IngestionRepository
from app.tasks.health import ping

AUTH = {"Authorization": "Bearer test-token"}


def test_worker_ping_payload() -> None:
    result = ping()

    assert result["status"] == "ok"
    assert result["timestamp"].endswith("+00:00")


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _FakeResult:
        return self

    def __iter__(self) -> Any:
        return iter(self._rows)


class _FakeConnection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.executed: list[tuple[str, dict[str, Any] | None]] = []

    async def __aenter__(self) -> _FakeConnection:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        self.executed.append((str(statement), params))
        return _FakeResult(self.rows)


class _FakeEngine:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.connection = _FakeConnection(rows)

    def connect(self) -> _FakeConnection:
        return self.connection

    def begin(self) -> _FakeConnection:
        return self.connection


def _task_row() -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "task_type": "INDEX_BUILD",
        "status": "FAILED",
        "stage": "embedding",
        "progress": 40,
        "attempt": 1,
        "kb_id": str(uuid4()),
        "index_id": str(uuid4()),
        "document_id": None,
        "error_code": "EmbeddingError",
        "error_message": "上游返回 429",
        "metadata": {"reason": "MANUAL"},
        "created_at": datetime.now(UTC),
        "started_at": datetime.now(UTC),
        "finished_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }


class TestListTasksRepository:
    async def test_list_tasks_applies_filters(self) -> None:
        engine = _FakeEngine([_task_row()])
        repository = IngestionRepository(
            cast(AsyncEngine, engine), Settings(_env_file=None, app_env="test")
        )

        rows = await repository.list_tasks(
            status="FAILED",
            knowledge_id=uuid4(),
            created_from=datetime(2026, 8, 19, tzinfo=UTC),
            created_to=datetime(2026, 8, 21, tzinfo=UTC),
            limit=25,
        )

        sql, params = engine.connection.executed[0]
        for fragment in (
            "status = :status",
            "kb_id = :kb_id",
            "created_at >= :created_from",
            "created_at < :created_to",
        ):
            assert fragment in sql
        assert params is not None
        assert params["limit"] == 25
        assert rows[0]["error_code"] == "EmbeddingError"

    async def test_list_tasks_without_filters_lists_everything(self) -> None:
        engine = _FakeEngine([])
        repository = IngestionRepository(
            cast(AsyncEngine, engine), Settings(_env_file=None, app_env="test")
        )

        await repository.list_tasks()

        sql, params = engine.connection.executed[0]
        assert "WHERE 1 = 1" in sql
        assert params is not None and set(params) == {"limit", "offset"}


class _StubIngestionRepository:
    def __init__(self) -> None:
        self.calls: dict[str, Any] = {}

    async def list_tasks(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls = kwargs
        return [_task_row()]


class TestTasksApi:
    def test_global_tasks_endpoint_wires_filters(self, client: TestClient) -> None:
        stub = _StubIngestionRepository()
        client.app.dependency_overrides[get_ingestion_repository] = lambda: stub
        try:
            response = client.get(
                "/api/v1/tasks?status=FAILED&from=2026-08-19T00:00:00&to=2026-08-21T00:00:00"
                "&limit=25",
                headers=AUTH,
            )
            assert response.status_code == 200
            body = response.json()
            assert body[0]["error_code"] == "EmbeddingError"
            assert body[0]["status"] == "FAILED"
            call = stub.calls
            assert call["status"] == "FAILED"
            assert call["created_from"] == datetime(2026, 8, 19, 0, 0)
            assert call["limit"] == 25
        finally:
            client.app.dependency_overrides.clear()

    def test_global_tasks_endpoint_rejects_unknown_status(self, client: TestClient) -> None:
        response = client.get("/api/v1/tasks?status=BROKEN", headers=AUTH)
        assert response.status_code == 422

    def test_global_tasks_endpoint_requires_token(self, client: TestClient) -> None:
        response = client.get("/api/v1/tasks")
        assert response.status_code == 401
