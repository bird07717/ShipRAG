from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.types import Receive, Scope, Send

from app.api.dependencies import get_resources
from app.api.engine_dependencies import (
    get_assistant_facade,
    get_gateway_repository,
    get_rag_repository,
)
from app.api.schemas import (
    AssistantChatStreamRequest,
    AssistantConversationItem,
    AssistantConversationListResponse,
    AssistantKnowledgeBaseListResponse,
    AssistantMessageListResponse,
    AssistantWelcomeResponse,
)
from app.assistant.service import (
    AssistantFacadeService,
    decode_cursor,
    encode_cursor,
    public_payload,
)
from app.common.assets import attachment_content_disposition
from app.common.errors import ApiError
from app.core.resources import AppResources
from app.gateway.repository import GatewayRepository
from app.observability.recorder import observe_stream_errors
from app.rag.repository import RagRepository
from app.rag.welcome import build_welcome


def external_user_id(
    value: Annotated[str, Header(alias="X-External-User-ID", min_length=1, max_length=100)],
) -> str:
    return value


router = APIRouter(
    prefix="/assistant", tags=["assistant"], dependencies=[Depends(external_user_id)]
)


class _AssistantStreamingResponse(StreamingResponse):
    """Finalize the reserved request even when ASGI body delivery fails."""

    def __init__(
        self,
        content: AsyncIterator[bytes],
        *,
        finalizer: Callable[[], Awaitable[None]],
        media_type: str,
        headers: dict[str, str],
    ) -> None:
        super().__init__(content, media_type=media_type, headers=headers)
        self._finalizer = finalizer

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._finalizer()


def _conversation(row: dict[str, Any], retention_days: int) -> dict[str, Any]:
    return {
        "conversation_id": row["conversation_id"],
        "knowledge": {"knowledge_id": row["knowledge_id"], "name": row["knowledge_name"]},
        "title": row["title"],
        "message_count": row["message_count"],
        "created_at": row["created_at"],
        "last_activity_at": row["last_activity_at"],
        "expires_at": (
            row["last_activity_at"] + timedelta(days=retention_days) if retention_days > 0 else None
        ),
        "status": "ACTIVE",
    }


