from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import SecretStr

from app.api.dependencies import get_app_settings, get_resources
from app.api.engine_dependencies import get_rag_repository, get_rag_service
from app.api.ingestion import get_ingestion_service
from app.api.pagination import OffsetQuery, paged_total, set_total_count
from app.api.schemas import (
    ApiKeyUpdate,
    ChatRequest,
    ChatResponse,
    ChatStreamRequest,
    EngineRetrievalConfigResponse,
    EngineRetrievalConfigUpdate,
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
from app.common.assets import attachment_content_disposition
from app.common.errors import ApiError
from app.core.config import Settings
from app.core.resources import AppResources
from app.observability.recorder import observe_stream_errors
from app.rag.model_probe import probe_model
from app.rag.model_providers import is_supported_provider, supported_providers
from app.rag.prompt import runtime_prompt_entries
from app.rag.repository import RagRepository
from app.services.ingestion import IngestionService
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
    disposition = attachment_content_disposition(filename)
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
    # Runtime (code-managed, RAG V2) snapshots come first. Database rows remain
    # available for administration and historical inspection, but do not drive V2.
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
    ingestion_service: Annotated[IngestionService, Depends(get_ingestion_service)],
) -> dict[str, Any]:
    # Provider, model id, endpoint and API key form an atomic runtime
    # identity.  Probe the candidate before storing it so an incompatible
    # provider/key/endpoint combination cannot break chat or profile jobs.
    candidate_input_supplied = any(
        value is not None
        for value in (payload.provider, payload.model_name, payload.base_url, payload.api_key)
    )
    embedding_identity_changed = False
    if candidate_input_supplied:
        current = await repository.read_model_with_key(model_id)
        model_type = str(current["model_type"])
        candidate_provider = payload.provider or str(current["provider"])
        if not is_supported_provider(model_type, candidate_provider):
            supported = "、".join(supported_providers(model_type))
            raise ApiError(
                "MODEL_PROVIDER_UNSUPPORTED",
                f"{model_type} 不支持 Provider：{candidate_provider}（支持：{supported}）",
                422,
            )
        candidate_name = payload.model_name or str(current["model_name"])
        candidate_base_url = payload.base_url or (
            str(current["base_url"]) if current["base_url"] is not None else None
        )
        candidate_key = (
            SecretStr(payload.api_key) if payload.api_key is not None else current.get("api_key")
        )
        embedding_identity_changed = model_type == "EMBEDDING" and (
            candidate_provider != str(current["provider"])
            or candidate_name != str(current["model_name"])
            or candidate_base_url
            != (str(current["base_url"]) if current["base_url"] is not None else None)
        )
        identity_changed = (
            candidate_provider != str(current["provider"])
            or candidate_name != str(current["model_name"])
            or candidate_base_url
            != (str(current["base_url"]) if current["base_url"] is not None else None)
        )
        if embedding_identity_changed and not payload.rebuild_all_confirmed:
            raise ApiError(
                "EMBEDDING_REBUILD_REQUIRED",
                "修改 Embedding Provider、模型或 Base URL 会改变向量空间："
                "必须确认对所有知识库执行 RAG 全量重建",
                409,
            )
        if identity_changed or payload.api_key is not None:
            probe = await probe_model(
                settings,
                model_type=model_type,
                provider=candidate_provider,
                model_name=candidate_name,
                base_url=candidate_base_url,
                api_key=candidate_key,
            )
            if not probe["ok"]:
                code = (
                    "EMBEDDING_DIMENSION_MISMATCH"
                    if model_type == "EMBEDDING"
                    and (probe.get("dimension") is not None or "维度" in str(probe["detail"]))
                    else "MODEL_PROBE_FAILED"
                )
                raise ApiError(
                    code,
                    f"模型配置连通性校验未通过：{probe['detail']}",
                    422 if code == "EMBEDDING_DIMENSION_MISMATCH" else 409,
                )
    # begin_turn snapshots model_config every turn, so the committed values
    # affect the next chat request without a restart.
    result = await repository.update_model_config(
        model_id,
        provider=payload.provider,
        model_name=payload.model_name,
        base_url=payload.base_url,
        api_key=payload.api_key,
        parameters=payload.parameters,
        enabled=payload.enabled,
        actor=str(request.state.request_id),
    )
    if embedding_identity_changed:
        # Policy: an EMBEDDING identity change invalidates every index's
        # vector space.  Confirming the edit means paying for a full fleet
        # rebuild immediately, not remembering to click "build" later.
        rebuilds = await ingestion_service.request_rebuild_all(reason="EMBEDDING_MODEL_CHANGED")
        result = {**result, "rebuilds": rebuilds}
    return result


@router.put("/models/{model_id}/api-key", response_model=ModelConfigResponse, tags=["models"])
async def set_model_api_key(
    model_id: UUID,
    payload: ApiKeyUpdate,
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
    repository: Annotated[RagRepository, Depends(get_rag_repository)],
) -> dict[str, Any]:
    # Validate the new credential against the current provider/model before
    # committing it.  The raw key remains request-local and is never returned.
    current = await repository.read_model_with_key(model_id)
    probe = await probe_model(
        settings,
        model_type=str(current["model_type"]),
        provider=str(current["provider"]),
        model_name=str(current["model_name"]),
        base_url=(str(current["base_url"]) if current["base_url"] is not None else None),
        api_key=SecretStr(payload.api_key),
    )
    if not probe["ok"]:
        raise ApiError("MODEL_PROBE_FAILED", f"API 密钥校验未通过：{probe['detail']}", 409)
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
    execution_policy: Literal["RAG_ONLY", "SUPPORT_WITH_RAG_FALLBACK"],
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
    response: Response,
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
    source: Annotated[
        Literal["ASSISTANT_API", "INTERNAL_CHAT", "PLAYGROUND"] | None, Query()
    ] = None,
    user_id: Annotated[str | None, Query(max_length=100)] = None,
    request_id: Annotated[str | None, Query(max_length=200)] = None,
    error_code: Annotated[str | None, Query(max_length=100)] = None,
    conversation_id: Annotated[UUID | None, Query()] = None,
    exclude_low_information: Annotated[bool, Query()] = False,
    created_from: Annotated[datetime | None, Query(alias="from")] = None,
    created_to: Annotated[datetime | None, Query(alias="to")] = None,
    question: Annotated[str | None, Query(alias="q", max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: OffsetQuery = 0,
) -> list[dict[str, Any]]:
    filters: dict[str, Any] = {
        "knowledge_id": knowledge_id,
        "status": trace_status,
        "mode": mode,
        "user_id": user_id,
        "response_type": response_type,
        "engine": engine,
        "source": source,
        "error_code": error_code,
        "conversation_id": conversation_id,
        "request_id": request_id,
        "question": question.strip() if question else None,
        "exclude_low_information": exclude_low_information,
        "created_from": created_from,
        "created_to": created_to,
    }
    rows = await repository.list_traces(limit=limit, offset=offset, **filters)
    # A short first page already is the whole result; skip the count query.
    total = await paged_total(
        rows, limit=limit, offset=offset, count=lambda: repository.count_traces(**filters)
    )
    set_total_count(response, total)
    return rows


@router.get("/traces/{trace_id}", response_model=RagTraceResponse, tags=["traces"])
async def get_trace(
    trace_id: UUID,
    repository: Annotated[RagRepository, Depends(get_rag_repository)],
) -> dict[str, Any]:
    return await repository.get_trace(trace_id)
