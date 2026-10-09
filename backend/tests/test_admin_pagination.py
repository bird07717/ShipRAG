"""Server-side paging of admin lists (traces, tasks, documents, conversations).

List bodies stay unchanged arrays for existing callers; the total number of
matching rows is returned in ``X-Total-Count`` (conversations: ``total`` in
the existing envelope). Filters apply before LIMIT/OFFSET and to the count.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.engine_dependencies import get_gateway_repository, get_rag_repository
from app.api.ingestion import get_ingestion_repository
from app.api.pagination import paged_total
from app.common.errors import ApiError
from app.common.text import escape_like
from app.core.config import Settings
from app.ingestion.repository import IngestionRepository
from app.rag.repository import RagRepository

AUTH = {"Authorization": "Bearer test-token"}


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar: int = 0) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self) -> _Result:
        return self

    def __iter__(self) -> Any:
        return iter(self._rows)

    def one_or_none(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def scalar_one(self) -> int:
        return self._scalar


class _Connection:
    def __init__(self, results: list[_Result]) -> None:
        self.results = list(results)
        self.executed: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> _Connection:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        self.executed.append((str(statement), dict(params or {})))
        return self.results.pop(0) if self.results else _Result()


class _Engine:
    def __init__(self, results: list[_Result]) -> None:
        self.connection = _Connection(results)

    def begin(self) -> _Connection:
        return self.connection

    def connect(self) -> _Connection:
        return self.connection


def _settings() -> Settings:
    return Settings(_env_file=None, app_env="test")


async def test_paged_total_skips_the_count_only_for_a_complete_first_page() -> None:
    calls: list[int] = []

    async def count() -> int:
        calls.append(1)
        return 99

    assert await paged_total([1, 2], limit=5, offset=0, count=count) == 2
    assert await paged_total([1, 2], limit=None, offset=0, count=count) == 2
    assert calls == []
    assert await paged_total([1, 2], limit=2, offset=0, count=count) == 99
    assert await paged_total([], limit=5, offset=10, count=count) == 99
    assert await paged_total([1], limit=None, offset=3, count=count) == 99
    assert len(calls) == 3


def test_escape_like_matches_wildcards_literally() -> None:
    assert escape_like(r"50%_a\b") == r"50\%\_a\\b"


# --------------------------------------------------------------------------- repositories


async def test_trace_page_and_count_share_filters() -> None:
    engine = _Engine([_Result(), _Result(scalar=7)])
    repository = RagRepository(cast(AsyncEngine, engine), _settings())
    knowledge_id = uuid4()

    await repository.list_traces(
        knowledge_id=knowledge_id, status="FAILED", question="端口_%", limit=20, offset=40
    )
    total = await repository.count_traces(
        knowledge_id=knowledge_id, status="FAILED", question="端口_%"
    )

    (page_sql, page_params), (count_sql, count_params) = engine.connection.executed
    # A unique tie-breaker keeps pages stable when traces share created_at.
    assert "ORDER BY created_at DESC, id DESC" in page_sql
    assert "LIMIT :limit OFFSET :offset" in page_sql
    assert page_params["limit"] == 20 and page_params["offset"] == 40
    assert "question ILIKE :question ESCAPE" in page_sql
    assert page_params["question"] == r"%端口\_\%%"
    assert count_sql.startswith("SELECT count(*) FROM rag_trace WHERE")
    assert "question ILIKE :question" in count_sql and "status = :status" in count_sql
    assert {k: v for k, v in page_params.items() if k not in {"limit", "offset"}} == count_params
    assert total == 7


async def test_task_page_and_count_share_filters() -> None:
    engine = _Engine([_Result(), _Result(scalar=3)])
    repository = IngestionRepository(cast(AsyncEngine, engine), _settings())

    await repository.list_tasks(status="FAILED", limit=10, offset=10)
    assert await repository.count_tasks(status="FAILED") == 3

    (page_sql, page_params), (count_sql, count_params) = engine.connection.executed
    assert "ORDER BY created_at DESC, id DESC" in page_sql
    assert "LIMIT :limit OFFSET :offset" in page_sql
    assert page_params == {"status": "FAILED", "limit": 10, "offset": 10}
    assert count_sql == "SELECT count(*) FROM task_record WHERE 1 = 1 AND status = :status"
    assert count_params == {"status": "FAILED"}


def _document_repository(results: list[_Result]) -> tuple[IngestionRepository, _Connection]:
    engine = _Engine(results)
    repository = IngestionRepository(cast(AsyncEngine, engine), _settings())

    async def _knowledge_base(_knowledge_id: UUID) -> dict[str, Any]:
        return {}

    repository.get_knowledge_base = _knowledge_base  # type: ignore[method-assign]
    return repository, engine.connection


async def test_document_list_without_card_scope_keeps_original_shape() -> None:
    repository, connection = _document_repository([_Result()])

    await repository.list_documents(uuid4())

    sql, params = connection.executed[0]
    assert "rag_document_card" not in sql
    assert "NULL::text AS card_state" in sql
    assert "LIMIT ALL OFFSET :offset" in sql
    assert "ORDER BY created_at DESC, id DESC" in sql
    assert params == {"kb_id": params["kb_id"], "offset": 0}


async def test_document_list_honours_offset_without_limit() -> None:
    repository, connection = _document_repository([_Result()])

    await repository.list_documents(uuid4(), offset=30)

    sql, params = connection.executed[0]
    assert sql.rstrip().endswith("LIMIT ALL OFFSET :offset")
    assert params["offset"] == 30 and "limit" not in params


async def test_document_list_filters_by_card_state_build_status_and_search() -> None:
    repository, connection = _document_repository([_Result(), _Result(scalar=2)])
    knowledge_id, index_id = uuid4(), uuid4()
    filters: dict[str, Any] = {
        "search": "PSU_板",
        "build_status": "SKIPPED",
        "card_state": "PENDING_REVIEW",
        "card_index_id": index_id,
    }

    await repository.list_documents(
        knowledge_id, limit=25, offset=50, sort="display_name", descending=False, **filters
    )
    assert await repository.count_documents(knowledge_id, **filters) == 2

    (sql, params), (count_sql, count_params) = connection.executed
    assert "LEFT JOIN rag_document_card c ON c.index_id = :card_index_id" in sql
    assert "ELSE 'PENDING_REVIEW'" in sql
    assert "last_build_status = :build_status" in sql and "card_state = :card_state" in sql
    assert "d.display_name ILIKE :search" in sql and "d.filename ILIKE :search" in sql
    assert "ORDER BY display_name ASC, id ASC LIMIT :limit OFFSET :offset" in sql
    assert params["search"] == r"%PSU\_板%"
    assert params["limit"] == 25 and params["offset"] == 50
    assert count_sql.startswith("SELECT count(*) FROM (")
    assert count_params == {k: v for k, v in params.items() if k not in {"limit", "offset"}}


async def test_document_list_rejects_unknown_sort_column() -> None:
    repository, _connection = _document_repository([])
    try:
        await repository.list_documents(uuid4(), sort="id; DROP TABLE x")
    except ApiError as error:
        assert error.status_code == 422
    else:  # pragma: no cover - the assertion above must run
        raise AssertionError("unknown sort column accepted")


# --------------------------------------------------------------------------- API


class _PagedRepository:
    """Serves ``rows`` with real offset/limit slicing and records the calls."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.list_calls: list[dict[str, Any]] = []
        self.count_calls: list[dict[str, Any]] = []

    def _page(self, limit: int | None, offset: int) -> list[dict[str, Any]]:
        return self.rows[offset : None if limit is None else offset + limit]

    async def list_traces(self, *, limit: int, offset: int, **filters: Any) -> list[dict[str, Any]]:
        self.list_calls.append({"limit": limit, "offset": offset, **filters})
        return self._page(limit, offset)

    async def count_traces(self, **filters: Any) -> int:
        self.count_calls.append(filters)
        return len(self.rows)

    async def list_tasks(self, *, limit: int, offset: int, **filters: Any) -> list[dict[str, Any]]:
        self.list_calls.append({"limit": limit, "offset": offset, **filters})
        return self._page(limit, offset)

    async def count_tasks(self, **filters: Any) -> int:
        self.count_calls.append(filters)
        return len(self.rows)

    async def list_documents(
        self, knowledge_id: UUID, *, limit: int | None, offset: int, **rest: Any
    ) -> list[dict[str, Any]]:
        self.list_calls.append({"limit": limit, "offset": offset, **rest})
        return self._page(limit, offset)

    async def count_documents(self, knowledge_id: UUID, **filters: Any) -> int:
        self.count_calls.append(filters)
        return len(self.rows)


