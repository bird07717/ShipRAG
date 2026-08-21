"""Unit tests for the chat doc-routing repository methods (fake engine)."""

from __future__ import annotations

import json
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.common.errors import ApiError
from app.core.config import Settings
from app.rag.models import ModelSnapshot, Turn
from app.rag.repository import RagRepository


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _FakeResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def one_or_none(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


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


async def test_set_conversation_focus_resets_chat_context() -> None:
    repo, connection = _repository([])
    conversation_id, document_id = uuid4(), uuid4()

    await repo.set_conversation_focus(conversation_id, document_id)

    sql, params = connection.executed[0]
    assert "focus_document_id = :document_id" in sql
    assert "'{}'::jsonb" in sql
    assert params == {"conversation_id": conversation_id, "document_id": document_id}


async def test_clear_conversation_focus() -> None:
    repo, connection = _repository([])
    conversation_id = uuid4()

    await repo.clear_conversation_focus(conversation_id)

    sql, params = connection.executed[0]
    assert "focus_document_id = NULL" in sql
    assert params == {"conversation_id": conversation_id}


async def test_set_conversation_pending_serializes_json() -> None:
    repo, connection = _repository([])
    conversation_id = uuid4()
    options = [{"document_id": "doc-1", "title": "标题"}]

    await repo.set_conversation_pending(
        conversation_id, pending_options=options, pending_query="如何升级"
    )

    sql, params = connection.executed[0]
    assert "chat_context = CAST(:chat_context AS jsonb)" in sql
    payload = json.loads(str(params["chat_context"]))
    assert payload == {"pending_options": options, "pending_query": "如何升级"}


async def test_clear_conversation_pending() -> None:
    repo, connection = _repository([])
    conversation_id = uuid4()

    await repo.clear_conversation_pending(conversation_id)

    sql, params = connection.executed[0]
    assert "chat_context = '{}'::jsonb" in sql
    assert params == {"conversation_id": conversation_id}


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


async def test_complete_turn_patches_doc_routing_and_prompt() -> None:
    repo, connection = _repository([])
    turn = Turn(
        trace_id=uuid4(),
        conversation_id=uuid4(),
        user_message_id=uuid4(),
        assistant_message_id=uuid4(),
        knowledge_id=uuid4(),
        index_id=uuid4(),
        embedding_model_name="m",
        prompt_template="t",
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
        prompt="实际 Prompt",
        doc_routing=routing,
    )

    trace_sql, params = connection.executed[1]
    assert "jsonb_set" in trace_sql
    assert "{doc_routing}" in trace_sql
    assert "prompt = :prompt" in trace_sql
    assert json.loads(str(params["doc_routing"]))["decision"] == "DELIVER"
    assert params["prompt"] == "实际 Prompt"


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
        prompt_template="t",
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
    assert "prompt = :prompt" not in trace_sql


def test_turn_focus_defaults_are_backward_compatible() -> None:
    turn = Turn(
        trace_id=uuid4(),
        conversation_id=uuid4(),
        user_message_id=uuid4(),
        assistant_message_id=uuid4(),
        knowledge_id=uuid4(),
        index_id=uuid4(),
        embedding_model_name="m",
        prompt_template="t",
        llm=ModelSnapshot("zhipu", "glm", {}),
        history=[],
    )
    assert turn.focus_document_id is None
    assert turn.chat_context == {}
    assert isinstance(turn.trace_id, UUID)
