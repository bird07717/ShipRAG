from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse

from app.api.engine_dependencies import get_support_repository, get_support_service
from app.api.schemas import (
    ChatRequest,
    ChatResponse,
    ChatStreamRequest,
    ProblemProfileResponse,
    ProblemProfileUpsert,
)
from app.support.repository import SupportRepository
from app.support.service import TechnicalSupportService

router = APIRouter(prefix="/support", tags=["support"])


@router.post("/chat", response_model=ChatResponse)
async def support_chat(
    payload: ChatRequest,
    request: Request,
    service: Annotated[TechnicalSupportService, Depends(get_support_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=200)] = None,
) -> dict[str, Any]:
    """Run Profile Support directly, independent of the runtime switch."""
    return await service.handle(
        knowledge_id=payload.knowledge_id,
        conversation_id=payload.conversation_id,
        question=payload.question,
        request_id=payload.request_id or str(request.state.request_id),
        user_id=payload.user_id,
        problem_code=payload.problem_code,
        document_id=payload.document_id,
        choice_id=payload.choice_id,
        include_images=payload.include_images,
        idempotency_key=idempotency_key,
    )


@router.post("/chat/stream")
async def support_chat_stream(
    payload: ChatStreamRequest,
    request: Request,
    service: Annotated[TechnicalSupportService, Depends(get_support_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=200)] = None,
) -> StreamingResponse:
    return StreamingResponse(
        service.stream(
            knowledge_id=payload.knowledge_id,
            conversation_id=payload.conversation_id,
            question=payload.question,
            request_id=payload.request_id or str(request.state.request_id),
            user_id=payload.user_id,
            problem_code=payload.problem_code,
            document_id=payload.document_id,
            choice_id=payload.choice_id,
            include_images=payload.include_images,
            idempotency_key=idempotency_key,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/problem-profiles", response_model=list[ProblemProfileResponse])
async def list_problem_profiles(
    knowledge_id: UUID,
    repository: Annotated[SupportRepository, Depends(get_support_repository)],
) -> list[dict[str, Any]]:
    profiles = await repository.list_profiles(knowledge_id)
    return [
        {
            "id": item.id,
            "document_id": item.document_id,
            "knowledge_id": item.knowledge_id,
            "problem_code": item.problem_code,
            "canonical_question": item.canonical_question,
            "aliases": item.aliases,
            "keywords": item.keywords,
            "product_name": item.product_name,
            "model_names": item.model_names,
            "task_type": item.task_type,
            "enabled": item.enabled,
            "support_ready": item.support_ready,
            "readiness_reasons": item.readiness_reasons,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }
        for item in profiles
    ]


@router.put("/problem-profiles", response_model=ProblemProfileResponse)
async def upsert_problem_profile(
    knowledge_id: UUID,
    payload: ProblemProfileUpsert,
    repository: Annotated[SupportRepository, Depends(get_support_repository)],
) -> dict[str, Any]:
    return await repository.upsert_profile(knowledge_id, payload.model_dump(mode="json"))
