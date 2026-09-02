from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.common.errors import ApiError
from app.gateway.repository import GatewayRepository
from app.gateway.service import ChatGatewayService

HEARTBEAT_SECONDS = 15.0


def sse(event: str, payload: dict[str, Any]) -> bytes:
    return (
        f"event: {event}\ndata: "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "\n\n"
    ).encode()


def ping() -> bytes:
    return b": ping\n\n"


def public_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize database-native identifiers before they reach the public stream."""
    return {key: str(value) if isinstance(value, UUID) else value for key, value in payload.items()}


def public_payload(value: Any) -> Any:
    """Remove internal resource URLs from values crossing the Assistant boundary.

    The documented external contract identifies documents and images by ID; it
    never grants callers a management-plane URL. Applying this recursively
    protects both newly streamed responses and historical response payloads.
    """
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, list):
        return [public_payload(item) for item in value]
    if isinstance(value, dict):
        return {
            key: public_payload(item)
            for key, item in value.items()
            if key not in {"download_url", "url"}
        }
    return value


def parse_sse(frame: bytes) -> tuple[str | None, dict[str, Any] | None]:
    event: str | None = None
    data: list[str] = []
    for line in frame.decode("utf-8", errors="replace").replace("\r\n", "\n").split("\n"):
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if not data:
        return event, None
    try:
        value = json.loads("\n".join(data))
    except json.JSONDecodeError:
        return event, None
    return event, value if isinstance(value, dict) else None


def encode_cursor(activity: datetime, conversation_id: UUID) -> str:
    raw = json.dumps({"a": activity.astimezone(UTC).isoformat(), "i": str(conversation_id)})
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode())
        activity = datetime.fromisoformat(str(value["a"])).astimezone(UTC)
        return activity, UUID(str(value["i"]))
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError("INVALID_CURSOR", "分页游标无效", 422) from exc


def request_hash(
    *, conversation_id: UUID | None, question: str, choice_id: str | None, include_images: bool
) -> str:
    payload = {
        "conversation_id": str(conversation_id) if conversation_id else None,
        "question": question.strip(),
        "choice_id": choice_id,
        "include_images": include_images,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def public_complete(response: dict[str, Any], request_id: str) -> dict[str, Any]:
    """Remove pipeline/model diagnostics from a final gateway response."""
    fields = (
        "conversation_id",
        "message_id",
        "trace_id",
        "response_type",
        "answer",
        "content",
        "sections",
        "document",
        "references",
        "clarification",
        "escalation",
        "disclaimer",
        "next_suggestions",
    )
    result = {
        key: public_payload(response.get(key)) for key in fields if response.get(key) is not None
    }
    result.setdefault("content", [])
    result.setdefault("sections", [])
    result.setdefault("references", [])
    result.setdefault("next_suggestions", [])
    result["request_id"] = request_id
    result["created_at"] = datetime.now(UTC).isoformat()
    return result


class AssistantFacadeService:
    """Translate existing gateway frames to the stable external contract."""

    def __init__(self, repository: GatewayRepository, gateway: ChatGatewayService) -> None:
        self.repository = repository
        self.gateway = gateway

    async def stream(
        self,
        *,
        external_user_id: str,
        knowledge_id: UUID,
        conversation_id: UUID | None,
        question: str,
        choice_id: str | None,
        include_images: bool,
        idempotency_key: str,
        request_id: str,
    ) -> AsyncIterator[bytes]:
        if conversation_id is not None:
            visible = await self.repository.get_assistant_conversation(
                conversation_id=conversation_id,
                external_user_id=external_user_id,
                knowledge_id=knowledge_id,
            )
            if visible is None:
                raise ApiError("CONVERSATION_NOT_FOUND", "会话不存在", 404)
        policy = await self.repository.resolve_policy(
            knowledge_id=knowledge_id, conversation_id=conversation_id
        )
        state, stored = await self.repository.reserve_assistant_request(
            external_user_id=external_user_id,
            knowledge_id=knowledge_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash(
                conversation_id=conversation_id,
                question=question,
                choice_id=choice_id,
                include_images=include_images,
            ),
            conversation_id=conversation_id,
        )
        durable_id = UUID(str(stored["id"]))
        yield sse(
            "accepted",
            {
                "request_id": request_id,
                "knowledge_id": str(knowledge_id),
                "conversation_id": str(conversation_id) if conversation_id else None,
            },
        )
        if state == "REPLAY":
            status = str(stored["status"])
            if status == "COMPLETED":
                response = dict(stored.get("terminal_response") or {})
                if response:
                    yield sse("complete", response)
                    return
            error = dict(stored.get("terminal_error") or {})
            yield sse(
                "error",
                {
                    "request_id": request_id,
                    "code": error.get("code", "REQUEST_PREVIOUSLY_FAILED"),
                    "message": error.get("message", "请求未完成，请使用新的幂等键重试"),
                    "retryable": True,
                },
            )
            return

        known_context: dict[str, Any] = {}
        answer_parts: list[str] = []
        source = self.gateway.stream(
            policy=policy,
            knowledge_id=knowledge_id,
            conversation_id=conversation_id,
            question=question,
            request_id=request_id,
            user_id=external_user_id,
            problem_code=None,
            document_id=None,
            choice_id=choice_id,
            include_images=include_images,
            # The facade owns idempotency; avoid collision with legacy support keys.
            idempotency_key=None,
        )
        pending_frame: asyncio.Future[bytes] | None = None
        try:
            iterator = source.__aiter__()
            while True:
                if pending_frame is None:
                    # AsyncIterator.__anext__ is typed as an Awaitable, while
                    # create_task accepts only coroutines.  ensure_future
                    # preserves the frame across heartbeat timeouts either way.
                    pending_frame = asyncio.ensure_future(iterator.__anext__())
                try:
                    frame = await asyncio.wait_for(
                        asyncio.shield(pending_frame), timeout=HEARTBEAT_SECONDS
                    )
                except TimeoutError:
                    yield ping()
                    continue
                except StopAsyncIteration:
                    pending_frame = None
                    break
                pending_frame = None
                event, payload = parse_sse(frame)
                payload = payload or {}
                if event == "status":
                    stage = str(payload.get("stage", ""))
                    public_stage = {
                        "gateway_routing": "PREPARING",
                        "support_processing": "PREPARING",
                        "rag_processing": "RETRIEVING",
                    }.get(stage, "GENERATING")
                    yield sse("progress", {"stage": public_stage})
                elif event == "trace":
                    await self.repository.mark_assistant_trace_runtime(
                        request_id=request_id, policy=policy
                    )
                    known_context.update(
                        {key: value for key, value in payload.items() if value is not None}
                    )
                    context = await self.repository.assistant_turn_context(
                        conversation_id=UUID(str(payload["conversation_id"])),
                        trace_id=UUID(str(payload["trace_id"])),
                    )
                    known_context.update(public_context(context))
                    await self.repository.set_assistant_request_context(
                        request_id=durable_id,
                        conversation_id=UUID(str(known_context["conversation_id"])),
                        user_message_id=UUID(str(known_context["user_message_id"]))
                        if known_context.get("user_message_id")
                        else None,
                        assistant_message_id=UUID(str(known_context["assistant_message_id"]))
                        if known_context.get("assistant_message_id")
                        else None,
                        trace_id=UUID(str(known_context["trace_id"]))
                        if known_context.get("trace_id")
                        else None,
                    )
                    yield sse("context", known_context)
                elif event == "message":
                    text = str(payload.get("delta", ""))
                    if text:
                        answer_parts.append(text)
                        yield sse(
                            "delta",
                            {"message_id": known_context.get("assistant_message_id"), "text": text},
                        )
                elif event == "done":
                    await self.repository.mark_assistant_trace_runtime(
                        request_id=request_id, policy=policy
                    )
                    complete = public_complete(payload, request_id)
                    context = {
                        "conversation_id": complete.get("conversation_id"),
                        "assistant_message_id": complete.get("message_id"),
                        "trace_id": complete.get("trace_id"),
                    }
                    if not known_context and context["conversation_id"] and context["trace_id"]:
                        resolved = await self.repository.assistant_turn_context(
                            conversation_id=UUID(str(context["conversation_id"])),
                            trace_id=UUID(str(context["trace_id"])),
                        )
                        context.update(public_context(resolved))
                        yield sse("context", context)
                    await self.repository.complete_assistant_request(
                        request_id=durable_id,
                        response=complete,
                        message_id=UUID(str(complete["message_id"])),
                        conversation_id=UUID(str(complete["conversation_id"])),
                        trace_id=UUID(str(complete["trace_id"]))
                        if complete.get("trace_id")
                        else None,
                    )
                    yield sse("complete", complete)
                    return
                elif event == "error":
                    await self.repository.mark_assistant_trace_runtime(
                        request_id=request_id, policy=policy
                    )
                    error = {
                        "request_id": request_id,
                        "conversation_id": known_context.get("conversation_id"),
                        "message_id": known_context.get("assistant_message_id"),
                        "trace_id": payload.get("trace_id") or known_context.get("trace_id"),
                        "code": payload.get("code", "INTERNAL_ERROR"),
                        "message": payload.get("message", "回答生成失败"),
                        "retryable": bool(payload.get("retryable", False)),
                        "partial_content": "".join(answer_parts),
                    }
                    await self.repository.fail_assistant_request(
                        request_id=durable_id, payload=error
                    )
                    yield sse("error", error)
                    return
            error = {
                "request_id": request_id,
                "code": "STREAM_INTERRUPTED",
                "message": "回答流意外中断",
                "retryable": True,
                "partial_content": "".join(answer_parts),
            }
            await self.repository.mark_assistant_trace_runtime(request_id=request_id, policy=policy)
            await self.repository.fail_assistant_request(request_id=durable_id, payload=error)
            yield sse("error", error)
        except asyncio.CancelledError:
            await asyncio.shield(
                self.repository.mark_assistant_trace_runtime(request_id=request_id, policy=policy)
            )
            await asyncio.shield(
                self.repository.fail_assistant_request(
                    request_id=durable_id,
                    payload={"code": "CLIENT_DISCONNECTED", "message": "客户端已断开"},
                    cancelled=True,
                )
            )
            raise
        except Exception:
            await self.repository.mark_assistant_trace_runtime(request_id=request_id, policy=policy)
            error = {
                "request_id": request_id,
                "conversation_id": known_context.get("conversation_id"),
                "message_id": known_context.get("assistant_message_id"),
                "trace_id": known_context.get("trace_id"),
                "code": "INTERNAL_ERROR",
                "message": "回答生成失败",
                "retryable": False,
                "partial_content": "".join(answer_parts),
            }
            await self.repository.fail_assistant_request(request_id=durable_id, payload=error)
            yield sse("error", error)
        finally:
            if pending_frame is not None and not pending_frame.done():
                pending_frame.cancel()
                await asyncio.gather(pending_frame, return_exceptions=True)
            await source.aclose()
