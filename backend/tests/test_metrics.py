"""Server-side quality metrics: range validation, API wiring, trace filters."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.metrics import get_metrics_repository
from app.api.rag import get_rag_repository
from app.rag.metrics import MetricsRepository

pytestmark = pytest.mark.asyncio


class _StubMetricsRepository:
    def __init__(self) -> None:
        self.calls: dict[str, dict[str, Any]] = {}

    async def overview(self, **kwargs: Any) -> dict[str, Any]:
        self.calls["overview"] = kwargs
        return {
            "range": {"from": "2026-08-14T00:00:00", "to": "2026-08-21T00:00:00", "mode": "CHAT"},
            "total_traces": 100,
            "completed": 95,
            "failed": 5,
            "cancelled": 0,
            "response_type_counts": {
                "ANSWERED": 60,
                "UNCONFIRMED": 20,
                "OUT_OF_SCOPE": 5,
                "DOC_DELIVERED": 10,
            },
            "classified_traces": 95,
            "no_answer_rate": 0.2632,
            "doc_delivered_rate": 0.1053,
            "failure_rate": 0.05,
            "citation_missing_rate": 0.02,
            "rerank_degraded_rate": 0.01,
            "unique_users": 42,
            "latency": {"p50_ms": 1200, "p95_ms": 3400, "p99_ms": 5100, "avg_ms": 1500},
            "tokens": {
                "llm_prompt": 8000,
                "llm_completion": 2000,
                "llm_total": 10000,
                "rerank_total": 500,
            },
        }

    async def timeseries(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls["timeseries"] = kwargs
        return [
            {
                "bucket": "2026-08-20T00:00:00",
                "queries": 12,
                "failed": 1,
                "answered": 8,
                "unconfirmed": 2,
                "out_of_scope": 0,
                "doc_delivered": 1,
                "no_answer_rate": 0.2,
                "p95_ms": 3000,
                "llm_tokens": 900,
                "rerank_tokens": 40,
            }
        ]

    async def latency(self, **kwargs: Any) -> dict[str, Any]:
        self.calls["latency"] = kwargs
        return {
            "range": {"from": "2026-08-14T00:00:00", "to": "2026-08-21T00:00:00", "mode": "CHAT"},
            "total": {"p50_ms": 1200, "p95_ms": 3400, "p99_ms": 5100, "avg_ms": 1500},
            "stages": {"prepare_turn_ms": 40, "llm_ms": 900},
        }


class _StubTraceRepository:
    def __init__(self) -> None:
        self.list_calls: dict[str, Any] = {}

    async def list_traces(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.list_calls = kwargs
        return [
            {
                "trace_id": "00000000-0000-0000-0000-000000000001",
                "request_id": "req-1",
                "mode": "CHAT",
                "kb_id": "00000000-0000-0000-0000-000000000002",
                "index_id": "00000000-0000-0000-0000-000000000003",
                "question": "端口是多少",
                "status": "COMPLETED",
                "user_id": "mobile-user-1",
                "response_type": "UNCONFIRMED",
                "answer_mode": "UNCONFIRMED",
                "latency": {"total_ms": 800},
                "error": {},
                "created_at": "2026-08-20T10:00:00",
                "finished_at": "2026-08-20T10:00:01",
            }
        ]


AUTH = {"Authorization": "Bearer test-token"}


class TestMetricsRangeValidation:
    def test_default_range_is_last_seven_days(self) -> None:
        start, end = MetricsRepository.resolve_range(None, None)
        assert timedelta(days=6) < end - start <= timedelta(days=7)
        # defaults must be timezone-aware (naive/aware mixing would TypeError)
        assert start.tzinfo is not None and end.tzinfo is not None

    def test_mixed_naive_and_aware_inputs_are_normalized(self) -> None:
        # from=naive (ISO string from a client) + to omitted (aware default)
        # previously raised TypeError -> HTTP 500.
        start, end = MetricsRepository.resolve_range(datetime.now() - timedelta(days=1), None)
        assert start.tzinfo is not None
        assert start < end

    def test_invalid_range_rejected(self) -> None:
        now = datetime.now(UTC)
        with pytest.raises(Exception) as error:
            MetricsRepository.resolve_range(now, now)
        assert "时间范围无效" in str(error.value)

    def test_range_capped_at_92_days(self) -> None:
        now = datetime.now(UTC)
        with pytest.raises(Exception) as error:
            MetricsRepository.resolve_range(now - timedelta(days=100), now)
        assert "92" in str(error.value)


class TestMetricsApi:
    def test_overview_endpoint_wires_filters(self, client: TestClient) -> None:
        stub = _StubMetricsRepository()
        client.app.dependency_overrides[get_metrics_repository] = lambda: stub
        try:
            response = client.get(
                "/api/v1/metrics/overview"
                "?knowledge_id=00000000-0000-0000-0000-000000000002"
                "&from=2026-08-14T00:00:00&to=2026-08-21T00:00:00",
                headers=AUTH,
            )
            assert response.status_code == 200
            body = response.json()
            assert body["no_answer_rate"] == 0.2632
            assert body["tokens"]["llm_total"] == 10000
            call = stub.calls["overview"]
            assert str(call["knowledge_id"]) == "00000000-0000-0000-0000-000000000002"
            assert call["created_from"] == datetime(2026, 8, 14, 0, 0)
        finally:
            client.app.dependency_overrides.clear()

    def test_overview_requires_token(self, client: TestClient) -> None:
        response = client.get("/api/v1/metrics/overview")
        assert response.status_code == 401

    def test_overview_rejects_invalid_iso_with_422(self, client: TestClient) -> None:
        response = client.get("/api/v1/metrics/overview?from=not-a-date", headers=AUTH)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_overview_accepts_naive_from_with_default_to(self, client: TestClient) -> None:
        # regression: naive from + omitted to previously raised TypeError -> 500
        stub = _StubMetricsRepository()
        client.app.dependency_overrides[get_metrics_repository] = lambda: stub
        try:
            response = client.get("/api/v1/metrics/overview?from=2026-08-20T00:00:00", headers=AUTH)
            assert response.status_code == 200
            assert stub.calls["overview"]["created_from"] == datetime(2026, 8, 20, 0, 0)
        finally:
            client.app.dependency_overrides.clear()

    def test_timeseries_endpoint(self, client: TestClient) -> None:
        stub = _StubMetricsRepository()
        client.app.dependency_overrides[get_metrics_repository] = lambda: stub
        try:
            response = client.get("/api/v1/metrics/timeseries?granularity=hour", headers=AUTH)
            assert response.status_code == 200
            body = response.json()
            assert body[0]["bucket"] == "2026-08-20T00:00:00"
            assert body[0]["no_answer_rate"] == 0.2
            assert stub.calls["timeseries"]["granularity"] == "hour"
        finally:
            client.app.dependency_overrides.clear()

    def test_latency_endpoint(self, client: TestClient) -> None:
        stub = _StubMetricsRepository()
        client.app.dependency_overrides[get_metrics_repository] = lambda: stub
        try:
            response = client.get("/api/v1/metrics/latency", headers=AUTH)
            assert response.status_code == 200
            body = response.json()
            assert body["total"]["p95_ms"] == 3400
            assert body["stages"]["llm_ms"] == 900
        finally:
            client.app.dependency_overrides.clear()


class TestTraceFilters:
    def test_traces_endpoint_passes_new_filters(self, client: TestClient) -> None:
        stub = _StubTraceRepository()
        client.app.dependency_overrides[get_rag_repository] = lambda: stub
        try:
            response = client.get(
                "/api/v1/traces?response_type=UNCONFIRMED&user_id=mobile-user-1"
                "&from=2026-08-20T00:00:00&to=2026-08-21T00:00:00",
                headers=AUTH,
            )
            assert response.status_code == 200
            body = response.json()
            assert body[0]["response_type"] == "UNCONFIRMED"
            assert body[0]["user_id"] == "mobile-user-1"
            call = stub.list_calls
            assert call["response_type"] == "UNCONFIRMED"
            assert call["user_id"] == "mobile-user-1"
            assert call["created_from"] == datetime(2026, 8, 20, 0, 0)
            assert call["created_to"] == datetime(2026, 8, 21, 0, 0)
        finally:
            client.app.dependency_overrides.clear()


class _FakeMappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def one(self) -> dict[str, Any]:
        return self._rows[0]

    def __iter__(self) -> Any:
        return iter(self._rows)


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _FakeMappings:
        return _FakeMappings(self._rows)

    def __iter__(self) -> Any:
        return iter(self._rows)


class _FakeConnection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.queries: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, query: Any, parameters: dict[str, Any] | None = None) -> _FakeResult:
        self.queries.append((str(query), dict(parameters or {})))
        return _FakeResult(self.rows)


class _FakeEngine:
    """Minimal async engine stand-in: returns canned rows for every query."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.connection = _FakeConnection(rows)

    def connect(self) -> Any:
        @asynccontextmanager
        async def _ctx() -> Any:
            yield self.connection

        return _ctx()