def _trace(index: int) -> dict[str, Any]:
    return {
        "trace_id": str(uuid4()),
        "request_id": f"request-{index}",
        "mode": "CHAT",
        "kb_id": str(uuid4()),
        "index_id": str(uuid4()),
        "question": f"问题 {index}",
        "status": "COMPLETED",
        "latency": {},
        "error": {},
        "created_at": "2026-10-09T00:00:00Z",
        "finished_at": None,
    }


def test_trace_list_returns_total_header_and_passes_offset(client: TestClient) -> None:
    repository = _PagedRepository([_trace(i) for i in range(5)])
    client.app.dependency_overrides[get_rag_repository] = lambda: repository
    try:
        response = client.get("/api/v1/traces?limit=2&offset=2&q=%20端口%20", headers=AUTH)
        assert response.status_code == 200
        assert [row["question"] for row in response.json()] == ["问题 2", "问题 3"]
        assert response.headers["X-Total-Count"] == "5"
        assert repository.list_calls[0]["offset"] == 2
        assert repository.list_calls[0]["question"] == "端口"
        assert repository.count_calls[0]["question"] == "端口"

        # A short first page is already complete: no count query.
        repository.count_calls.clear()
        response = client.get("/api/v1/traces?limit=50", headers=AUTH)
        assert response.headers["X-Total-Count"] == "5"
        assert repository.count_calls == []
    finally:
        client.app.dependency_overrides.clear()


