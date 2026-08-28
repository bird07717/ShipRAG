from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.engine_dependencies import get_gateway_repository
from app.api.schemas import (
    ConversationDeleteResponse,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationMessageListResponse,
)
from app.common.errors import ApiError
from app.gateway.repository import GatewayRepository

router = APIRouter()


def _require_visible(conversation: dict | None, user_id: str | None) -> dict:
    if conversation is None:
        raise ApiError("CONVERSATION_NOT_FOUND", "会话不存在", 404)
    owner = conversation.get("user_id")
    if user_id is not None and owner is not None and str(owner) != user_id:
        raise ApiError("CONVERSATION_NOT_FOUND", "会话不存在", 404)
    return conversation


@router.get(
    "/conversations",
    response_model=ConversationListResponse,
    tags=["conversations"],
)
async def list_conversations(
    knowledge_id: UUID,
    repository: Annotated[GatewayRepository, Depends(get_gateway_repository)],
    user_id: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    rows = await repository.list_conversations(knowledge_id, user_id, limit)
    return {"items": rows}


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetailResponse,
    tags=["conversations"],
)
async def get_conversation(
    conversation_id: UUID,
    repository: Annotated[GatewayRepository, Depends(get_gateway_repository)],
    user_id: Annotated[str | None, Query(max_length=100)] = None,
) -> dict:
    return _require_visible(await repository.get_conversation(conversation_id), user_id)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=ConversationMessageListResponse,
    tags=["conversations"],
)
async def list_conversation_messages(
    conversation_id: UUID,
    repository: Annotated[GatewayRepository, Depends(get_gateway_repository)],
    user_id: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict:
    _require_visible(await repository.get_conversation(conversation_id), user_id)
    rows = await repository.list_conversation_messages(conversation_id, limit)
    return {"items": rows}


@router.delete(
    "/conversations/{conversation_id}",
    response_model=ConversationDeleteResponse,
    tags=["conversations"],
)
async def delete_conversation(
    conversation_id: UUID,
    repository: Annotated[GatewayRepository, Depends(get_gateway_repository)],
    user_id: Annotated[str | None, Query(max_length=100)] = None,
) -> dict:
    _require_visible(await repository.get_conversation(conversation_id), user_id)
    deleted = await repository.delete_conversation(conversation_id)
    return {"conversation_id": conversation_id, "deleted": deleted}
