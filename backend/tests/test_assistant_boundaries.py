from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from test_assistant_content_consistency import (
    RecordingFacadeRepository,
    message_row,
    terminal,
)
from test_assistant_facade import _Repository
from test_chat_doc_routing_flow import (
    DOC_A,
    _candidate,
    _RecordingLlmProvider,
    _RoutingRepository,
    _service,
    _turn,
)
from test_gateway import FakeGatewayRepository, FakeSupport, policy

from app.api.assistant import _message, messages
from app.api.schemas import AssistantMessageResponse
from app.assistant.service import AssistantFacadeService, parse_sse, public_error, sse
from app.common.errors import ApiError
from app.gateway.models import ExecutionPolicy
from app.gateway.service import ChatGatewayService
from app.rag.models import RetrievalConfig
from app.support.document_delivery import deliver_document
from app.support.models import DocumentContentBlock, FocusedDocument


class PageRepository:
    def __init__(self, count):
        self.sequences = list(range(1, count + 1))

    async def get_assistant_conversation(self, **kwargs):
        return {"conversation_id": kwargs["conversation_id"]}

    async def list_assistant_messages(self, *, conversation_id, limit, before_sequence):
        # Match the real SQL: DESC LIMIT, then ASC for presentation.
        selected = [n for n in self.sequences if before_sequence is None or n < before_sequence]
        now = datetime.now(UTC)
        return [
            dict(
                id=str(n),
                conversation_id=conversation_id,
                sequence_no=n,
                role="USER" if n % 2 else "ASSISTANT",
                content=str(n),
                status="COMPLETED",
                created_at=now,
                updated_at=now,
            )
            for n in sorted(sorted(selected, reverse=True)[:limit])
        ]


@pytest.mark.parametrize("count", [0, 1, 3, 4, 6, 8, 100, 101])
@pytest.mark.parametrize("limit", [1, 3, 100])
async def test_message_pages_cover_every_row_once_and_start_with_latest(count, limit):
    repo = PageRepository(count)
    cursor, seen = None, []
    while True:
        page = await messages(uuid4(), repo, "user", limit=limit, before=cursor)
        ids = [int(row["message_id"]) for row in page["items"]]
        assert ids == sorted(ids)
        if not seen:
            assert ids == list(range(max(1, count - limit + 1), count + 1))
        seen += ids
        cursor = page["next_before"]
        if cursor is None:
            break
        assert ids and cursor == str(ids[0])
    assert sorted(seen) == list(range(1, count + 1))
    assert len(seen) == len(set(seen))


async def test_older_page_is_stable_when_new_messages_arrive():
    repo = PageRepository(8)
    first = await messages(uuid4(), repo, "user", limit=3, before=None)
    repo.sequences.extend([9, 10])
    second = await messages(uuid4(), repo, "user", limit=3, before=first["next_before"])
    assert [row["message_id"] for row in first["items"]] == ["6", "7", "8"]
    assert [row["message_id"] for row in second["items"]] == ["3", "4", "5"]


@pytest.mark.parametrize(
    "cursor", ["", "0", "-1", "1.5", "abc", " 1", "\uff11", str(2**31), "9" * 32]
)
async def test_bad_message_cursor_rejected_before_query(cursor):
    repo = PageRepository(0)
    repo.list_assistant_messages = AsyncMock()
    with pytest.raises(ApiError) as exc:
        await messages(uuid4(), repo, "user", limit=3, before=cursor)
    assert exc.value.code == "INVALID_CURSOR"
    repo.list_assistant_messages.assert_not_called()


async def test_message_pagination_does_not_bypass_ownership():
    repo = PageRepository(0)
    repo.get_assistant_conversation = AsyncMock(return_value=None)
    repo.list_assistant_messages = AsyncMock()
    with pytest.raises(ApiError) as exc:
        await messages(uuid4(), repo, "other-user", limit=3, before=None)
    assert exc.value.code == "CONVERSATION_NOT_FOUND"
    repo.list_assistant_messages.assert_not_called()


