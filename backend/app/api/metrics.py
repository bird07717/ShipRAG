from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_resources
from app.api.schemas import (
    MetricsLatencyResponse,
    MetricsOverviewResponse,
    MetricsTimeseriesPoint,
    SupportMetricsResponse,
)
from app.common.errors import ApiError
from app.core.resources import AppResources
from app.rag.metrics import MetricsRepository

router = APIRouter(tags=["metrics"])


def get_metrics_repository(
    resources: Annotated[AppResources, Depends(get_resources)],
) -> MetricsRepository:
    return MetricsRepository(resources.database)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ApiError(
            "VALIDATION_ERROR", f"时间参数格式无效：{value}（需要 ISO 8601）", 422
        ) from exc


@router.get("/metrics/overview", response_model=MetricsOverviewResponse)
async def metrics_overview(
    repository: Annotated[MetricsRepository, Depends(get_metrics_repository)],
    knowledge_id: Annotated[UUID | None, Query()] = None,
    mode: Annotated[Literal["CHAT", "PLAYGROUND"], Query()] = "CHAT",
    created_from: Annotated[str | None, Query(alias="from")] = None,
    created_to: Annotated[str | None, Query(alias="to")] = None,
) -> dict[str, Any]:
    # Defaults to the last 7 days. Denominators exclude rows whose
    # response_type is NULL (pre-0014 history and failed turns), so rates
    # stay meaningful across the backfill boundary.
    return await repository.overview(
        knowledge_id=knowledge_id,
        mode=mode,
        created_from=_parse_datetime(created_from),
        created_to=_parse_datetime(created_to),
    )


@router.get("/metrics/timeseries", response_model=list[MetricsTimeseriesPoint])
async def metrics_timeseries(
    repository: Annotated[MetricsRepository, Depends(get_metrics_repository)],
    knowledge_id: Annotated[UUID | None, Query()] = None,
    mode: Annotated[Literal["CHAT", "PLAYGROUND"], Query()] = "CHAT",
    granularity: Annotated[Literal["day", "hour"], Query()] = "day",
    created_from: Annotated[str | None, Query(alias="from")] = None,
    created_to: Annotated[str | None, Query(alias="to")] = None,
) -> list[dict[str, Any]]:
    return await repository.timeseries(
        knowledge_id=knowledge_id,
        mode=mode,
        granularity=granularity,
        created_from=_parse_datetime(created_from),
        created_to=_parse_datetime(created_to),
    )


@router.get("/metrics/latency", response_model=MetricsLatencyResponse)
async def metrics_latency(
    repository: Annotated[MetricsRepository, Depends(get_metrics_repository)],
    knowledge_id: Annotated[UUID | None, Query()] = None,
    mode: Annotated[Literal["CHAT", "PLAYGROUND"], Query()] = "CHAT",
    created_from: Annotated[str | None, Query(alias="from")] = None,
    created_to: Annotated[str | None, Query(alias="to")] = None,
) -> dict[str, Any]:
    return await repository.latency(
        knowledge_id=knowledge_id,
        mode=mode,
        created_from=_parse_datetime(created_from),
        created_to=_parse_datetime(created_to),
    )


@router.get("/metrics/support", response_model=SupportMetricsResponse)
async def support_metrics(
    repository: Annotated[MetricsRepository, Depends(get_metrics_repository)],
    knowledge_id: Annotated[UUID | None, Query()] = None,
    created_from: Annotated[str | None, Query(alias="from")] = None,
    created_to: Annotated[str | None, Query(alias="to")] = None,
) -> dict[str, Any]:
    """Return operational statistics for formal technical-support turns."""
    return await repository.support(
        knowledge_id=knowledge_id,
        created_from=_parse_datetime(created_from),
        created_to=_parse_datetime(created_to),
    )
