from __future__ import annotations

from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse

from app.api.dependencies import get_app_settings, get_resources
from app.api.schemas import (
    ChatRequest,
    ChatResponse,
    ChatStreamRequest,
    MessageResponse,
    ModelConfigResponse,
    ModelConfigUpdate,
    PlaygroundRequest,
    PromptResponse,
    RagConfigResponse,
    RagConfigUpdate,
    RagTraceResponse,
    TraceSummaryResponse,
    WelcomeResponse,
)
from app.core.config import Settings
from app.core.resources import AppResources
from app.rag.repository import RagRepository
from app.rag.welcome import build_welcome
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


@router.get("/chat/welcome", response_model=WelcomeResponse, tags=["chat"])
async def chat_welcome(
    knowledge_id: UUID,
    repository: Annotated[RagRepository, Depends(get_rag_repository)],
) -> dict[str, Any]:
    kb = await repository.get_knowledge_base_summary(knowledge_id)
    catalog = await repository.list_kb_documents(knowledge_id)
    return build_welcome(kb, catalog)


@router.post("/chat/stream", tags=["chat"])
async def chat_stream(
    payload: ChatStreamRequest,
    request: Request,
    service: Annotated[RagService, Depends(get_rag_service)],
) -> StreamingResponse:
    return StreamingResponse(
        service.chat_stream(
            knowledge_id=payload.knowledge_id,
            conversation_id=payload.conversation_id,
            question=payload.question,
            request_id=str(request.state.request_id),
            product_name="",
            scope_description="本产品相关的问题，包括参数、功能、使用步骤、维护和故障排查",
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat", response_model=ChatResponse, tags=["chat"])
async def chat(
    payload: ChatRequest,
    request: Request,
    service: Annotated[RagService, Depends(get_rag_service)],
) -> dict[str, Any]:
    return await service.generate(
        knowledge_id=payload.knowledge_id,
        conversation_id=payload.conversation_id,
        question=payload.question,
        request_id=str(request.state.request_id),
        product_name="",
        scope_description="本产品相关的问题，包括参数、功能、使用步骤、维护和故障排查",
    )


@router.get("/documents/{document_id}/content", tags=["documents"])
async def download_document(
    document_id: UUID,
    repository: Annotated[RagRepository, Depends(get_rag_repository)],
    resources: Annotated[AppResources, Depends(get_resources)],
) -> Response:
    import asyncio

    doc = await repository.get_document_source(document_id)

    def download() -> bytes:
        upstream = resources.minio.get_object(doc["minio_bucket"], doc["minio_object_key"])
        try:
            return bytes(upstream.read())
        finally:
            upstream.close()
            upstream.release_conn()

    content = await asyncio.to_thread(download)
    filename = doc["display_name"] or doc["filename"]
    safe = filename.replace(chr(34), "").replace(chr(13), "").replace(chr(10), "")
    fallback = safe if safe.isascii() else "download.docx"
    disposition = (
        "attachment; filename=" + chr(34) + fallback + chr(34) + "; filename*=UTF-8''" + quote(safe)
    )
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": disposition,
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
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


@router.patch("/models/{model_id}", response_model=ModelConfigResponse, tags=["models"])
async def update_model(
    model_id: UUID,
    payload: ModelConfigUpdate,
    repository: Annotated[RagRepository, Depends(get_rag_repository)],
) -> dict[str, Any]:
    # begin_turn snapshots model_config every turn, so the committed values
    # affect the next chat request without a restart.
    return await repository.update_model_config(
        model_id,
        model_name=payload.model_name,
        base_url=payload.base_url,
        parameters=payload.parameters,
        enabled=payload.enabled,
    )


@router.get("/rag-config", response_model=RagConfigResponse, tags=["rag-config"])
async def get_rag_config(
    repository: Annotated[RagRepository, Depends(get_rag_repository)],
) -> dict[str, Any]:
    return await repository.get_rag_config()


@router.patch("/rag-config", response_model=RagConfigResponse, tags=["rag-config"])
async def update_rag_config(
    payload: RagConfigUpdate,
    repository: Annotated[RagRepository, Depends(get_rag_repository)],
) -> dict[str, Any]:
    return await repository.update_rag_config(
        vector_top_k=payload.vector_top_k,
        bm25_top_k=payload.bm25_top_k,
        fusion_top_k=payload.fusion_top_k,
        rerank_top_n=payload.rerank_top_n,
        context_max_chunks=payload.context_max_chunks,
    )


@router.post("/rag/playground", response_model=RagTraceResponse, tags=["playground"])
async def run_playground(
    payload: PlaygroundRequest,
    request: Request,
    repository: Annotated[RagRepository, Depends(get_rag_repository)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    resources: Annotated[AppResources, Depends(get_resources)],
) -> dict[str, Any]:
    options = payload.options
    retrieval_overrides = {
        field: value
        for field, value in {
            "vector_top_k": options.vector_top_k,
            "bm25_top_k": options.bm25_top_k,
            "fusion_top_k": options.fusion_top_k,
            "rerank_top_n": options.rerank_top_n,
            "context_max_chunks": options.context_max_chunks,
        }.items()
        if value is not None
    }
    playground_service = RagService(repository, settings, resources.minio)
    prepared = await playground_service.prepare(
        knowledge_id=payload.knowledge_id,
        conversation_id=payload.conversation_id,
        question=payload.question,
        request_id=str(request.state.request_id),
        mode="PLAYGROUND",
        retrieval_overrides=retrieval_overrides,
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