def _overview_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "total": 10,
        "completed": 9,
        "failed": 1,
        "cancelled": 0,
        "answered": 5,
        "unconfirmed": 3,
        "out_of_scope": 1,
        "doc_delivered": 2,
        "with_response_type": 11,
        "unique_users": 4,
        "citation_missing": 2,
        "rerank_degraded": 1,
        "p50_ms": 100,
        "p95_ms": 200,
        "p99_ms": 300,
        "avg_ms": 120,
        "llm_prompt_tokens": 1000,
        "llm_completion_tokens": 200,
        "rerank_tokens": 50,
    }
    row.update(overrides)
    return row


class TestMetricsAggregation:
    async def test_overview_derives_rates_from_row(self) -> None:
        engine = _FakeEngine([_overview_row()])
        result = await MetricsRepository(engine).overview()  # type: ignore[arg-type]

        assert result["total_traces"] == 10
        assert result["classified_traces"] == 11
        # (unconfirmed 3 + out_of_scope 1) / classified 11
        assert result["no_answer_rate"] == round(4 / 11, 4)
        assert result["doc_delivered_rate"] == round(2 / 11, 4)
        assert result["failure_rate"] == 0.1
        assert result["citation_missing_rate"] == round(2 / 9, 4)
        assert result["rerank_degraded_rate"] == 0.1
        assert result["tokens"]["llm_total"] == 1200
        assert result["tokens"]["rerank_total"] == 50
        assert result["latency"] == {"p50_ms": 100, "p95_ms": 200, "p99_ms": 300, "avg_ms": 120}

    async def test_overview_zero_denominator_rates_are_zero(self) -> None:
        row = _overview_row(
            total=0,
            completed=0,
            with_response_type=0,
            unconfirmed=0,
            out_of_scope=0,
            doc_delivered=0,
            failed=0,
            citation_missing=0,
            rerank_degraded=0,
        )
        result = await MetricsRepository(_FakeEngine([row])).overview()  # type: ignore[arg-type]
        assert result["no_answer_rate"] == 0.0
        assert result["failure_rate"] == 0.0

    async def test_overview_applies_filters_to_query(self) -> None:
        engine = _FakeEngine([_overview_row()])
        await MetricsRepository(engine).overview(  # type: ignore[arg-type]
            knowledge_id="kb-1", mode="PLAYGROUND"
        )
        sql, params = engine.connection.queries[0]
        assert "kb_id = :kb_id" in sql
        assert "mode = :mode" in sql
        assert params["kb_id"] == "kb-1"
        assert params["mode"] == "PLAYGROUND"

    async def test_timeseries_builds_points_and_rates(self) -> None:
        rows = [
            {
                "bucket": datetime(2026, 8, 20, tzinfo=UTC),
                "queries": 10,
                "failed": 1,
                "answered": 5,
                "unconfirmed": 2,
                "out_of_scope": 1,
                "doc_delivered": 1,
                "p95_ms": 900,
                "llm_tokens": 400,
                "rerank_tokens": 20,
            }
        ]
        result = await MetricsRepository(_FakeEngine(rows)).timeseries()  # type: ignore[arg-type]
        assert len(result) == 1
        point = result[0]
        assert point["bucket"] == "2026-08-20T00:00:00+00:00"
        assert point["no_answer_rate"] == round(3 / 9, 4)
        assert point["queries"] == 10
        assert point["p95_ms"] == 900

    async def test_latency_maps_stages(self) -> None:
        row: dict[str, Any] = {
            "p50_ms": 100,
            "p95_ms": 200,
            "p99_ms": 300,
            "avg_ms": 120,
        }
        for stage in (
            "prepare_turn_ms",
            "query_rewrite_ms",
            "query_embedding_ms",
            "bm25_retrieval_ms",
            "vector_retrieval_ms",
            "fusion_ms",
            "rerank_ms",
            "context_expansion_ms",
            "prompt_ms",
            "llm_ms",
        ):
            row[stage] = 10
        row["llm_ms"] = 500
        result = await MetricsRepository(_FakeEngine([row])).latency()  # type: ignore[arg-type]
        assert result["total"] == {"p50_ms": 100, "p95_ms": 200, "p99_ms": 300, "avg_ms": 120}
        assert result["stages"]["llm_ms"] == 500
        assert result["stages"]["rerank_ms"] == 10
        assert len(result["stages"]) == 10