@pytest.mark.parametrize("status", ["FAILED", "CANCELLED"])
@pytest.mark.parametrize("retryable", [False, True])
async def test_failed_replay_preserves_public_snapshot_and_does_not_generate(status, retryable):
    error = {
        "request_id": "original",
        "code": "INTERNAL_ERROR",
        "message": "回答生成失败",
        "retryable": retryable,
        "conversation_id": str(uuid4()),
        "message_id": str(uuid4()),
        "trace_id": str(uuid4()),
        "partial_content": "已生成部分内容",
        "url": "/management-only",
        "internal_diagnostics": {"model": "private"},
    }
    before = deepcopy(error)
    facade = AssistantFacadeService(None, None)
    frames = [
        f
        async for f in facade._stream_prepared(
            external_user_id="user",
            knowledge_id=uuid4(),
            conversation_id=None,
            question="question",
            choice_id=None,
            include_images=True,
            request_id="retry",
            policy=None,
            state="REPLAY",
            stored={"id": uuid4(), "status": status, "terminal_error": error},
        )
    ]
    assert [event for event, _ in map(parse_sse, frames)] == ["accepted", "error"]
    assert terminal(frames, "error") == {
        k: v for k, v in error.items() if k not in {"url", "internal_diagnostics"}
    }
    assert error == before


@pytest.mark.parametrize(
    "code, expected",
    [
        ("TURN_DEADLINE_EXCEEDED", True),
        ("CLIENT_DISCONNECTED", True),
        ("INTERNAL_ERROR", False),
        ("UNKNOWN_ERROR", False),
    ],
)
def test_legacy_error_without_retry_flag_uses_code_not_truthiness(code, expected):
    result = public_error({"code": code, "retryable": None}, request_id="current")
    assert result["retryable"] is expected
    assert result["request_id"] == "current"


def test_empty_legacy_error_has_compatible_defaults():
    assert public_error({}, request_id="current")["code"] == "REQUEST_PREVIOUSLY_FAILED"


class FailureRepository(_Repository):
    async def reserve_assistant_request(self, **kwargs):
        if self.failed:
            return "REPLAY", {
                "id": uuid4(),
                "status": "FAILED",
                "terminal_error": self.failed["payload"],
            }
        return await super().reserve_assistant_request(**kwargs)


@pytest.mark.parametrize("retryable", [False, True])
async def test_live_failed_response_saved_history_and_replay_agree(retryable):
    class Gateway:
        async def stream(self, **kwargs):
            yield sse("trace", {"conversation_id": str(uuid4()), "trace_id": str(uuid4())})
            yield sse("message", {"delta": "部分回答"})
            yield sse(
                "error",
                {"code": "INTERNAL_ERROR", "message": "回答生成失败", "retryable": retryable},
            )

    repo = FailureRepository()
    facade = AssistantFacadeService(repo, Gateway())
    kwargs = dict(
        external_user_id="user",
        knowledge_id=uuid4(),
        conversation_id=None,
        question="question",
        choice_id=None,
        include_images=True,
        idempotency_key="key",
        request_id="first",
    )
    live = terminal([f async for f in facade.stream(**kwargs)], "error")
    assert live == repo.failed["payload"]
    row = message_row(snapshot={"error": live}, blocks=[])
    row.update(
        status="FAILED", content=live["partial_content"], trace_error={"retryable": not retryable}
    )
    history = AssistantMessageResponse.model_validate(_message(row)).model_dump()
    assert history["error"] == {k: live[k] for k in ("code", "message", "retryable")}
    assert history["content"] == live["partial_content"]
    facade.gateway = None  # replay must never touch a model
    replay = terminal(
        [f async for f in facade.stream(**{**kwargs, "request_id": "second"})], "error"
    )
    assert replay == live


