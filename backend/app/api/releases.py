from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_app_settings, get_resources
from app.api.schemas import TaskSubmissionResponse
from app.core.config import Settings
from app.core.resources import AppResources
from app.releases.models import ProfileDraftPatch
from app.releases.repository import ReleaseRepository
from app.releases.service import ReleaseService

router = APIRouter(tags=["knowledge-releases"])


def get_release_repository(
    resources: Annotated[AppResources, Depends(get_resources)],
) -> ReleaseRepository:
    return ReleaseRepository(resources.database)


def get_release_service(
    repository: Annotated[ReleaseRepository, Depends(get_release_repository)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> ReleaseService:
    return ReleaseService(repository, settings)


@router.get("/knowledge-bases/{knowledge_id}/releases")
async def list_releases(
    knowledge_id: UUID,
    repository: Annotated[ReleaseRepository, Depends(get_release_repository)],
) -> list[dict[str, Any]]:
    return await repository.list_releases(knowledge_id)


@router.get("/releases/{release_id}")
async def get_release(
    release_id: UUID,
    repository: Annotated[ReleaseRepository, Depends(get_release_repository)],
) -> dict[str, Any]:
    return await repository.get_release(release_id)


@router.post(
    "/indexes/{index_id}/profile-drafts/generate",
    response_model=TaskSubmissionResponse,
    status_code=202,
)
async def generate_profile_drafts(
    index_id: UUID,
    service: Annotated[ReleaseService, Depends(get_release_service)],
) -> dict[str, Any]:
    return await service.request_profile_task(index_id, task_type="PROFILE_GENERATE")


@router.post(
    "/indexes/{index_id}/profile-drafts/backfill",
    response_model=TaskSubmissionResponse,
    status_code=202,
)
async def backfill_profile_drafts(
    index_id: UUID,
    service: Annotated[ReleaseService, Depends(get_release_service)],
) -> dict[str, Any]:
    """Generate only Profiles that are missing the current enrichment version."""
    return await service.request_profile_task(index_id, task_type="PROFILE_BACKFILL")


@router.post(
    "/indexes/{index_id}/profile-drafts/refresh",
    response_model=TaskSubmissionResponse,
    status_code=202,
)
async def refresh_profile_drafts(
    index_id: UUID,
    service: Annotated[ReleaseService, Depends(get_release_service)],
) -> dict[str, Any]:
    """Regenerate drafts for all documents in an editable candidate."""
    return await service.request_profile_task(index_id, task_type="PROFILE_REFRESH")


@router.post(
    "/profile-drafts/{draft_id}/regenerate",
    response_model=TaskSubmissionResponse,
    status_code=202,
)
async def regenerate_profile_draft(
    draft_id: UUID,
    service: Annotated[ReleaseService, Depends(get_release_service)],
) -> dict[str, Any]:
    return await service.request_regenerate_profile_task(draft_id)


@router.post("/releases/{release_id}/profiles/{profile_id}/draft")
async def create_profile_edit_draft(
    release_id: UUID,
    profile_id: UUID,
    repository: Annotated[ReleaseRepository, Depends(get_release_repository)],
) -> dict[str, Any]:
    """Copy a live Profile into an UPDATE draft in the selected candidate."""
    return await repository.create_draft_from_live(
        release_id=release_id,
        profile_id=profile_id,
    )


@router.patch("/profile-drafts/{draft_id}")
async def update_profile_draft(
    draft_id: UUID,
    payload: ProfileDraftPatch,
    service: Annotated[ReleaseService, Depends(get_release_service)],
) -> dict[str, Any]:
    return await service.update_draft(draft_id, payload)


@router.post("/releases/{release_id}/validate")
async def validate_release(
    release_id: UUID,
    service: Annotated[ReleaseService, Depends(get_release_service)],
) -> dict[str, Any]:
    return await service.validate(release_id)


@router.post("/releases/{release_id}/publish")
async def publish_release(
    release_id: UUID,
    service: Annotated[ReleaseService, Depends(get_release_service)],
) -> dict[str, Any]:
    return await service.publish(release_id)


@router.get("/knowledge-bases/{knowledge_id}/profile-feedback")
async def profile_feedback(
    knowledge_id: UUID,
    repository: Annotated[ReleaseRepository, Depends(get_release_repository)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[dict[str, Any]]:
    return await repository.feedback(knowledge_id, limit)
