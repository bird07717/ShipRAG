from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.pool import QueuePool

from app.api.dependencies import get_resources
from app.api.schemas import (
    AssistantMetricsResponse,
    MetricsLatencyResponse,
    MetricsOverviewResponse,
    MetricsTimeseriesPoint,
    RuntimeMetricsResponse,
    SupportMetricsResponse,
)
from app.common.errors import ApiError
from app.common.turn_gate import turn_gate_snapshot
from app.core.config import Settings, get_settings
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
    engine: Annotated[Literal["SUPPORT", "RAG"] | None, Query()] = None,
    source: Annotated[
        Literal["ASSISTANT_API", "INTERNAL_CHAT", "PLAYGROUND"] | None,
        Query(),
    ] = None,
    created_from: Annotated[str | None, Query(alias="from")] = None,
    created_to: Annotated[str | None, Query(alias="to")] = None,
) -> dict[str, Any]:
    # Defaults to the last 7 days. Quality-rate denominators use only the
    # explicit RAG response categories, excluding failed and unclassified
    # history so traffic composition cannot masquerade as answer quality.
    return await repository.overview(
        knowledge_id=knowledge_id,
        mode=mode,
        engine=engine,
        source=source,
        created_from=_parse_datetime(created_from),
        created_to=_parse_datetime(created_to),
    )


@router.get("/metrics/timeseries", response_model=list[MetricsTimeseriesPoint])
async def metrics_timeseries(
    repository: Annotated[MetricsRepository, Depends(get_metrics_repository)],
    knowledge_id: Annotated[UUID | None, Query()] = None,
    mode: Annotated[Literal["CHAT", "PLAYGROUND"], Query()] = "CHAT",
    engine: Annotated[Literal["SUPPORT", "RAG"] | None, Query()] = None,
    source: Annotated[
        Literal["ASSISTANT_API", "INTERNAL_CHAT", "PLAYGROUND"] | None,
        Query(),
    ] = None,
    granularity: Annotated[Literal["day", "hour"], Query()] = "day",
    created_from: Annotated[str | None, Query(alias="from")] = None,
    created_to: Annotated[str | None, Query(alias="to")] = None,
) -> list[dict[str, Any]]:
    return await repository.timeseries(
        knowledge_id=knowledge_id,
        mode=mode,
        granularity=granularity,
        engine=engine,
        source=source,
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


@router.get("/metrics/assistant", response_model=AssistantMetricsResponse)
async def assistant_metrics(
    repository: Annotated[MetricsRepository, Depends(get_metrics_repository)],
    settings: Annotated[Settings, Depends(get_settings)],
    days: Annotated[int, Query(ge=1, le=92)] = 7,
    knowledge_id: Annotated[UUID | None, Query()] = None,
) -> dict[str, Any]:
    """Operational panel for the online Assistant surface.

    Aggregates call-source mix, external user activity, turn/HTTP error
    rates, idempotency protection with replay hits, and conversation
    retention state — all from existing ledgers, scoped to the optional
    knowledge base.
    """
    return await repository.assistant(
        days=days,
        retention_days=settings.conversation_retention_days,
        knowledge_id=knowledge_id,
        stale_seconds=settings.assistant_request_stale_seconds,
    )


@router.get("/metrics/runtime", response_model=RuntimeMetricsResponse)
async def runtime_metrics(
    settings: Annotated[Settings, Depends(get_settings)],
    resources: Annotated[AppResources, Depends(get_resources)],
) -> dict[str, Any]:
    """Per-process runtime snapshot: turn-gate occupancy and DB pool state.

    Behind multiple uvicorn workers each instance reports only its own
    gate and pool; scrape per instance (or accept the mix) when sharded.
    """
    pool: dict[str, int] = {"size": 0, "checked_in": 0, "checked_out": 0, "overflow": 0}
    raw_pool = resources.database.pool
    if isinstance(raw_pool, QueuePool):
        pool.update(
            size=raw_pool.size(),
            checked_in=raw_pool.checkedin(),
            checked_out=raw_pool.checkedout(),
            overflow=raw_pool.overflow(),
        )
    return {
        "turn_gate": turn_gate_snapshot(settings),
        "database_pool": pool,
    }
