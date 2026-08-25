"""Unit and API tests for the persisted failure telemetry (request_error_log)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.logs import get_observability_repository
from app.common.errors import ApiError
from app.observability.recorder import record_request_error, record_unhandled_exception
from app.observability.repository import ObservabilityRepository

AUTH = {"Authorization": "Bearer test-token"}


class _FakeResult:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def mappings(self) -> _FakeResult:
        return self

    def __iter__(self) -> Any:
        return iter(self.all())

    def all(self) -> list[Any]:
        return self._payload if isinstance(self._payload, list) else []

    def one_or_none(self) -> Any:
        rows = self.all()
        return rows[0] if rows else None

    def scalar_one(self) -> Any:
        return self._payload

    @property
    def rowcount(self) -> int:
        return self._payload if isinstance(self._payload, int) else len(self.all())


class _FakeConnection:
    def __init__(self, results: list[Any]) -> None:
        self.results = list(results)
        self.executed: list[tuple[str, dict[str, Any] | None]] = []

    async def __aenter__(self) -> _FakeConnection:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _FakeResult:
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


def _repository(results: list[Any]) -> tuple[ObservabilityRepository, _FakeConnection]:
    engine = _FakeEngine(results)
    repo = ObservabilityRepository(cast(AsyncEngine, engine))
    return repo, engine.connection


class TestObservabilityRepository:
    async def test_insert_truncates_identifiers_and_casts_detail(self) -> None:
        repo, connection = _repository([])
        long_id = "r" * 200

        await repo.insert_request_error(
            request_id=long_id,
            method="GET",
            path="/api/v1/support/chat",
            status_code=502,
            error_code="UPSTREAM_UNAVAILABLE" + "x" * 120,
            message="上游不可用",
            detail={"exception_type": "LlmError"},
        )

        sql, params = connection.executed[0]
        assert "INSERT INTO request_error_log" in sql
        assert "CAST(:detail AS jsonb)" in sql
        assert params is not None
        assert len(params["request_id"]) == 128
        assert params["error_code"] == "UPSTREAM_UNAVAILABLE" + "x" * 80
        assert json.loads(str(params["detail"])) == {"exception_type": "LlmError"}
        assert params["status_code"] == 502

    async def test_list_request_errors_applies_filters_and_pagination(self) -> None:
        row = {
            "id": str(uuid4()),
            "request_id": "req-1",
            "method": "POST",
            "path": "/api/v1/support/chat/stream",
            "status_code": 500,
            "error_code": "INTERNAL_ERROR",
            "message": "服务发生内部错误",
            "created_at": "2026-08-20T10:00:00",
        }
        repo, connection = _repository([7, [row]])

        result = await repo.list_request_errors(
            request_id="req-1",
            path="/support",
            status_code=500,
            created_from=datetime(2026, 8, 19, tzinfo=UTC),
            created_to=datetime(2026, 8, 21, tzinfo=UTC),
            limit=10,
            offset=20,
        )

        count_sql, _ = connection.executed[0]
        list_sql, params = connection.executed[1]
        assert "SELECT count(*)" in count_sql
        for fragment in (
            "request_id = :request_id",
            "path ILIKE :path",
            "status_code = :status_code",
            "created_at >= :created_from",
            "created_at < :created_to",
        ):
            assert fragment in list_sql
        assert params is not None
        assert params["path"] == "%/support%"
        assert params["limit"] == 10
        assert params["offset"] == 20
        assert result == {"items": [row], "total": 7, "limit": 10, "offset": 20}

    async def test_get_request_error_not_found(self) -> None:
        repo, _ = _repository([[]])
        with pytest.raises(ApiError) as error:
            await repo.get_request_error(uuid4())
        assert error.value.status_code == 404
        assert error.value.code == "REQUEST_ERROR_LOG_NOT_FOUND"

    async def test_get_request_error_returns_full_row(self) -> None:
        error_id = uuid4()
        row = {"id": str(error_id), "request_id": "req-1", "detail": {"stack": "..."}}
        repo, _ = _repository([[row]])
        assert await repo.get_request_error(error_id) == row

    async def test_failure_summary_groups_failures(self) -> None:
        start = datetime.now(UTC) - timedelta(days=1)
        end = datetime.now(UTC)
        repo, connection = _repository(
            [
                [{"error_code": "INTERNAL_ERROR", "count": 3, "last_seen": end}],
                [
                    {
                        "path": "/api/v1/support/chat",
                        "status_code": 502,
                        "count": 2,
                        "last_seen": end,
                    }
                ],
                [{"day": "2026-08-20", "count": 2}],
            ]
        )

        summary = await repo.failure_summary(
            created_from=start, created_to=end, knowledge_id=uuid4()
        )

        trace_sql, params = connection.executed[0]
        assert "status = 'FAILED'" in trace_sql
        assert "kb_id = :kb_id" in trace_sql
        assert params is not None and "kb_id" in params
        assert "GROUP BY 1" in trace_sql
        request_sql, _ = connection.executed[1]
        assert "GROUP BY path, status_code" in request_sql
        day_sql, _ = connection.executed[2]
        assert "date_trunc('day'" in day_sql
        assert summary["trace_failures"][0]["error_code"] == "INTERNAL_ERROR"
        assert summary["request_errors"][0]["path"] == "/api/v1/support/chat"
        assert summary["request_errors_by_day"] == [{"day": "2026-08-20", "count": 2}]

    async def test_failure_summary_rejects_invalid_range(self) -> None:
        repo, _ = _repository([])
        now = datetime.now(UTC)
        with pytest.raises(ApiError, match="时间范围"):
            await repo.failure_summary(created_from=now, created_to=now)

    async def test_delete_expired_uses_retention_interval(self) -> None:
        repo, connection = _repository([5])
        deleted = await repo.delete_expired(retention_days=30)
        sql, params = connection.executed[0]
        assert "DELETE FROM request_error_log" in sql
        assert "make_interval(days => :days)" in sql
        assert params == {"days": 30}
        assert deleted == 5


class _FakeRequestState:
    request_id = "req-test"


class _FakeURL:
    def __init__(self, path: str) -> None:
        self.path = path


class _FakeApp:
    def __init__(self, engine: Any) -> None:
        self.state = type("State", (), {"resources": type("R", (), {"database": engine})()})()


class _FakeRequest:
    def __init__(self, path: str, method: str = "GET", engine: Any = None) -> None:
        self.url = _FakeURL(path)
        self.method = method
        self.state = _FakeRequestState()
        self.app = _FakeApp(engine)


class TestRecorder:
    async def test_skips_health_paths(self) -> None:
        engine = _FakeEngine([])
        request = _FakeRequest("/health/ready", engine=engine)
        await record_request_error(
            request, status_code=503, error_code="NOT_READY", message="未就绪"
        )
        assert engine.connection.executed == []

    async def test_skips_when_resources_missing(self) -> None:
        request = _FakeRequest("/api/v1/support/chat")
        request.app = type("App", (), {"state": type("State", (), {})()})()
        # must not raise even though there is no database engine at all
        await record_request_error(
            request, status_code=500, error_code="INTERNAL_ERROR", message="x"
        )

    async def test_swallows_database_failures(self) -> None:
        engine = _FakeEngine([RuntimeError("connection refused")])
        request = _FakeRequest("/api/v1/support/chat", method="POST", engine=engine)
        await record_request_error(
            request, status_code=500, error_code="INTERNAL_ERROR", message="服务发生内部错误"
        )
        assert len(engine.connection.executed) == 1

    async def test_unhandled_exception_records_stack(self) -> None:
        engine = _FakeEngine([])
        request = _FakeRequest("/api/v1/support/chat", method="POST", engine=engine)
        try:
            raise ValueError("boom")
        except ValueError as exc:
            await record_unhandled_exception(request, exc)
        sql, params = engine.connection.executed[0]
        assert "INSERT INTO request_error_log" in sql
        detail = json.loads(str(params["detail"]))
        assert detail["exception_type"] == "ValueError"
        assert "ValueError: boom" in detail["stack"]


class TestErrorResponsesAndCapture:
    def test_404_uses_unified_error_body(self, client: TestClient) -> None:
        response = client.get("/api/v1/nonexistent", headers=AUTH)
        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "HTTP_404"
        assert body["error"]["request_id"]

    def test_api_error_response_still_includes_request_id(self, client: TestClient) -> None:
        response = client.get("/api/v1/system/info")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"

    def test_validation_error_is_captured(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        async def spy(self: ObservabilityRepository, **kwargs: Any) -> None:
            captured.update(kwargs)

        monkeypatch.setattr(ObservabilityRepository, "insert_request_error", spy)
        response = client.get("/api/v1/logs/request-errors?status_code=399", headers=AUTH)
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert captured["error_code"] == "VALIDATION_ERROR"
        assert captured["status_code"] == 422
        assert captured["path"] == "/api/v1/logs/request-errors"
        assert captured["request_id"]
        fields = captured["detail"]["fields"]
        assert fields and fields[0]["location"] == ["query", "status_code"]

    def test_recorder_failure_never_breaks_the_response(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def boom(self: ObservabilityRepository, **kwargs: Any) -> None:
            raise RuntimeError("db down")

        monkeypatch.setattr(ObservabilityRepository, "insert_request_error", boom)
        response = client.get("/api/v1/logs/request-errors?status_code=399", headers=AUTH)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


class _StubRepository:
    def __init__(self) -> None:
        self.calls: dict[str, Any] = {}

    async def list_request_errors(self, **kwargs: Any) -> dict[str, Any]:
        self.calls["list"] = kwargs
        return {
            "items": [
                {
                    "id": str(uuid4()),
                    "request_id": "req-1",
                    "method": "POST",
                    "path": "/api/v1/support/chat",
                    "status_code": 500,
                    "error_code": "INTERNAL_ERROR",
                    "message": "服务发生内部错误",
                    "created_at": "2026-08-20T10:00:00",
                }
            ],
            "total": 1,
            "limit": kwargs["limit"],
            "offset": kwargs["offset"],
        }

    async def get_request_error(self, error_id: Any) -> dict[str, Any]:
        raise ApiError("REQUEST_ERROR_LOG_NOT_FOUND", "请求错误日志不存在", 404)

    async def failure_summary(self, **kwargs: Any) -> dict[str, Any]:
        self.calls["summary"] = kwargs
        now = datetime.now(UTC)
        return {
            "from": now - timedelta(days=7),
            "to": now,
            "trace_failures": [{"error_code": "INTERNAL_ERROR", "count": 1, "last_seen": now}],
            "request_errors": [],
            "request_errors_by_day": [],
        }


class TestLogsApi:
    def test_request_errors_endpoint_wires_filters(self, client: TestClient) -> None:
        stub = _StubRepository()
        client.app.dependency_overrides[get_observability_repository] = lambda: stub
        try:
            response = client.get(
                "/api/v1/logs/request-errors"
                "?request_id=req-1&path=/support&status_code=500"
                "&from=2026-08-14T00:00:00&to=2026-08-21T00:00:00"
                "&limit=10&offset=20",
                headers=AUTH,
            )
            assert response.status_code == 200
            body = response.json()
            assert body["total"] == 1
            assert body["items"][0]["error_code"] == "INTERNAL_ERROR"
            call = stub.calls["list"]
            assert call["request_id"] == "req-1"
            assert call["path"] == "/support"
            assert call["status_code"] == 500
            assert call["created_from"] == datetime(2026, 8, 14, 0, 0)
            assert call["limit"] == 10
            assert call["offset"] == 20
        finally:
            client.app.dependency_overrides.clear()

    def test_request_error_detail_maps_404(self, client: TestClient) -> None:
        stub = _StubRepository()
        client.app.dependency_overrides[get_observability_repository] = lambda: stub
        try:
            response = client.get(f"/api/v1/logs/request-errors/{uuid4()}", headers=AUTH)
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "REQUEST_ERROR_LOG_NOT_FOUND"
        finally:
            client.app.dependency_overrides.clear()

    def test_failure_summary_endpoint_wires_range(self, client: TestClient) -> None:
        stub = _StubRepository()
        client.app.dependency_overrides[get_observability_repository] = lambda: stub
        try:
            response = client.get(
                "/api/v1/logs/failure-summary"
                "?knowledge_id=00000000-0000-0000-0000-000000000002"
                "&from=2026-08-14T00:00:00&to=2026-08-21T00:00:00",
                headers=AUTH,
            )
            assert response.status_code == 200
            body = response.json()
            assert body["trace_failures"][0]["error_code"] == "INTERNAL_ERROR"
            call = stub.calls["summary"]
            assert call["created_from"] == datetime(2026, 8, 14, 0, 0)
            assert str(call["knowledge_id"]) == "00000000-0000-0000-0000-000000000002"
        finally:
            client.app.dependency_overrides.clear()

    def test_logs_endpoints_require_token(self, client: TestClient) -> None:
        for path in ("/api/v1/logs/request-errors", "/api/v1/logs/failure-summary"):
            response = client.get(path)
            assert response.status_code == 401