def _message(row: dict[str, Any]) -> dict[str, Any]:
    payload, trace_error = (
        public_payload(dict(row.get("response_payload") or {})),
        row.get("trace_error") or {},
    )
    status = "GENERATING" if str(row["status"]) == "STREAMING" else str(row["status"])
    error = None
    if status in {"FAILED", "CANCELLED"} and isinstance(trace_error, dict):
        error = {
            "code": str(trace_error.get("code", "ANSWER_FAILED")),
            "message": str(trace_error.get("message", "回答未完成")),
            "retryable": trace_error.get("retryable"),
        }
    return {
        "message_id": row["id"],
        "conversation_id": row["conversation_id"],
        "role": row["role"],
        "status": status,
        "content": row["content"],
        "trace_id": row.get("trace_id"),
        "response_type": row.get("response_type") or payload.get("response_type"),
        "content_blocks": row.get("content_blocks") or payload.get("content") or [],
        "sections": payload.get("sections") or [],
        "document": payload.get("document"),
        "references": payload.get("references") or [],
        "clarification": payload.get("clarification"),
        "escalation": payload.get("escalation"),
        "disclaimer": payload.get("disclaimer"),
        "error": error,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@router.get("/knowledge-bases", response_model=AssistantKnowledgeBaseListResponse)
async def list_knowledge_bases(
    repository: Annotated[GatewayRepository, Depends(get_gateway_repository)],
) -> dict[str, Any]:
    return {"items": await repository.list_assistant_knowledge_bases()}


@router.get("/knowledge-bases/{knowledge_id}/welcome", response_model=AssistantWelcomeResponse)
async def welcome(
    knowledge_id: UUID, repository: Annotated[RagRepository, Depends(get_rag_repository)]
) -> dict[str, Any]:
    payload = build_welcome(
        await repository.get_knowledge_base_summary(knowledge_id, enabled_only=True),
        await repository.list_kb_documents(knowledge_id),
    )
    return {
        "knowledge_id": knowledge_id,
        "message": payload["message"],
        "suggestions": payload["suggestions"],
    }


@router.get("/conversations", response_model=AssistantConversationListResponse)
async def list_conversations(
    knowledge_id: UUID,
    repository: Annotated[GatewayRepository, Depends(get_gateway_repository)],
    user_id: Annotated[str, Depends(external_user_id)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> dict[str, Any]:
    before_activity, before_id = decode_cursor(cursor) if cursor else (None, None)
    rows = await repository.list_assistant_conversations(
        knowledge_id=knowledge_id,
        external_user_id=user_id,
        limit=limit + 1,
        before_activity=before_activity,
        before_id=before_id,
    )
    page, more = rows[:limit], len(rows) > limit
    return {
        "items": [
            _conversation(row, repository.settings.conversation_retention_days) for row in page
        ],
        "next_cursor": encode_cursor(page[-1]["last_activity_at"], page[-1]["conversation_id"])
        if more and page
        else None,
    }


@router.get("/conversations/{conversation_id}", response_model=AssistantConversationItem)
async def conversation(
    conversation_id: UUID,
    repository: Annotated[GatewayRepository, Depends(get_gateway_repository)],
    user_id: Annotated[str, Depends(external_user_id)],
) -> dict[str, Any]:
    row = await repository.get_assistant_conversation(
        conversation_id=conversation_id, external_user_id=user_id
    )
    if row is None:
        raise ApiError("CONVERSATION_NOT_FOUND", "会话不存在", 404)
    return _conversation(row, repository.settings.conversation_retention_days)


@router.get(
    "/conversations/{conversation_id}/messages", response_model=AssistantMessageListResponse
)
async def messages(
    conversation_id: UUID,
    repository: Annotated[GatewayRepository, Depends(get_gateway_repository)],
    user_id: Annotated[str, Depends(external_user_id)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before: Annotated[str | None, Query(max_length=32)] = None,
) -> dict[str, Any]:
    if (
        await repository.get_assistant_conversation(
            conversation_id=conversation_id, external_user_id=user_id
        )
        is None
    ):
        raise ApiError("CONVERSATION_NOT_FOUND", "会话不存在", 404)
    try:
        before_sequence = int(before) if before else None
    except ValueError as exc:
        raise ApiError("INVALID_CURSOR", "分页游标无效", 422) from exc
    rows = await repository.list_assistant_messages(
        conversation_id=conversation_id, limit=limit + 1, before_sequence=before_sequence
    )
    page, more = rows[:limit], len(rows) > limit
    return {
        "items": [_message(row) for row in page],
        "next_before": str(page[0]["sequence_no"]) if more and page else None,
    }


@router.post("/chat/stream")
async def chat_stream(
    payload: AssistantChatStreamRequest,
    request: Request,
    facade: Annotated[AssistantFacadeService, Depends(get_assistant_facade)],
    user_id: Annotated[str, Depends(external_user_id)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> StreamingResponse:
    # Complete visibility, policy, idempotency and concurrency checks before
    # StreamingResponse commits HTTP 200. Contractual 4xx errors therefore
    # remain normal JSON responses instead of becoming in-stream SSE errors.
    source = await facade.prepare_stream(
        external_user_id=user_id,
        knowledge_id=payload.knowledge_id,
        conversation_id=payload.conversation_id,
        question=payload.question,
        choice_id=payload.choice_id,
        include_images=payload.include_images,
        idempotency_key=idempotency_key,
        request_id=str(request.state.request_id),
    )
    return _AssistantStreamingResponse(
        observe_stream_errors(request, source, user_facing=True),
        finalizer=source.aclose,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _asset_response(
    resources: AppResources, asset: dict[str, Any], *, filename: str | None = None
) -> StreamingResponse:
    try:
        upstream = await run_in_threadpool(
            resources.minio.get_object, asset["minio_bucket"], asset["minio_object_key"]
        )
    except Exception as exc:
        raise ApiError("ASSET_UNAVAILABLE", "资源暂时不可用", 503) from exc

    async def body() -> AsyncIterator[bytes]:
        try:
            while chunk := await run_in_threadpool(upstream.read, 64 * 1024):
                yield bytes(chunk)
        finally:
            await run_in_threadpool(upstream.close)
            await run_in_threadpool(upstream.release_conn)

    upstream_headers = getattr(upstream, "headers", None)
    upstream_content_type = (
        upstream_headers.get("Content-Type") if upstream_headers is not None else None
    )
    headers = {
        "Cache-Control": "private, max-age=300",
        "X-Content-Type-Options": "nosniff",
    }
    for candidate in (
        upstream_headers.get("Content-Length") if upstream_headers is not None else None,
        asset.get("file_size"),
    ):
        if candidate is None:
            continue
        try:
            content_length = int(candidate)
        except (TypeError, ValueError):
            continue
        if content_length >= 0:
            headers["Content-Length"] = str(content_length)
            break
    if filename:
        headers["Content-Disposition"] = attachment_content_disposition(filename)
    return StreamingResponse(
        body(),
        media_type=asset.get("mime_type") or upstream_content_type or "application/octet-stream",
        headers=headers,
    )


@router.get("/knowledge-bases/{knowledge_id}/documents/{document_id}/content")
async def document_content(
    knowledge_id: UUID,
    document_id: UUID,
    repository: Annotated[GatewayRepository, Depends(get_gateway_repository)],
    resources: Annotated[AppResources, Depends(get_resources)],
) -> StreamingResponse:
    asset = await repository.assistant_document(knowledge_id=knowledge_id, document_id=document_id)
    return await _asset_response(
        resources, asset, filename=str(asset.get("filename") or asset["display_name"])
    )


@router.get("/knowledge-bases/{knowledge_id}/image-assets/{image_asset_id}/content")
async def image_content(
    knowledge_id: UUID,
    image_asset_id: UUID,
    repository: Annotated[GatewayRepository, Depends(get_gateway_repository)],
    resources: Annotated[AppResources, Depends(get_resources)],
) -> StreamingResponse:
    return await _asset_response(
        resources,
        await repository.assistant_image_asset(
            knowledge_id=knowledge_id, image_asset_id=image_asset_id
        ),
    )
