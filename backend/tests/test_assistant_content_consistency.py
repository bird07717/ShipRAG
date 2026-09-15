from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_assistant_facade import _Repository
from test_gateway import FakeGatewayRepository, FakeRag, FakeSupport, policy

from app.api.assistant import _message
from app.api.schemas import AssistantMessageResponse
from app.assistant.service import AssistantFacadeService, parse_sse, public_complete
from app.gateway.models import ExecutionPolicy
from app.gateway.service import ChatGatewayService


def terminal(frames, name="done"):
    return next(body for event, body in map(parse_sse, frames) if event == name)


def message_row(*, snapshot, blocks):
    now = datetime.now(UTC)
    return {
        "id": uuid4(),
        "conversation_id": uuid4(),
        "role": "ASSISTANT",
        "status": "COMPLETED",
        "content": "legacy text",
        "response_payload": snapshot,
        "content_blocks": blocks,
        "created_at": now,
        "updated_at": now,
    }


def document_payload():
    return {
        "answer": "**《操作手册》**",
        "response_type": "DOC_DELIVERED",
        "content": [
            {"type": "text", "text": "1. 检查接线。"},
            {
                "type": "image",
                "image_asset_id": str(uuid4()),
                "url": "/api/v1/image-assets/internal/content",
                "metadata": {"download_url": "/management-only", "width": 640},
            },
            {"type": "table", "text": "|端口|状态|\n|---|---|\n|A|正常|"},
        ],
        "document": {"id": str(uuid4()), "title": "操作手册", "download_url": "/private"},
        "references": [{"document_id": str(uuid4()), "title": "操作手册"}],
        "sections": [{"title": "检查", "content": "完成后核对状态。"}],
        "clarification": {"message": "请选择", "choices": []},
        "disclaimer": "按原文操作。",
    }


@pytest.mark.parametrize("with_snapshot", [True, False])
def test_history_filters_legacy_urls_even_after_response_schema(with_snapshot):
    original = document_payload()
    row = message_row(snapshot=original if with_snapshot else {}, blocks=original["content"])
    before = deepcopy(row)
    history = AssistantMessageResponse.model_validate(_message(row)).model_dump()
    live = public_complete(original, "request")
    assert history["content_blocks"] == live["content"]
    assert "/api/v1/" not in json.dumps(history["content_blocks"])
    assert "download_url" not in json.dumps(history["content_blocks"])
    assert history["content_blocks"][1]["image_asset_id"]
    assert history["content_blocks"][1]["metadata"] == {"width": 640}
    if with_snapshot:
        assert history["content"] == live["answer"]
        for key in ("document", "references", "sections", "clarification", "disclaimer"):
            assert history[key] == live[key]
    assert row == before  # public filtering does not rewrite stored history


@pytest.mark.parametrize("content", [[], None, [{"type": "text", "text": "只返回文字"}]])
def test_history_never_resurrects_internal_images_over_public_snapshot(content):
    row = message_row(snapshot={"content": content}, blocks=document_payload()["content"])
    assert _message(row)["content_blocks"] == (content or [])


@pytest.mark.asyncio
@pytest.mark.parametrize("include_images", [False, True])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "engine", [ExecutionPolicy.RAG_ONLY, ExecutionPolicy.SUPPORT_WITH_RAG_FALLBACK]
)
async def test_gateway_forwards_image_preference_to_rag(engine, stream, include_images):
    rag = FakeRag()
    selected = policy(engine)
    gateway = ChatGatewayService(FakeGatewayRepository(), FakeSupport("NO_MATCH"), rag)
    kwargs = dict(
        policy=selected,
        knowledge_id=selected.knowledge_id,
        conversation_id=None,
        question="操作步骤",
        request_id="preference-test",
        user_id=None,
        problem_code=None,
        document_id=None,
        choice_id=None,
        include_images=include_images,
        idempotency_key=None,
    )
    if stream:
        assert terminal([frame async for frame in gateway.stream(**kwargs)])
    else:
        await gateway.handle(**kwargs)
    assert rag.last_kwargs["include_images"] is include_images


class RecordingFacadeRepository(_Repository):
    def __init__(self, selected, turn):
        super().__init__()
        self.selected = selected
        self.turn = turn
        self.durable_id = uuid4()

    async def resolve_policy(self, **kwargs):
        return self.selected

    async def reserve_assistant_request(self, **kwargs):
        if self.completed:
            return "REPLAY", {
                "id": self.durable_id,
                "status": "COMPLETED",
                "terminal_response": self.completed["response"],
            }
        return "NEW", {"id": self.durable_id, "status": "RUNNING"}

    async def assistant_turn_context(self, **kwargs):
        return {
            "conversation_id": kwargs["conversation_id"],
            "user_message_id": self.turn.user_message_id,
            "assistant_message_id": self.turn.assistant_message_id,
            "trace_id": kwargs["trace_id"],
        }


@pytest.mark.asyncio
async def test_legacy_replay_strips_urls_without_regeneration_or_snapshot_mutation():
    turn = SimpleNamespace(
        knowledge_id=uuid4(),
        user_message_id=uuid4(),
        assistant_message_id=uuid4(),
    )
    repo = RecordingFacadeRepository(policy(ExecutionPolicy.RAG_ONLY), turn)
    snapshot = {
        **document_payload(),
        "request_id": "original-request",
        "created_at": "2026-09-11T00:00:00Z",
    }
    original = deepcopy(snapshot)
    repo.completed = {"response": snapshot}
    # Any model invocation would fail: replay does not need a gateway.
    facade = AssistantFacadeService(repo, None)
    frames = [
        frame
        async for frame in facade.stream(
            external_user_id="user",
            knowledge_id=turn.knowledge_id,
            conversation_id=None,
            question="操作步骤",
            choice_id=None,
            include_images=True,
            idempotency_key="old-key",
            request_id="new-request",
        )
    ]
    response = terminal(frames, "complete")
    assert "download_url" not in json.dumps(response)
    assert '"url"' not in json.dumps(response)
    assert response["answer"] == original["answer"]
    assert response["request_id"] == original["request_id"]
    assert response["created_at"] == original["created_at"]
    assert snapshot == original
