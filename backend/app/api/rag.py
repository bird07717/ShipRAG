from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_app_settings, get_resources
from app.api.schemas import (
    ChatStreamRequest,
    MessageResponse,
    ModelConfigResponse,
    PlaygroundRequest,
    PromptResponse,
    RagTraceResponse,
    TraceSummaryResponse,
)
from app.core.config import Settings
from app.core.resources import AppResources
from app.rag.repository import RagRepository
from app.services.rag import RagService

router = APIRouter()


def get_rag_repository(
    settings: Annotated[Settings, Depends(get_app_settings)],
    resources: Annotated[AppResources, Depends(get_resources)],
) -> RagRepository:
    return RagRepository(resources.database, settings)


def get_rag_service(
    repository: Annotated[RagRepository, Depends(get_rag_repository)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    resources: Annotated[AppResources, Depends(get_resources)],
) -> RagService:
    return RagService(repository, settings, resources.minio)


@router.post("/chat/stream", tags=["chat"])
async def chat_stream(
    payload: ChatStreamRequest,
    request: Request,
    service: Annotated[RagService, Depends(get_rag_service)],
) -> StreamingResponse:
    prepared = await service.prepare(
        knowledge_id=payload.knowledge_id,
        conversation_id=payload.conversation_id,
        question=payload.question,
        request_id=str(request.state.request_id),
    )
    return StreamingResponse(
        service.stream(prepared),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/prompts", response_model=list[PromptResponse], tags=["prompts"])
async def list_prompts(
    repository: Annotated[RagRepository, Depends(get_rag_repository)],
) -> list[dict[str, Any]]:
    return await repository.list_prompts()


@router.get("/models", response_model=list[ModelConfigResponse], tags=["models"])
async def list_models(
    repository: Annotated[RagRepository, Depends(get_rag_repository)],
) -> list[dict[str, Any]]:
    return await repository.list_models()


@router.post("/rag/playground", response_model=RagTraceResponse, tags=["playground"])
async def run_playground(
    payload: PlaygroundRequest,
    request: Request,
    repository: Annotated[RagRepository, Depends(get_rag_repository)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    resources: Annotated[AppResources, Depends(get_resources)],
) -> dict[str, Any]:
    options = payload.options
    overrides = {
        field: value
        for field, value in {
            "m3_vector_top_k": options.vector_top_k,
            "m5_bm25_top_k": options.bm25_top_k,
            "m5_fusion_top_k": options.fusion_top_k,
            "m5_rerank_top_n": options.rerank_top_n,
            "m3_context_max_chunks": options.context_max_chunks,
        }.items()
        if value is not None
    }
    playground_service = RagService(
        repository,
        settings.model_copy(update=overrides),
        resources.minio,
    )
    prepared = await playground_service.prepare(
        knowledge_id=payload.knowledge_id,
        conversation_id=payload.conversation_id,
        question=payload.question,
        request_id=str(request.state.request_id),
        mode="PLAYGROUND",
    )
    async for _event in playground_service.stream(prepared):
        pass
    trace = await repository.get_trace(prepared.turn.trace_id)
    if not options.include_prompt:
        trace["prompt"] = None
    return trace


@router.get("/traces", response_model=list[TraceSummaryResponse], tags=["traces"])
async def list_traces(
    repository: Annotated[RagRepository, Depends(get_rag_repository)],
    knowledge_id: Annotated[UUID | None, Query()] = None,
    trace_status: Annotated[
        Literal["RUNNING", "COMPLETED", "FAILED", "CANCELLED"] | None,
        Query(alias="status"),
    ] = None,
    mode: Annotated[Literal["CHAT", "PLAYGROUND"] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    return await repository.list_traces(
        knowledge_id=knowledge_id,
        status=trace_status,
        mode=mode,
        limit=limit,
    )


@router.get("/traces/{trace_id}", response_model=RagTraceResponse, tags=["traces"])
async def get_trace(
    trace_id: UUID,
    repository: Annotated[RagRepository, Depends(get_rag_repository)],
) -> dict[str, Any]:
    return await repository.get_trace(trace_id)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[MessageResponse],
    tags=["conversations"],
)
async def list_messages(
    conversation_id: UUID,
    repository: Annotated[RagRepository, Depends(get_rag_repository)],
) -> list[dict[str, Any]]:
    return await repository.list_messages(conversation_id)
