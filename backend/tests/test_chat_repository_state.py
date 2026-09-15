"""Unit tests for the chat doc-routing repository methods (fake engine)."""

from __future__ import annotations

import json
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.common.errors import ApiError
from app.core.config import Settings
from app.gateway.repository import GatewayRepository
from app.rag.models import ModelSnapshot, Turn
from app.rag.repository import RagRepository


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _FakeResult:
        return self

    def __iter__(self) -> Any:
        return iter(self._rows)

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def one_or_none(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def one(self) -> dict[str, Any]:
        return self._rows[0]

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    @property
    def rowcount(self) -> int:
        return len(self._rows)


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


def _repository(results: list[Any]) -> tuple[RagRepository, _FakeConnection]:
    engine = _FakeEngine(results)
    repo = RagRepository(cast(AsyncEngine, engine), Settings(_env_file=None, app_env="test"))
    return repo, engine.connection


def _gateway_repository(results: list[Any]) -> tuple[GatewayRepository, _FakeConnection]:
    engine = _FakeEngine(results)
    repo = GatewayRepository(cast(AsyncEngine, engine), Settings(_env_file=None, app_env="test"))
    return repo, engine.connection


async def test_assistant_conversation_filter_uses_typed_direct_uuid_comparison() -> None:
    repo, connection = _gateway_repository([[]])
    conversation_id, knowledge_id = uuid4(), uuid4()

    await repo.get_assistant_conversation(
        conversation_id=conversation_id,
        external_user_id="business-user-1",
        knowledge_id=knowledge_id,
    )

    sql, params = connection.executed[0]
    assert "c.knowledge_id = :knowledge_id" in sql
    assert "kb.assistant_online" in sql
    assert ":knowledge_id IS NULL" not in sql
    assert params is not None and params["knowledge_id"] == knowledge_id


async def test_assistant_image_asset_requires_stored_document() -> None:
    image_asset_id, knowledge_id = uuid4(), uuid4()
    repo, connection = _gateway_repository(
        [
            [
                {
                    "id": image_asset_id,
                    "mime_type": "image/png",
                    "minio_bucket": "documents",
                    "minio_object_key": "images/example.png",
                }
            ]
        ]
    )

    asset = await repo.assistant_image_asset(
        knowledge_id=knowledge_id, image_asset_id=image_asset_id
    )

    sql, params = connection.executed[0]
    assert "d.status = 'STORED'" in sql
    assert "kb.assistant_online" in sql
    assert params == {"image_asset_id": image_asset_id, "knowledge_id": knowledge_id}
    assert asset["id"] == image_asset_id


async def test_assistant_request_reservation_rechecks_publication_in_transaction() -> None:
    knowledge_id = uuid4()
    repo, connection = _gateway_repository([[{"status": "ENABLED", "assistant_online": True}], []])

    state, request = await repo.reserve_assistant_request(
        external_user_id="business-user-1",
        knowledge_id=knowledge_id,
        idempotency_key="request-key-1",
        request_hash="request-hash-1",
        conversation_id=None,
    )

    statements = "\n".join(statement for statement, _ in connection.executed)
    assert state == "NEW"
    assert request["status"] == "RUNNING"
    assert "assistant_online" in connection.executed[0][0]
    assert "FOR SHARE" in connection.executed[0][0]
    assert "INSERT INTO assistant_request" in statements


async def test_assistant_request_reservation_rejects_offline_knowledge_base() -> None:
    repo, connection = _gateway_repository([[{"status": "ENABLED", "assistant_online": False}]])

    with pytest.raises(ApiError) as raised:
        await repo.reserve_assistant_request(
            external_user_id="business-user-1",
            knowledge_id=uuid4(),
            idempotency_key="request-key-1",
            request_hash="request-hash-1",
            conversation_id=None,
        )

    assert raised.value.code == "KNOWLEDGE_BASE_NOT_FOUND"
    assert all("INSERT INTO assistant_request" not in sql for sql, _ in connection.executed)


async def test_assistant_request_replay_increments_observability_fields() -> None:
    knowledge_id = uuid4()
    request_id = uuid4()
    existing = {
        "id": request_id,
        "request_hash": "request-hash-1",
        "status": "COMPLETED",
        "replay_count": 0,
    }
    replayed = {**existing, "replay_count": 1}
    repo, connection = _gateway_repository(
        [[{"status": "ENABLED", "assistant_online": True}], [existing], [replayed]]
    )

    state, request = await repo.reserve_assistant_request(
        external_user_id="business-user-1",
        knowledge_id=knowledge_id,
        idempotency_key="request-key-1",
        request_hash="request-hash-1",
        conversation_id=None,
    )

    assert state == "REPLAY"
    assert request["replay_count"] == 1
    sql, params = connection.executed[2]
    assert "replay_count = replay_count + 1" in sql
    assert "last_replayed_at = now()" in sql
    assert "updated_at = now()" in sql
    assert "RETURNING *" in sql
    assert params == {"id": request_id}


async def test_assistant_request_reservation_rechecks_conversation_association() -> None:
    repo, connection = _gateway_repository([[{"status": "ENABLED", "assistant_online": True}], []])

    with pytest.raises(ApiError) as raised:
        await repo.reserve_assistant_request(
            external_user_id="business-user-1",
            knowledge_id=uuid4(),
            idempotency_key="request-key-1",
            request_hash="request-hash-1",
            conversation_id=uuid4(),
        )

    assert raised.value.code == "CONVERSATION_NOT_FOUND"
    conversation_sql = connection.executed[1][0]
    assert "knowledge_id = :knowledge_id" in conversation_sql
    assert "user_id = :user_id" in conversation_sql
    assert "FOR SHARE" in conversation_sql


async def test_cancel_assistant_request_closes_linked_runtime_in_one_statement() -> None:
    request_id = uuid4()
    repo, connection = _gateway_repository([[]])

    await repo.cancel_assistant_request(
        request_id=request_id,
        external_request_id="external-request-1",
        payload={"code": "CLIENT_DISCONNECTED", "message": "客户端已断开"},
        partial_content="部分回答",
    )

    sql, params = connection.executed[0]
    assert "UPDATE assistant_request" in sql
    assert "UPDATE rag_trace" in sql
    assert "UPDATE message" in sql
    assert "status = 'CANCELLED'" in sql
    assert "t.request_id = :external_request_id" in sql
    assert params is not None
    assert params["id"] == request_id
    assert params["external_request_id"] == "external-request-1"
    assert params["partial_content"] == "部分回答"


async def test_list_kb_documents_prefers_display_name() -> None:
    doc_id, kb_id = uuid4(), uuid4()
    repo, _ = _repository(
        [
            [
                {"id": doc_id, "filename": "a.docx", "display_name": "显示名"},
                {"id": uuid4(), "filename": "b.docx", "display_name": None},
            ]
        ]
    )

    docs = await repo.list_kb_documents(kb_id)

    assert docs[0] == {"document_id": str(doc_id), "title": "显示名"}
    assert docs[1]["title"] == "b.docx"


async def test_get_document_blocks_orders_elements_and_assets() -> None:
    doc_id, kb_id = uuid4(), uuid4()
    element_id, asset_id = uuid4(), uuid4()
    repo, _ = _repository(
        [
            [{"id": doc_id, "filename": "a.docx", "display_name": "升级文档"}],
            [
                {
                    "id": element_id,
                    "element_type": "IMAGE",
                    "content": "",
                    "section_path": [],
                    "asset_id": asset_id,
                    "ocr_text": "OCR 文本",
                    "vision_caption": "截图描述",
                    "asset_width": 1145,
                    "asset_height": 764,
                }
            ],
        ]
    )

    blocks = await repo.get_document_blocks(knowledge_id=kb_id, index_id=kb_id, document_id=doc_id)

    assert blocks["document_id"] == str(doc_id)
    assert blocks["title"] == "升级文档"
    element = blocks["elements"][0]
    assert element["element_id"] == element_id
    assert element["image_asset_id"] == asset_id
    assert element["image_caption"] == "截图描述"


async def test_get_document_blocks_rejects_missing_document() -> None:
    doc_id, kb_id = uuid4(), uuid4()
    repo, _ = _repository([[]])

    with pytest.raises(ApiError) as exc_info:
        await repo.get_document_blocks(knowledge_id=kb_id, index_id=kb_id, document_id=doc_id)

    assert exc_info.value.code == "DOCUMENT_NOT_FOUND"
    assert exc_info.value.status_code == 404


async def test_complete_turn_patches_doc_routing() -> None:
    repo, connection = _repository([])
    turn = Turn(
        trace_id=uuid4(),
        conversation_id=uuid4(),
        user_message_id=uuid4(),
        assistant_message_id=uuid4(),
        knowledge_id=uuid4(),
        index_id=uuid4(),
        embedding_model_name="m",
        llm=ModelSnapshot("zhipu", "glm", {}),
        history=[],
    )
    routing = {"decision": "DELIVER", "doc_scores": []}

    await repo.complete_turn(
        turn,
        answer="摘要",
        sources=[{"source_id": "S1"}],
        usage={},
        citation_result={"document_level": True},
        timings={"total_ms": 5},
        doc_routing=routing,
    )

    trace_sql, params = connection.executed[1]
    assert "jsonb_set" in trace_sql
    assert "{doc_routing}" in trace_sql
    assert json.loads(str(params["doc_routing"]))["decision"] == "DELIVER"


async def test_complete_turn_without_routing_leaves_trace_untouched() -> None:
    repo, connection = _repository([])
    turn = Turn(
        trace_id=uuid4(),
        conversation_id=uuid4(),
        user_message_id=uuid4(),
        assistant_message_id=uuid4(),
        knowledge_id=uuid4(),
        index_id=uuid4(),
        embedding_model_name="m",
        llm=ModelSnapshot("zhipu", "glm", {}),
        history=[],
    )

    await repo.complete_turn(
        turn,
        answer="答案",
        sources=[],
        usage={},
        citation_result={},
        timings={"total_ms": 5},
    )

    trace_sql, _ = connection.executed[1]
    assert "jsonb_set" not in trace_sql


async def test_list_traces_filters_by_error_code_and_conversation() -> None:
    repo, connection = _repository([])
    conversation_id = uuid4()

    await repo.list_traces(
        status="FAILED", error_code="INVALID_EVIDENCE", conversation_id=conversation_id
    )

    sql, params = connection.executed[0]
    assert "error ->> 'code' = :error_code" in sql
    assert "conversation_id = :conversation_id" in sql
    assert params is not None
    assert params["error_code"] == "INVALID_EVIDENCE"
    assert params["conversation_id"] == conversation_id


async def test_gateway_message_replay_exposes_support_columns() -> None:
    """Conversation replay needs response_type / support_mode / content_blocks;
    the columns exist since 0015 but were never selected."""
    conversation_id = uuid4()
    row = {
        "id": str(uuid4()),
        "conversation_id": str(conversation_id),
        "role": "ASSISTANT",
        "content": "请先执行第一步",
        "sources": [],
        "status": "COMPLETED",
        "content_blocks": [],
        "response_type": "DOCUMENT_DELIVERED",
        "support_mode": "STEP_GUIDANCE",
        "created_at": "2026-08-20T10:00:00",
        "updated_at": "2026-08-20T10:00:01",
    }
    repo, connection = _gateway_repository([[row]])

    messages = await repo.list_conversation_messages(conversation_id, 100)

    sql, _ = connection.executed[0]
    for column in ("content_blocks", "response_type", "support_mode"):
        assert column in sql
    assert messages[0]["response_type"] == "DOCUMENT_DELIVERED"
    assert messages[0]["support_mode"] == "STEP_GUIDANCE"
