from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import SecretStr, ValidationError

from app.api.dependencies import require_assistant_service_token
from app.api.schemas import AssistantChatStreamRequest
from app.assistant.service import AssistantFacadeService, decode_cursor, encode_cursor, parse_sse
from app.common.errors import ApiError
from app.core.config import Settings


def _event(frame: bytes) -> tuple[str | None, dict | None]:
    return parse_sse(frame)


class _Repository:
    def __init__(self, *, replay: bool = False) -> None:
        self.replay = replay
        self.completed: dict | None = None
        self.context: dict | None = None
        self.failed: dict | None = None
        self.runtime_marks: list[dict] = []

    async def get_assistant_conversation(self, **_: object) -> dict | None:
        return {"conversation_id": uuid4()}

    async def resolve_policy(self, **_: object) -> object:
        return object()

    async def reserve_assistant_request(self, **_: object) -> tuple[str, dict]:
        if self.replay:
            return "REPLAY", {
                "id": uuid4(),
                "status": "COMPLETED",
                "terminal_response": {
                    "request_id": "old",
                    "answer": "已保存",
                    "conversation_id": str(uuid4()),
                    "message_id": str(uuid4()),
                    "content": [],
                    "sections": [],
                    "references": [],
                    "next_suggestions": [],
                },
            }
        return "NEW", {"id": uuid4(), "status": "RUNNING"}

    async def assistant_turn_context(self, **_: object) -> dict:
        return {
            "conversation_id": str(CONVERSATION_ID),
            "user_message_id": str(USER_MESSAGE_ID),
            "assistant_message_id": str(ASSISTANT_MESSAGE_ID),
            "trace_id": str(TRACE_ID),
        }

    async def set_assistant_request_context(self, **kwargs: object) -> None:
        self.context = dict(kwargs)

    async def complete_assistant_request(self, **kwargs: object) -> None:
        self.completed = dict(kwargs)

    async def fail_assistant_request(self, **kwargs: object) -> None:
        self.failed = dict(kwargs)

    async def mark_assistant_trace_runtime(self, **kwargs: object) -> None:
        self.runtime_marks.append(dict(kwargs))


class _Gateway:
    def __init__(self) -> None:
        self.called = False

    async def stream(self, **_: object):  # type: ignore[no-untyped-def]
        self.called = True
        yield b'event: status\ndata: {"stage":"rag_processing","engine":"RAG"}\n\n'
        trace = {"conversation_id": str(CONVERSATION_ID), "trace_id": str(TRACE_ID)}
        yield f"event: trace\ndata: {json.dumps(trace)}\n\n".encode()
        yield 'event: message\ndata: {"delta":"你好"}\n\n'.encode()
        done = {
            "conversation_id": str(CONVERSATION_ID),
            "message_id": str(ASSISTANT_MESSAGE_ID),
            "trace_id": str(TRACE_ID),
            "response_type": "ANSWERED",
            "answer": "你好",
            "execution_engine": "RAG",
        }
        yield f"event: done\ndata: {json.dumps(done, ensure_ascii=False)}\n\n".encode()


class _BrokenGateway:
    async def stream(self, **_: object):  # type: ignore[no-untyped-def]
        raise RuntimeError("provider unavailable")
        yield b""  # pragma: no cover - makes this an async generator


CONVERSATION_ID = uuid4()
USER_MESSAGE_ID = uuid4()
ASSISTANT_MESSAGE_ID = uuid4()
TRACE_ID = uuid4()
KNOWLEDGE_ID = uuid4()


@pytest.mark.asyncio
async def test_facade_hides_engine_and_normalizes_existing_stream() -> None:
    repository, gateway = _Repository(), _Gateway()
    service = AssistantFacadeService(repository, gateway)  # type: ignore[arg-type]
    frames = [
        frame
        async for frame in service.stream(
            external_user_id="business-user-1",
            knowledge_id=KNOWLEDGE_ID,
            conversation_id=None,
            question="怎么做",
            choice_id=None,
            include_images=True,
            idempotency_key="key-1",
            request_id="request-1",
        )
    ]
    events = [_event(frame) for frame in frames]
    assert [event for event, _ in events] == [
        "accepted",
        "progress",
        "context",
        "delta",
        "complete",
    ]
    assert events[1][1] == {"stage": "RETRIEVING"}
    assert events[3][1] == {"message_id": str(ASSISTANT_MESSAGE_ID), "text": "你好"}
    assert events[-1][1]["request_id"] == "request-1"
    assert "execution_engine" not in events[-1][1]
    assert repository.completed is not None
    assert len(repository.runtime_marks) == 2
    assert all(mark["request_id"] == "request-1" for mark in repository.runtime_marks)


@pytest.mark.asyncio
async def test_facade_replays_completed_request_without_second_generation() -> None:
    repository, gateway = _Repository(replay=True), _Gateway()
    service = AssistantFacadeService(repository, gateway)  # type: ignore[arg-type]
    frames = [
        frame
        async for frame in service.stream(
            external_user_id="business-user-1",
            knowledge_id=KNOWLEDGE_ID,
            conversation_id=None,
            question="怎么做",
            choice_id=None,
            include_images=True,
            idempotency_key="key-1",
            request_id="request-1",
        )
    ]
    assert [event for event, _ in map(_event, frames)] == ["accepted", "complete"]
    assert not gateway.called


@pytest.mark.asyncio
async def test_facade_persists_unexpected_stream_failure() -> None:
    repository = _Repository()
    service = AssistantFacadeService(repository, _BrokenGateway())  # type: ignore[arg-type]
    frames = [
        frame
        async for frame in service.stream(
            external_user_id="business-user-1",
            knowledge_id=KNOWLEDGE_ID,
            conversation_id=None,
            question="怎么做",
            choice_id=None,
            include_images=True,
            idempotency_key="key-1",
            request_id="request-1",
        )
    ]
    event, payload = _event(frames[-1])
    assert event == "error"
    assert payload and payload["code"] == "INTERNAL_ERROR"
    assert repository.failed is not None


def test_external_request_rejects_internal_engine_selection() -> None:
    with pytest.raises(ValidationError):
        AssistantChatStreamRequest.model_validate(
            {"knowledge_id": str(KNOWLEDGE_ID), "question": "测试", "execution_engine": "RAG"}
        )


def test_cursor_is_opaque_and_invalid_cursor_is_rejected() -> None:
    created = datetime(2026, 9, 1, tzinfo=UTC)
    cursor = encode_cursor(created, CONVERSATION_ID)
    assert decode_cursor(cursor) == (created, CONVERSATION_ID)
    with pytest.raises(ApiError) as exc:
        decode_cursor("not-a-cursor")
    assert exc.value.code == "INVALID_CURSOR"


@pytest.mark.asyncio
async def test_assistant_credential_is_separate_and_supports_rotation() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        service_token=SecretStr("admin-token"),
        assistant_service_token=SecretStr("assistant-token"),
        assistant_service_token_previous=SecretStr("previous-token"),
    )
    await require_assistant_service_token(
        settings,
        HTTPAuthorizationCredentials(scheme="Bearer", credentials="assistant-token"),
    )
    await require_assistant_service_token(
        settings,
        HTTPAuthorizationCredentials(scheme="Bearer", credentials="previous-token"),
    )
    with pytest.raises(ApiError) as exc:
        await require_assistant_service_token(
            settings,
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="admin-token"),
        )
    assert exc.value.code == "AUTHENTICATION_REQUIRED"
