from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.api.dependencies import get_app_settings, get_resources
from app.core.config import Settings
from app.core.resources import AppResources
from app.gateway.repository import GatewayRepository
from app.gateway.service import ChatGatewayService
from app.rag.repository import RagRepository
from app.services.rag import RagService
from app.support.repository import SupportRepository
from app.support.service import TechnicalSupportService


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


def get_support_repository(
    settings: Annotated[Settings, Depends(get_app_settings)],
    resources: Annotated[AppResources, Depends(get_resources)],
) -> SupportRepository:
    return SupportRepository(resources.database, settings)


def get_support_service(
    repository: Annotated[SupportRepository, Depends(get_support_repository)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> TechnicalSupportService:
    return TechnicalSupportService(repository, settings)


def get_gateway_repository(
    settings: Annotated[Settings, Depends(get_app_settings)],
    resources: Annotated[AppResources, Depends(get_resources)],
) -> GatewayRepository:
    return GatewayRepository(resources.database, settings)


def get_chat_gateway(
    repository: Annotated[GatewayRepository, Depends(get_gateway_repository)],
    support: Annotated[TechnicalSupportService, Depends(get_support_service)],
    rag: Annotated[RagService, Depends(get_rag_service)],
) -> ChatGatewayService:
    return ChatGatewayService(repository, support, rag)
