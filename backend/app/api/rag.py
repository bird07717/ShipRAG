from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse

from app.api.dependencies import get_app_settings, get_resources
from app.api.engine_dependencies import get_rag_repository, get_rag_service
from app.api.schemas import (
    ApiKeyUpdate,
    ChatRequest,
    ChatResponse,
    ChatStreamRequest,
    EngineRetrievalConfigResponse,
    EngineRetrievalConfigUpdate,
    MessageResponse,
    ModelConfigResponse,
    ModelConfigUpdate,
    ModelProbeResponse,
    PlaygroundRequest,
    PromptResponse,
    RagConfigResponse,
    RagConfigUpdate,
    RagTraceResponse,
    TraceSummaryResponse,
)
from app.common.errors import ApiError
from app.core.config import Settings
from app.core.resources import AppResources
from app.observability.recorder import observe_stream_errors
from app.rag.model_probe import probe_model
from app.rag.prompt import runtime_prompt_entries
from app.rag.repository import RagRepository
from app.services.rag import RagService

router = APIRouter()


@router.post("/rag/chat/stream", tags=["rag"])
async def rag_chat_stream(
    payload: ChatStreamRequest,
    request: Request,
    service: Annotated[RagService, Depends(get_rag_service)],
) -> StreamingResponse:
    return StreamingResponse(
        observe_stream_errors(
            request,
            service.chat_stream(
                knowledge_id=payload.knowledge_id,
                conversation_id=payload.conversation_id,
                question=payload.question,
                request_id=payload.request_id or str(request.state.request_id),
                product_name="",
                scope_description="本产品相关的问题，包括参数、功能、使用步骤、维护和故障排查",
                user_id=payload.user_id,
            ),
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/rag/chat", response_model=ChatResponse, tags=["rag"])
async def rag_chat(
    payload: ChatRequest,
    request: Request,
    service: Annotated[RagService, Depends(get_rag_service)],
) -> dict[str, Any]:
    return await service.generate(
        knowledge_id=payload.knowledge_id,
        conversation_id=payload.conversation_id,
        question=payload.question,
        request_id=payload.request_id or str(request.state.request_id),
        product_name="",
        scope_description="本产品相关的问题，包括参数、功能、使用步骤、维护和故障排查",
        user_id=payload.user_id,
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
    # Runtime (code-managed, main chat path) templates come first; database
    # rows only serve the chunk-RAG fallback path.
    return runtime_prompt_entries() + await repository.list_prompts()


@router.get("/models", response_model=list[ModelConfigResponse], tags=["models"])
async def list_models(
    repository: Annotated[RagRepository, Depends(get_rag_repository)],
) -> list[dict[str, Any]]:
    return await repository.list_models()


@router.patch("/models/{model_id}", response_model=ModelConfigResponse, tags=["models"])
async def update_model(
    model_id: UUID,
    payload: ModelConfigUpdate,
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
    repository: Annotated[RagRepository, Depends(get_rag_repository)],
) -> dict[str, Any]:
    # Dimension contract gate: changing an EMBEDDING row's model or endpoint
    # must be verified against the frozen dimension (1024) BEFORE commit.
    # The probe runs with the candidate values + effective key, so a config
    # that would poison the next index build is rejected up front.
    if payload.model_name is not None or payload.base_url is not None:
        current = await repository.read_model_with_key(model_id)
        if str(current["model_type"]) == "EMBEDDING":
            candidate_name = payload.model_name or str(current["model_name"])
            candidate_base_url = payload.base_url or (
                str(current["base_url"]) if current["base_url"] is not None else None
            )
            if candidate_name != str(current["model_name"]) or candidate_base_url != (
                str(current["base_url"]) if current["base_url"] is not None else None
            ):
                probe = await probe_model(
                    settings,
                    model_type="EMBEDDING",
                    provider=str(current["provider"]),
                    model_name=candidate_name,
                    base_url=candidate_base_url,
                    api_key=current.get("api_key"),
                )
                if not probe["ok"]:
                    code = (
                        "EMBEDDING_DIMENSION_MISMATCH"
                        if probe.get("dimension") is not None or "维度" in str(probe["detail"])
                        else "MODEL_PROBE_FAILED"
                    )
                    raise ApiError(
                        code,
                        f"维度契约门禁未通过：{probe['detail']}",
                        422 if code == "EMBEDDING_DIMENSION_MISMATCH" else 409,
                    )
    # begin_turn snapshots model_config every turn, so the committed values
    # affect the next chat request without a restart.
    return await repository.update_model_config(
        model_id,
        model_name=payload.model_name,
        base_url=payload.base_url,
        parameters=payload.parameters,
        enabled=payload.enabled,
        actor=str(request.state.request_id),
    )


@router.put("/models/{model_id}/api-key", response_model=ModelConfigResponse, tags=["models"])
async def set_model_api_key(
    model_id: UUID,
    payload: ApiKeyUpdate,
    request: Request,
    repository: Annotated[RagRepository, Depends(get_rag_repository)],
) -> dict[str, Any]:
    # Write-only: the key is encrypted at rest (ADR-0005) and is never
    # readable through the API. DB key wins over the env setting.
    return await repository.set_api_key(
        model_id, payload.api_key, actor=str(request.state.request_id)
    )


@router.delete("/models/{model_id}/api-key", response_model=ModelConfigResponse, tags=["models"])
async def clear_model_api_key(
    model_id: UUID,
    request: Request,
    repository: Annotated[RagRepository, Depends(get_rag_repository)],
) -> dict[str, Any]:
    # Clearing falls back to the env-injected key (bootstrap default).
    return await repository.clear_api_key(model_id, actor=str(request.state.request_id))


@router.post("/models/{model_id}/probe", response_model=ModelProbeResponse, tags=["models"])
async def probe_model_config(
    model_id: UUID,
    settings: Annotated[Settings, Depends(get_app_settings)],
    repository: Annotated[RagRepository, Depends(get_rag_repository)],
) -> dict[str, Any]:
    row = await repository.read_model_with_key(model_id)
    return await probe_model(
        settings,
        model_type=str(row["model_type"]),
        provider=str(row["provider"]),
        model_name=str(row["model_name"]),
        base_url=(str(row["base_url"]) if row["base_url"] is not None else None),
        api_key=row.get("api_key"),
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


@router.get(
    "/engine-retrieval-configs",
    response_model=list[EngineRetrievalConfigResponse],
    tags=["rag-config"],
)
async def list_engine_retrieval_configs(
    repository: Annotated[RagRepository, Depends(get_rag_repository)],
) -> list[dict[str, Any]]:
    return await repository.list_engine_retrieval_configs()


@router.patch(
    "/engine-retrieval-configs/{execution_policy}",
    response_model=EngineRetrievalConfigResponse,
    tags=["rag-config"],
)
async def update_engine_retrieval_config(
    execution_policy: Literal["SUPPORT_ONLY", "RAG_ONLY", "SUPPORT_WITH_RAG_FALLBACK"],
    payload: EngineRetrievalConfigUpdate,
    repository: Annotated[RagRepository, Depends(get_rag_repository)],
) -> dict[str, Any]:
    return await repository.update_engine_retrieval_config(
        execution_policy,
        **payload.model_dump(exclude_none=True),
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
    response_type: Annotated[
        Literal[
            "ANSWERED",
            "UNCONFIRMED",
            "OUT_OF_SCOPE",
            "DOC_DELIVERED",
            "DOCUMENT_DELIVERED",
            "CLARIFICATION_REQUIRED",
            "NEED_MORE_INFORMATION",
            "NO_MATCH",
            "ESCALATION_REQUIRED",
        ]
        | None,
        Query(),
    ] = None,
    engine: Annotated[Literal["SUPPORT", "RAG"] | None, Query()] = None,
    user_id: Annotated[str | None, Query(max_length=100)] = None,
    error_code: Annotated[str | None, Query(max_length=100)] = None,
    conversation_id: Annotated[UUID | None, Query()] = None,
    created_from: Annotated[datetime | None, Query(alias="from")] = None,
    created_to: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    return await repository.list_traces(
        knowledge_id=knowledge_id,
        status=trace_status,
        mode=mode,
        user_id=user_id,
        response_type=response_type,
        engine=engine,
        error_code=error_code,
        conversation_id=conversation_id,
        created_from=created_from,
        created_to=created_to,
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