def long_document():
    asset = uuid4()
    elements = [
        {"element_type": "TEXT", "content": "文档原始标题"},
        {"element_type": "TEXT", "content": "第一步：" + "长正文。" * 10000},
        {"element_type": "IMAGE", "content": "", "image_asset_id": asset},
        {"element_type": "TABLE", "content": "|参数|值|\n|---|---|\n|A|24|"},
        {"element_type": "TEXT", "content": "最后一步：检查完成后恢复供电。"},
        {"element_type": "IMAGE", "content": "", "image_asset_id": asset},
    ]
    return {"document_id": str(DOC_A), "title": "新显示名称", "elements": elements}


@pytest.mark.parametrize("include_images", [False, True])
@pytest.mark.parametrize(
    "engine", [ExecutionPolicy.RAG_ONLY, ExecutionPolicy.SUPPORT_WITH_RAG_FALLBACK]
)
async def test_long_delivery_complete_saved_history_replay_and_sync_agree(engine, include_images):
    document = long_document()
    original = deepcopy(document)
    repo = _RoutingRepository(
        turn=_turn(),
        candidates=[_candidate(DOC_A, i) for i in range(1, 4)],
        document_blocks={str(DOC_A): document},
    )
    repo.get_engine_retrieval_config = AsyncMock(return_value=RetrievalConfig())
    llm = _RecordingLlmProvider([])
    rag = _service(repo, llm)
    rag.settings.m3_doc_delivery_max_tokens = 1000  # old deployment config cannot truncate
    selected = policy(engine)
    facade_repo = RecordingFacadeRepository(selected, repo.turn)
    facade = AssistantFacadeService(
        facade_repo, ChatGatewayService(FakeGatewayRepository(), FakeSupport("NO_MATCH"), rag)
    )
    kwargs = dict(
        external_user_id="user",
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=None,
        question="操作步骤",
        choice_id=None,
        include_images=include_images,
        idempotency_key="long-doc",
        request_id="first",
    )
    live = terminal([f async for f in facade.stream(**kwargs)], "complete")
    assert live["response_type"] == "DOC_DELIVERED"
    expected = (
        ["text", "text", "image", "table", "text", "image"]
        if include_images
        else ["text", "text", "table", "text"]
    )
    assert [b["type"] for b in live["content"]] == expected
    assert live["content"][0]["text"] == document["elements"][0]["content"]
    assert live["content"][1]["text"] == document["elements"][1]["content"]
    assert any(b.get("text") == document["elements"][-2]["content"] for b in live["content"])
    assert live["answer"] == "**《新显示名称》**"
    assert facade_repo.completed["response"] == live
    assert _message(message_row(snapshot=live, blocks=[]))["content_blocks"] == live["content"]
    replay = terminal(
        [f async for f in facade.stream(**{**kwargs, "request_id": "second"})], "complete"
    )
    assert replay == live
    synchronous = await rag.generate(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=None,
        question="操作步骤",
        request_id="sync",
        include_images=include_images,
    )
    assert synchronous["content"] == live["content"]
    images = [b["image_asset_id"] for b in live["content"] if b["type"] == "image"]
    assert repo.completed["sources"][0]["image_asset_ids"] == images
    assert document == original
    assert llm.prompts == []


@pytest.mark.parametrize("include_images", [False, True])
def test_support_original_delivery_keeps_long_body_table_and_last_images(include_images):
    doc = long_document()
    focused = FocusedDocument(
        id=DOC_A,
        title=doc["title"],
        problem_code=None,
        index_id=uuid4(),
        blocks=[
            DocumentContentBlock(
                element_id=uuid4(),
                sequence_no=i,
                type=e["element_type"].lower(),
                text=e["content"],
                image_asset_id=e.get("image_asset_id"),
            )
            for i, e in enumerate(doc["elements"], 1)
        ],
    )
    content, images = deliver_document(focused, include_images=include_images)
    assert content[0]["text"] == doc["elements"][0]["content"]
    assert content[1]["text"] == doc["elements"][1]["content"]
    assert any(b.get("text") == doc["elements"][-2]["content"] for b in content)
    assert len(images) == (2 if include_images else 0)
    assert any(b["type"] == "table" for b in content)
