"""Server-side quality metrics: range validation, API wiring, trace filters."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import QueuePool

from app.api.dependencies import get_resources
from app.api.metrics import get_metrics_repository
from app.api.rag import get_rag_repository
from app.common.errors import ApiError
from app.rag.metrics import MetricsRepository


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
            "answer_mode_counts": {
                "PRODUCT_KNOWLEDGE": 30,
                "PRODUCT_EXPLAINED": 20,
                "PRODUCT_GENERAL": 10,
            },
            "answer_basis_traces": 60,
            "product_knowledge_rate": 0.5,
            "product_explained_rate": 0.3333,
            "product_general_rate": 0.1667,
            "classified_traces": 95,
            "in_scope_traces": 90,
            "direct_answer_rate": 0.7778,
            "needs_information_rate": 0.2222,
            "out_of_scope_rate": 0.0526,
            "doc_delivered_rate": 0.1053,
            "failure_rate": 0.05,
            "citation_missing_rate": 0.02,
            "rerank_degraded_rate": 0.01,
            "v2_quality": {
                "query_interpreter_total": 90,
                "query_interpreter_degraded": 3,
                "query_interpreter_degraded_rate": 0.0333,
                "document_rerank_attempted": 70,
                "document_rerank_degraded": 7,
                "document_rerank_degraded_rate": 0.1,
                "document_rerank_unconfigured": 0,
            },
            "unique_users": 42,
            "latency": {
                "p50_ms": 1200,
                "p95_ms": 3400,
                "p99_ms": 5100,
                "avg_ms": 1500,
                "samples": 118,
            },
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
                "direct_answer_rate": 0.8182,
                "needs_information_rate": 0.1818,
                "out_of_scope_rate": 0.0,
                "p95_ms": 3000,
                "llm_tokens": 900,
                "rerank_tokens": 40,
            }
        ]

    async def latency(self, **kwargs: Any) -> dict[str, Any]:
        self.calls["latency"] = kwargs
        return {
            "range": {"from": "2026-08-14T00:00:00", "to": "2026-08-21T00:00:00", "mode": "CHAT"},
            "total": {
                "p50_ms": 1200,
                "p95_ms": 3400,
                "p99_ms": 5100,
                "avg_ms": 1500,
                "samples": 118,
            },
            "stages": {"prepare_turn_ms": 40, "llm_ms": 900},
        }

    async def support(self, **kwargs: Any) -> dict[str, Any]:
        self.calls["support"] = kwargs
        return {
            "range": {"from": "2026-08-14T00:00:00", "to": "2026-08-21T00:00:00", "mode": "CHAT"},
            "support_turns": 12,
            "first_turns": 8,
            "followup_turns": 16,
            "document_delivered": 7,
            "clarification_required": 3,
            "clarification_resolved": 2,
            "no_match": 1,
            "escalation_required": 1,
            "canonical_or_alias_matches": 10,
            "llm_catalog_matches": 2,
            "general_guidance_turns": 2,
            "evidence_missing": 0,
            "high_risk_intercepts": 1,
            "structured_output_retries": 0,
            "workflow_conflicts": 0,
            "model_calls": 30,
            "document_delivery_rate": 0.5833,
            "clarification_rate": 0.25,
            "clarification_resolution_rate": 0.6667,
            "escalation_rate": 0.0833,
            "general_guidance_rate": 0.1667,
            "workflow_conflict_rate": 0.0,
            "first_turn_latency": {
                "p50_ms": 100,
                "p95_ms": 200,
                "p99_ms": 300,
                "avg_ms": 120,
                "samples": 6,
            },
            "followup_latency": {
                "p50_ms": 400,
                "p95_ms": 500,
                "p99_ms": 600,
                "avg_ms": 450,
                "samples": 4,
            },
            "tokens": {"llm_prompt": 1000, "llm_completion": 200, "llm_total": 1200},
        }

    async def assistant(self, **kwargs: Any) -> dict[str, Any]:
        self.calls["assistant"] = kwargs
        return {
            "days": 7,
            "sources": [
                {
                    "source": "ASSISTANT_API",
                    "turns": 40,
                    "failed": 1,
                    "error_rate": 0.025,
                    "conversations": 6,
                    "users": 3,
                    "latency": {
                        "p50_ms": 2100,
                        "p95_ms": 8200,
                        "p99_ms": 9100,
                        "avg_ms": 3200,
                        "samples": 40,
                    },
                },
                {
                    "source": "INTERNAL_CHAT",
                    "turns": 100,
                    "failed": 2,
                    "error_rate": 0.02,
                    "conversations": 20,
                    "users": 1,
                    "latency": {
                        "p50_ms": 1800,
                        "p95_ms": 7000,
                        "p99_ms": 8800,
                        "avg_ms": 3000,
                        "samples": 20,
                    },
                },
            ],
            "top_users": [
                {"user_id": "crew-01", "turns": 18, "failed": 0, "last_seen": "2026-08-21T09:00:00"}
            ],
            "turn_error_codes": [{"code": "UPSTREAM_UNAVAILABLE", "count": 1}],
            "http_errors": [{"status_code": 503, "error_code": "UPSTREAM_UNAVAILABLE", "count": 1}],
            "idempotency": {
                "assistant_protected": 35,
                "assistant_replays": 2,
                "ingestion_operations": {"upload_document:test-kb": 3},
            },
            "retention": {"retention_days": 7, "total": 6, "active": 5, "expired": 1},
            "concurrency": {
                "running_requests": 0,
                "oldest_running_age_seconds": 0,
                "stale_running_requests": 0,
                "busy_rejections": {"CONVERSATION_BUSY": 2},
            },
        }


class _StubTraceRepository:
    def __init__(self) -> None:
        self.list_calls: dict[str, Any] = {}
        self.rows: list[dict[str, Any]] = [
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

    async def list_traces(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.list_calls = kwargs
        return self.rows


AUTH = {"Authorization": "Bearer test-token"}


class _RepositoryResult:
    def __init__(self, *, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []

    def mappings(self) -> _RepositoryResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self.rows

    def one(self) -> dict[str, Any]:
        return self.rows[0]


class _MetricsConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> _MetricsConnection:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def execute(self, statement: Any, parameters: dict[str, Any]) -> _RepositoryResult:
        sql = str(statement)
        self.calls.append((sql, parameters))
        if "FROM assistant_request" in sql:
            if "status = 'RUNNING'" in sql:
                return _RepositoryResult(
                    rows=[{"count": 1, "oldest_age_seconds": 120, "stale_count": 0}]
                )
            return _RepositoryResult(rows=[{"protected": 3, "replays": 2}])
        if "FROM conversation c" in sql:
            return _RepositoryResult(rows=[{"total": 0, "active": 0, "expired": 0}])
        if "CONVERSATION_BUSY" in sql:
            return _RepositoryResult(rows=[{"error_code": "CONVERSATION_BUSY", "count": 2}])
        return _RepositoryResult()


class _MetricsEngine:
    def __init__(self) -> None:
        self.connection = _MetricsConnection()

    def connect(self) -> _MetricsConnection:
        return self.connection


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


@pytest.mark.asyncio
async def test_assistant_metrics_read_assistant_request_with_knowledge_filter() -> None:
    engine = _MetricsEngine()
    repository = MetricsRepository(cast(AsyncEngine, engine))
    knowledge_id = uuid4()

    result = await repository.assistant(knowledge_id=knowledge_id)

    assert result["idempotency"] == {
        "assistant_protected": 3,
        "assistant_replays": 2,
        "ingestion_operations": {},
    }
    idempotency_sql, parameters = next(
        call for call in engine.connection.calls if "FROM assistant_request" in call[0]
    )
    assert "FROM support_idempotency" not in idempotency_sql
    assert "knowledge_id = :kb_id" in idempotency_sql
    assert parameters["kb_id"] == knowledge_id
    turn_error_sql, _ = next(
        call
        for call in engine.connection.calls
        if "FROM rag_trace" in call[0] and "error ->> 'code' IS NOT NULL" in call[0]
    )
    assert "source = 'ASSISTANT_API'" in turn_error_sql
    http_error_sql, _ = next(
        call
        for call in engine.connection.calls
        if "FROM request_error_log" in call[0] and "GROUP BY 1, 2" in call[0]
    )
    assert "path LIKE '/api/v1/assistant%'" in http_error_sql
    assert "/api/v1/chat" not in http_error_sql


@pytest.mark.asyncio
async def test_assistant_metrics_include_running_backlog_and_busy_rejections() -> None:
    engine = _MetricsEngine()
    repository = MetricsRepository(cast(AsyncEngine, engine))

    result = await repository.assistant(stale_seconds=600)

    assert result["concurrency"] == {
        "running_requests": 1,
        "oldest_running_age_seconds": 120,
        "stale_running_requests": 0,
        "busy_rejections": {"CONVERSATION_BUSY": 2},
    }
    running_sql, running_params = next(
        call for call in engine.connection.calls if "status = 'RUNNING'" in call[0]
    )
    assert "FROM assistant_request" in running_sql
    assert "make_interval(secs => :stale_seconds)" in running_sql
    assert running_params["stale_seconds"] == 600
    busy_sql, busy_params = next(
        call for call in engine.connection.calls if "CONVERSATION_BUSY" in call[0]
    )
    assert "FROM request_error_log" in busy_sql
    assert "SERVICE_BUSY" in busy_sql
    assert "REQUEST_IN_PROGRESS" in busy_sql
    assert busy_params["start"] < busy_params["end"]


@pytest.mark.asyncio
async def test_assistant_metrics_reject_negative_stale_window() -> None:
    repository = MetricsRepository(cast(AsyncEngine, _MetricsEngine()))
    with pytest.raises(ApiError) as error:
        await repository.assistant(stale_seconds=-1)
    assert error.value.status_code == 422


class TestMetricsApi:
    def test_overview_endpoint_wires_filters(self, client: TestClient) -> None:
        stub = _StubMetricsRepository()
        client.app.dependency_overrides[get_metrics_repository] = lambda: stub
        try:
            response = client.get(
                "/api/v1/metrics/overview"
                "?knowledge_id=00000000-0000-0000-0000-000000000002"
                "&from=2026-08-14T00:00:00&to=2026-08-21T00:00:00"
                "&engine=RAG&source=ASSISTANT_API",
                headers=AUTH,
            )
            assert response.status_code == 200
            body = response.json()
            assert body["direct_answer_rate"] == 0.7778
            assert body["answer_mode_counts"]["PRODUCT_KNOWLEDGE"] == 30
            assert body["product_general_rate"] == 0.1667
            assert body["tokens"]["llm_total"] == 10000
            call = stub.calls["overview"]
            assert str(call["knowledge_id"]) == "00000000-0000-0000-0000-000000000002"
            assert call["created_from"] == datetime(2026, 8, 14, 0, 0)
            assert call["engine"] == "RAG"
            assert call["source"] == "ASSISTANT_API"
        finally:
            client.app.dependency_overrides.clear()

    def test_overview_requires_token(self, client: TestClient) -> None:
        response = client.get("/api/v1/metrics/overview")
        assert response.status_code == 401

    def test_assistant_endpoint_wires_filters(self, client: TestClient) -> None:
        stub = _StubMetricsRepository()
        client.app.dependency_overrides[get_metrics_repository] = lambda: stub
        try:
            response = client.get(
                "/api/v1/metrics/assistant"
                "?days=14&knowledge_id=00000000-0000-0000-0000-000000000002",
                headers=AUTH,
            )
            assert response.status_code == 200
            body = response.json()
            api_source = next(s for s in body["sources"] if s["source"] == "ASSISTANT_API")
            assert api_source["latency"]["p95_ms"] == 8200
            assert body["idempotency"]["assistant_replays"] == 2
            assert body["retention"]["expired"] == 1
            call = stub.calls["assistant"]
            assert call["days"] == 14
            assert str(call["knowledge_id"]) == "00000000-0000-0000-0000-000000000002"
            assert call["retention_days"] > 0
            assert call["stale_seconds"] > 0
            assert body["concurrency"]["busy_rejections"] == {"CONVERSATION_BUSY": 2}
        finally:
            client.app.dependency_overrides.clear()

    def test_assistant_rejects_out_of_range_days_with_422(self, client: TestClient) -> None:
        response = client.get("/api/v1/metrics/assistant?days=0", headers=AUTH)
        assert response.status_code == 422

    def test_assistant_requires_token(self, client: TestClient) -> None:
        response = client.get("/api/v1/metrics/assistant")
        assert response.status_code == 401

    def test_runtime_endpoint_reports_gate_and_pool(self, client: TestClient) -> None:
        class _Pool(QueuePool):
            """Counter-only fake: the endpoint reads pool statistics only."""

            def __init__(self) -> None:
                pass

            def size(self) -> int:
                return 5

            def checkedin(self) -> int:
                return 3

            def checkedout(self) -> int:
                return 2

            def overflow(self) -> int:
                return 0

        class _Resources:
            def __init__(self) -> None:
                self.database = SimpleNamespace(pool=_Pool())

        client.app.dependency_overrides[get_resources] = lambda: _Resources()
        try:
            response = client.get("/api/v1/metrics/runtime", headers=AUTH)
        finally:
            client.app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert body["turn_gate"]["limit"] > 0
        assert body["turn_gate"]["in_flight"] == 0
        assert body["database_pool"] == {
            "size": 5,
            "checked_in": 3,
            "checked_out": 2,
            "overflow": 0,
        }

    def test_runtime_requires_token(self, client: TestClient) -> None:
        response = client.get("/api/v1/metrics/runtime")
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
            assert body[0]["needs_information_rate"] == 0.1818
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

    def test_support_endpoint_wires_filters(self, client: TestClient) -> None:
        stub = _StubMetricsRepository()
        client.app.dependency_overrides[get_metrics_repository] = lambda: stub
        try:
            response = client.get(
                "/api/v1/metrics/support?knowledge_id=00000000-0000-0000-0000-000000000002"
                "&from=2026-08-14T00:00:00&to=2026-08-21T00:00:00",
                headers=AUTH,
            )
            assert response.status_code == 200
            body = response.json()
            assert body["clarification_resolution_rate"] == 0.6667
            assert body["followup_latency"]["p95_ms"] == 500
            call = stub.calls["support"]
            assert str(call["knowledge_id"]) == "00000000-0000-0000-0000-000000000002"
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

    def test_traces_endpoint_accepts_support_response_types(self, client: TestClient) -> None:
        stub = _StubTraceRepository()
        client.app.dependency_overrides[get_rag_repository] = lambda: stub
        try:
            response = client.get("/api/v1/traces?response_type=DOCUMENT_DELIVERED", headers=AUTH)
            assert response.status_code == 200
            assert stub.list_calls["response_type"] == "DOCUMENT_DELIVERED"
        finally:
            client.app.dependency_overrides.clear()

    def test_traces_endpoint_passes_error_code_and_conversation_filters(
        self, client: TestClient
    ) -> None:
        stub = _StubTraceRepository()
        client.app.dependency_overrides[get_rag_repository] = lambda: stub
        try:
            response = client.get(
                "/api/v1/traces?status=FAILED&error_code=INVALID_EVIDENCE"
                "&conversation_id=00000000-0000-0000-0000-000000000009",
                headers=AUTH,
            )
            assert response.status_code == 200
            call = stub.list_calls
            assert call["status"] == "FAILED"
            assert call["error_code"] == "INVALID_EVIDENCE"
            assert str(call["conversation_id"]) == "00000000-0000-0000-0000-000000000009"
        finally:
            client.app.dependency_overrides.clear()

    def test_trace_response_models_accept_need_more_information(self, client: TestClient) -> None:
        # regression: 0018 introduced NEED_MORE_INFORMATION but the response
        # Literals missed it, so reading such a trace failed with a 500.
        stub = _StubTraceRepository()
        stub.rows = [dict(stub.rows[0], response_type="NEED_MORE_INFORMATION", status="COMPLETED")]
        client.app.dependency_overrides[get_rag_repository] = lambda: stub
        try:
            response = client.get("/api/v1/traces", headers=AUTH)
            assert response.status_code == 200
            assert response.json()[0]["response_type"] == "NEED_MORE_INFORMATION"
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
        "technical_total": 10,
        "technical_completed": 9,
        "answered": 5,
        "unconfirmed": 3,
        "out_of_scope": 1,
        "doc_delivered": 2,
        "product_knowledge": 2,
        "product_explained": 2,
        "product_general": 1,
        "unique_users": 4,
        "citation_missing": 2,
        "rerank_degraded": 1,
        "v2_query_total": 8,
        "v2_query_degraded": 2,
        "v2_rerank_attempted": 5,
        "v2_rerank_degraded": 1,
        "v2_rerank_unconfigured": 3,
        "p50_ms": 100,
        "p95_ms": 200,
        "p99_ms": 300,
        "avg_ms": 120,
        "latency_samples": 8,
        "llm_prompt_tokens": 1000,
        "llm_completion_tokens": 200,
        "rerank_tokens": 50,
    }
    row.update(overrides)
    return row


def _support_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "support_turns": 10,
        "first_turns": 6,
        "followup_turns": 4,
        "document_delivered": 5,
        "clarification_required": 2,
        "clarification_resolved": 1,
        "no_match": 1,
        "escalation_required": 1,
        "canonical_or_alias_matches": 3,
        "llm_catalog_matches": 1,
        "general_guidance_turns": 2,
        "evidence_missing": 1,
        "high_risk_intercepts": 1,
        "structured_output_retries": 1,
        "workflow_conflicts": 1,
        "model_calls": 4,
        "first_p50_ms": 100,
        "first_p95_ms": 200,
        "first_p99_ms": 300,
        "first_avg_ms": 120,
        "followup_p50_ms": 400,
        "followup_p95_ms": 500,
        "followup_p99_ms": 600,
        "followup_avg_ms": 450,
        "llm_prompt_tokens": 1000,
        "llm_completion_tokens": 200,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
class TestMetricsAggregation:
    async def test_overview_derives_rates_from_row(self) -> None:
        engine = _FakeEngine([_overview_row()])
        result = await MetricsRepository(engine).overview()  # type: ignore[arg-type]

        assert result["total_traces"] == 10
        assert result["classified_traces"] == 11
        assert result["in_scope_traces"] == 10
        assert result["direct_answer_rate"] == 0.7
        assert result["needs_information_rate"] == 0.3
        assert result["out_of_scope_rate"] == round(1 / 11, 4)
        assert result["doc_delivered_rate"] == round(2 / 11, 4)
        assert result["answer_mode_counts"] == {
            "PRODUCT_KNOWLEDGE": 2,
            "PRODUCT_EXPLAINED": 2,
            "PRODUCT_GENERAL": 1,
        }
        assert result["answer_basis_traces"] == 5
        assert result["product_knowledge_rate"] == 0.4
        assert result["product_explained_rate"] == 0.4
        assert result["product_general_rate"] == 0.2
        assert result["failure_rate"] == 0.1
        assert result["citation_missing_rate"] == round(2 / 9, 4)
        assert result["rerank_degraded_rate"] == 0.1
        assert result["v2_quality"] == {
            "query_interpreter_total": 8,
            "query_interpreter_degraded": 2,
            "query_interpreter_degraded_rate": 0.25,
            "document_rerank_attempted": 5,
            "document_rerank_degraded": 1,
            "document_rerank_degraded_rate": 0.2,
            "document_rerank_unconfigured": 3,
        }
        assert result["tokens"]["llm_total"] == 1200
        assert result["tokens"]["rerank_total"] == 50
        assert result["latency"] == {
            "p50_ms": 100,
            "p95_ms": 200,
            "p99_ms": 300,
            "avg_ms": 120,
            "samples": 8,
        }

    async def test_overview_zero_denominator_rates_are_zero(self) -> None:
        row = _overview_row(
            total=0,
            completed=0,
            answered=0,
            unconfirmed=0,
            out_of_scope=0,
            doc_delivered=0,
            product_knowledge=0,
            product_explained=0,
            product_general=0,
            failed=0,
            technical_total=0,
            technical_completed=0,
            citation_missing=0,
            rerank_degraded=0,
            v2_query_total=0,
            v2_query_degraded=0,
            v2_rerank_attempted=0,
            v2_rerank_degraded=0,
            v2_rerank_unconfigured=0,
        )
        result = await MetricsRepository(_FakeEngine([row])).overview()  # type: ignore[arg-type]
        assert result["direct_answer_rate"] == 0.0
        assert result["needs_information_rate"] == 0.0
        assert result["out_of_scope_rate"] == 0.0
        assert result["product_knowledge_rate"] == 0.0
        assert result["product_explained_rate"] == 0.0
        assert result["product_general_rate"] == 0.0
        assert result["failure_rate"] == 0.0
        assert result["v2_quality"]["query_interpreter_degraded_rate"] == 0.0
        assert result["v2_quality"]["document_rerank_degraded_rate"] == 0.0

    async def test_overview_applies_filters_to_query(self) -> None:
        engine = _FakeEngine([_overview_row()])
        await MetricsRepository(engine).overview(  # type: ignore[arg-type]
            knowledge_id="kb-1",
            mode="CHAT",
            engine="RAG",
            source="ASSISTANT_API",
        )
        sql, params = engine.connection.queries[0]
        assert "kb_id = :kb_id" in sql
        assert "mode = :mode" in sql
        assert "engine = :engine" in sql
        assert "source = :source" in sql
        assert "COALESCE(citation_result ->> 'shortcut', '') <> 'GREETING'" not in sql
        assert "NOT IN ('GREETING', 'META_ACK')" in sql
        assert "citation_result ->> 'shortcut' IS NULL" in sql
        assert "citation_result ->> 'response_path'" in sql
        assert "<> 'CONVERSATION'" in sql
        assert params["kb_id"] == "kb-1"
        assert params["mode"] == "CHAT"
        assert params["engine"] == "RAG"
        assert params["source"] == "ASSISTANT_API"

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
        engine = _FakeEngine(rows)
        result = await MetricsRepository(engine).timeseries()  # type: ignore[arg-type]
        assert len(result) == 1
        point = result[0]
        assert point["bucket"] == "2026-08-20T00:00:00+00:00"
        assert point["direct_answer_rate"] == round(6 / 8, 4)
        assert point["needs_information_rate"] == round(2 / 8, 4)
        assert point["out_of_scope_rate"] == round(1 / 9, 4)
        assert point["queries"] == 10
        assert point["p95_ms"] == 900
        sql, _ = engine.connection.queries[0]
        assert "citation_result ->> 'shortcut' IS NULL" in sql
        assert "citation_result ->> 'response_path'" in sql
        assert "<> 'CONVERSATION'" in sql
        assert "NOT IN ('GREETING', 'META_ACK')" in sql

    async def test_latency_maps_stages(self) -> None:
        row: dict[str, Any] = {
            "p50_ms": 100,
            "p95_ms": 200,
            "p99_ms": 300,
            "avg_ms": 120,
            "latency_samples": 7,
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
        assert result["total"] == {
            "p50_ms": 100,
            "p95_ms": 200,
            "p99_ms": 300,
            "avg_ms": 120,
            "samples": 7,
        }
        assert result["stages"]["llm_ms"] == 500
        assert result["stages"]["rerank_ms"] == 10
        assert len(result["stages"]) == 10

    async def test_support_derives_support_rates_and_filters(self) -> None:
        engine = _FakeEngine([_support_row()])
        result = await MetricsRepository(engine).support(knowledge_id="kb-1")  # type: ignore[arg-type]

        assert result["document_delivery_rate"] == 0.5
        assert result["clarification_resolution_rate"] == 0.5
        assert result["workflow_conflict_rate"] == 0.1
        assert result["followup_latency"]["p95_ms"] == 500
        assert result["tokens"]["llm_total"] == 1200
        sql, params = engine.connection.queries[0]
        assert "support_version" in sql
        assert params["kb_id"] == "kb-1"
