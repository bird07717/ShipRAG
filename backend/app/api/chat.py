from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse

from app.api.engine_dependencies import get_chat_gateway, get_gateway_repository, get_rag_repository
from app.api.schemas import (
    ChatRequest,
    ChatResponse,
    ChatStreamRequest,
    RuntimePolicyResponse,
    RuntimePolicyUpdate,
    WelcomeResponse,
)
from app.gateway.models import ExecutionPolicy
from app.gateway.repository import GatewayRepository
from app.gateway.service import ChatGatewayService
from app.observability.recorder import observe_stream_errors
from app.rag.repository import RagRepository
from app.rag.welcome import build_welcome

router = APIRouter()


@router.get("/chat/welcome", response_model=WelcomeResponse, tags=["chat"])
async def chat_welcome(
    knowledge_id: UUID,
    repository: Annotated[RagRepository, Depends(get_rag_repository)],
) -> dict[str, Any]:
    kb = await repository.get_knowledge_base_summary(knowledge_id)
    catalog = await repository.list_kb_documents(knowledge_id)
    return build_welcome(kb, catalog)


@router.post("/chat", response_model=ChatResponse, tags=["chat"])
async def chat(
    payload: ChatRequest,
    request: Request,
    gateway: Annotated[ChatGatewayService, Depends(get_chat_gateway)],
    repository: Annotated[GatewayRepository, Depends(get_gateway_repository)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=200)] = None,
) -> dict[str, Any]:
    policy = await repository.resolve_policy(
        knowledge_id=payload.knowledge_id,
        conversation_id=payload.conversation_id,
    )
    return await gateway.handle(
        policy=policy,
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


@router.post("/chat/stream", tags=["chat"])
async def chat_stream(
    payload: ChatStreamRequest,
    request: Request,
    gateway: Annotated[ChatGatewayService, Depends(get_chat_gateway)],
    repository: Annotated[GatewayRepository, Depends(get_gateway_repository)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=200)] = None,
) -> StreamingResponse:
    policy = await repository.resolve_policy(
        knowledge_id=payload.knowledge_id,
        conversation_id=payload.conversation_id,
    )
    return StreamingResponse(
        observe_stream_errors(
            request,
            gateway.stream(
                policy=policy,
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
            user_facing=True,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/runtime-policies/{knowledge_id}",
    response_model=RuntimePolicyResponse,
    tags=["runtime-policy"],
)
async def get_runtime_policy(
    knowledge_id: UUID,
    repository: Annotated[GatewayRepository, Depends(get_gateway_repository)],
) -> dict[str, Any]:
    return await repository.get_policy(knowledge_id)


@router.put(
    "/runtime-policies/{knowledge_id}",
    response_model=RuntimePolicyResponse,
    tags=["runtime-policy"],
)
async def update_runtime_policy(
    knowledge_id: UUID,
    payload: RuntimePolicyUpdate,
    repository: Annotated[GatewayRepository, Depends(get_gateway_repository)],
) -> dict[str, Any]:
    return await repository.update_policy(knowledge_id, ExecutionPolicy(payload.execution_policy))