def test_total_header_is_exposed_to_cross_origin_callers(client: TestClient) -> None:
    repository = _PagedRepository([])
    client.app.dependency_overrides[get_rag_repository] = lambda: repository
    try:
        response = client.get("/api/v1/traces", headers={**AUTH, "Origin": "http://localhost:5173"})
        # localhost:5173 is in the default CORS allow-list.
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
        assert "X-Total-Count" in response.headers["access-control-expose-headers"]
    finally:
        client.app.dependency_overrides.clear()


def test_task_list_pages_with_total_header(client: TestClient) -> None:
    rows = [
        {
            "id": str(uuid4()),
            "task_type": "INDEX_BUILD",
            "status": "FAILED",
            "stage": "PARSING",
            "progress": 0,
            "attempt": 1,
            "kb_id": None,
            "index_id": None,
            "document_id": None,
            "error_code": "E",
            "error_message": "失败",
            "metadata": {},
            "created_at": "2026-10-09T00:00:00Z",
            "started_at": None,
            "finished_at": None,
            "updated_at": "2026-10-09T00:00:00Z",
        }
        for _ in range(3)
    ]
    repository = _PagedRepository(rows)
    client.app.dependency_overrides[get_ingestion_repository] = lambda: repository
    try:
        response = client.get("/api/v1/tasks?status=FAILED&limit=2&offset=2", headers=AUTH)
        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.headers["X-Total-Count"] == "3"
        assert repository.list_calls[0] == {
            "limit": 2,
            "offset": 2,
            "status": "FAILED",
            "knowledge_id": None,
            "created_from": None,
            "created_to": None,
        }
    finally:
        client.app.dependency_overrides.clear()


