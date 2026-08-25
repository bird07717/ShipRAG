from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_resources
from app.api.schemas import (
    FailureSummaryResponse,
    RequestErrorLogListResponse,
    RequestErrorLogResponse,
)
from app.core.resources import AppResources
from app.observability.repository import ObservabilityRepository

router = APIRouter(tags=["logs"])


def get_observability_repository(
    resources: Annotated[AppResources, Depends(get_resources)],
) -> ObservabilityRepository:
    return ObservabilityRepository(resources.database)


@router.get(
    "/logs/request-errors",
    response_model=RequestErrorLogListResponse,
    tags=["logs"],
)
async def list_request_errors(
    repository: Annotated[ObservabilityRepository, Depends(get_observability_repository)],
    request_id: Annotated[str | None, Query(max_length=128)] = None,
    path: Annotated[str | None, Query(max_length=200)] = None,
    status_code: Annotated[int | None, Query(ge=400, le=599)] = None,
    created_from: Annotated[datetime | None, Query(alias="from")] = None,
    created_to: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    return await repository.list_request_errors(
        request_id=request_id,
        path=path,
        status_code=status_code,
        created_from=created_from,
        created_to=created_to,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/logs/request-errors/{error_id}",
    response_model=RequestErrorLogResponse,
    tags=["logs"],
)
async def get_request_error(
    error_id: UUID,
    repository: Annotated[ObservabilityRepository, Depends(get_observability_repository)],
) -> dict[str, Any]:
    return await repository.get_request_error(error_id)


@router.get("/logs/failure-summary", response_model=FailureSummaryResponse, tags=["logs"])
async def failure_summary(
    repository: Annotated[ObservabilityRepository, Depends(get_observability_repository)],
    knowledge_id: Annotated[UUID | None, Query()] = None,
    created_from: Annotated[datetime | None, Query(alias="from")] = None,
    created_to: Annotated[datetime | None, Query(alias="to")] = None,
) -> dict[str, Any]:
    """Failure distribution across failed support turns and failed requests.

    The default window (last 7 days) and the 92-day ceiling match /metrics so
    the dashboard and the review pages cannot disagree about the range.
    """
    return await repository.failure_summary(
        created_from=created_from,
        created_to=created_to,
        knowledge_id=knowledge_id,
    )
