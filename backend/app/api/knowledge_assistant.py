"""Knowledge-base admin controls, protected by the management credential."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.engine_dependencies import get_gateway_repository
from app.api.schemas import (
    KnowledgeAssistantCheck,
    KnowledgeAssistantConfig,
    KnowledgeAssistantPublication,
    KnowledgeAssistantUpdate,
)
from app.assistant.configuration import AssistantConfiguration
from app.gateway.repository import GatewayRepository

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


def get_assistant_configuration(
    gateway: Annotated[GatewayRepository, Depends(get_gateway_repository)],
) -> AssistantConfiguration:
    return AssistantConfiguration(gateway)


@router.get("/{knowledge_id}/assistant-config", response_model=KnowledgeAssistantCheck)
async def get_config(
    knowledge_id: UUID,
    config: Annotated[AssistantConfiguration, Depends(get_assistant_configuration)],
) -> dict[str, Any]:
    return await config.get(knowledge_id)


@router.put("/{knowledge_id}/assistant-config", response_model=KnowledgeAssistantConfig)
async def save_config(
    knowledge_id: UUID,
    payload: KnowledgeAssistantUpdate,
    config: Annotated[AssistantConfiguration, Depends(get_assistant_configuration)],
) -> dict[str, Any]:
    return await config.update(
        knowledge_id, revision=payload.revision, suggestions=payload.suggestions
    )


@router.put("/{knowledge_id}/assistant-publication", response_model=KnowledgeAssistantConfig)
async def set_publication(
    knowledge_id: UUID,
    payload: KnowledgeAssistantPublication,
    config: Annotated[AssistantConfiguration, Depends(get_assistant_configuration)],
) -> dict[str, Any]:
    return await config.update(
        knowledge_id, revision=payload.revision, online=payload.assistant_online
    )