def _document(index: int) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "knowledge_id": str(uuid4()),
        "filename": f"doc-{index}.pdf",
        "display_name": f"文档 {index}",
        "file_hash": "0" * 64,
        "file_size": 100 + index,
        "status": "STORED",
        "created_at": "2026-10-09T00:00:00Z",
        "updated_at": "2026-10-09T00:00:00Z",
        "card_state": "CONFIRMED",
    }


def test_document_list_wires_filters_sort_and_paging(client: TestClient) -> None:
    repository = _PagedRepository([_document(i) for i in range(4)])
    client.app.dependency_overrides[get_ingestion_repository] = lambda: repository
    knowledge_id, index_id = uuid4(), uuid4()
    try:
        response = client.get(
            f"/api/v1/knowledge-bases/{knowledge_id}/documents?q=PSU&build_status=READY"
            f"&card_index_id={index_id}&card_state=CONFIRMED&sort=file_size&order=asc"
            "&limit=3&offset=3",
            headers=AUTH,
        )
        assert response.status_code == 200
        assert [row["display_name"] for row in response.json()] == ["文档 3"]
        assert response.json()[0]["card_state"] == "CONFIRMED"
        assert response.headers["X-Total-Count"] == "4"
        call = repository.list_calls[0]
        assert call["limit"] == 3 and call["offset"] == 3
        assert call["sort"] == "file_size" and call["descending"] is False
        assert call["search"] == "PSU" and call["build_status"] == "READY"
        assert call["card_state"] == "CONFIRMED" and call["card_index_id"] == index_id

        # Without limit the original "all documents" response is kept.
        repository.count_calls.clear()
        response = client.get(f"/api/v1/knowledge-bases/{knowledge_id}/documents", headers=AUTH)
        assert len(response.json()) == 4
        assert response.headers["X-Total-Count"] == "4"
        assert repository.count_calls == []
    finally:
        client.app.dependency_overrides.clear()


def test_document_card_filter_requires_an_index(client: TestClient) -> None:
    repository = _PagedRepository([])
    client.app.dependency_overrides[get_ingestion_repository] = lambda: repository
    try:
        response = client.get(
            f"/api/v1/knowledge-bases/{uuid4()}/documents?card_state=MISSING", headers=AUTH
        )
        assert response.status_code == 422
        bad_sort = client.get(f"/api/v1/knowledge-bases/{uuid4()}/documents?sort=id", headers=AUTH)
        assert bad_sort.status_code == 422
        assert repository.list_calls == []
    finally:
        client.app.dependency_overrides.clear()


class _ConversationRepository:
    def __init__(self, total: int) -> None:
        self.total = total
        self.offsets: list[int] = []

    async def list_conversations(
        self, knowledge_id: UUID, user_id: str | None, limit: int, *, offset: int, **_: Any
    ) -> list[dict[str, Any]]:
        self.offsets.append(offset)
        count = max(0, min(limit, self.total - offset))
        return [
            {
                "id": str(uuid4()),
                "knowledge_id": str(knowledge_id),
                "user_id": None,
                "title": "会话",
                "last_execution_engine": "RAG",
                "conversation_mode": "RAG",
                "message_count": 2,
                "last_source": "ASSISTANT_API",
                "created_at": "2026-10-09T00:00:00Z",
                "updated_at": "2026-10-09T00:00:00Z",
            }
            for _ in range(count)
        ]

    async def count_conversations(self, *_: Any, **__: Any) -> int:
        return self.total


def test_conversation_list_reports_total_and_offset(client: TestClient) -> None:
    repository = _ConversationRepository(total=120)
    client.app.dependency_overrides[get_gateway_repository] = lambda: repository
    try:
        response = client.get(
            f"/api/v1/conversations?knowledge_id={uuid4()}&limit=50&offset=100", headers=AUTH
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 20
        assert body["total"] == 120
        assert repository.offsets == [100]
    finally:
        client.app.dependency_overrides.clear()
