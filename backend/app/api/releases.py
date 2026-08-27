from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies import get_app_settings, get_resources
from app.api.schemas import (
    DocumentProfilePatchRequest,
    KnowledgeProfileSummaryResponse,
    ProfilePublishCheckResponse,
    TaskSubmissionResponse,
)
from app.core.config import Settings
from app.core.resources import AppResources
from app.document_profiles.repository import DocumentProfileRepository
from app.document_profiles.service import DocumentProfileService

router = APIRouter(tags=["document-profiles"])


def get_document_profile_repository(
    resources: Annotated[AppResources, Depends(get_resources)],
) -> DocumentProfileRepository:
    return DocumentProfileRepository(resources.database)


def get_document_profile_service(
    repository: Annotated[DocumentProfileRepository, Depends(get_document_profile_repository)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> DocumentProfileService:
    return DocumentProfileService(repository, settings)


@router.get(
    "/knowledge-bases/{knowledge_id}/profiles",
    response_model=KnowledgeProfileSummaryResponse,
)
async def list_document_profiles(
    knowledge_id: UUID,
    service: Annotated[DocumentProfileService, Depends(get_document_profile_service)],
) -> dict[str, Any]:
    return await service.summary(knowledge_id)


@router.post(
    "/knowledge-bases/{knowledge_id}/profiles/generate",
    response_model=TaskSubmissionResponse,
    status_code=202,
)
async def generate_document_profiles(
    knowledge_id: UUID,
    service: Annotated[DocumentProfileService, Depends(get_document_profile_service)],
) -> dict[str, Any]:
    return await service.request_generation(knowledge_id)


@router.post(
    "/knowledge-bases/{knowledge_id}/documents/{document_id}/profile/generate",
    response_model=TaskSubmissionResponse,
    status_code=202,
)
async def generate_document_profile(
    knowledge_id: UUID,
    document_id: UUID,
    service: Annotated[DocumentProfileService, Depends(get_document_profile_service)],
) -> dict[str, Any]:
    return await service.request_generation(knowledge_id, document_id=document_id)


@router.put("/knowledge-bases/{knowledge_id}/documents/{document_id}/profile")
async def update_document_profile(
    knowledge_id: UUID,
    document_id: UUID,
    payload: DocumentProfilePatchRequest,
    service: Annotated[DocumentProfileService, Depends(get_document_profile_service)],
) -> dict[str, Any]:
    return await service.edit(knowledge_id, document_id, payload)


@router.post(
    "/knowledge-bases/{knowledge_id}/profiles/check",
    response_model=ProfilePublishCheckResponse,
)
async def check_document_profiles(
    knowledge_id: UUID,
    service: Annotated[DocumentProfileService, Depends(get_document_profile_service)],
) -> dict[str, Any]:
    return await service.check(knowledge_id)


@router.post("/knowledge-bases/{knowledge_id}/profiles/publish")
async def publish_document_profiles(
    knowledge_id: UUID,
    service: Annotated[DocumentProfileService, Depends(get_document_profile_service)],
) -> dict[str, Any]:
    return await service.publish(knowledge_id)


@router.post("/knowledge-bases/{knowledge_id}/profiles/discard-pending")
async def discard_pending_document_profiles(
    knowledge_id: UUID,
    repository: Annotated[DocumentProfileRepository, Depends(get_document_profile_repository)],
) -> dict[str, Any]:
    discarded_count = await repository.discard_pending(knowledge_id)
    return {"knowledge_id": str(knowledge_id), "discarded_count": discarded_count}
