from __future__ import annotations

import asyncio
from typing import Any

from redis import Redis
from rq import Queue

from app.core.config import Settings, get_settings
from app.core.resources import AppResources
from app.observability.repository import ObservabilityRepository

_DAILY_GATE_KEY = "rq:request-error-log-retention:daily-gate"
_RETENTION_INTERVAL_SECONDS = 86_400
_JOB_TIMEOUT_SECONDS = 600


async def _run_prune(settings: Settings) -> dict[str, Any]:
    if settings.request_error_log_retention_days <= 0:
        return {"deleted_count": 0}
    resources = AppResources.create(settings)
    repository = ObservabilityRepository(resources.database)
    try:
        deleted = await repository.delete_expired(
            retention_days=settings.request_error_log_retention_days
        )
        return {"deleted_count": deleted}
    finally:
        await resources.close()


def run_request_error_log_retention_sync(settings: Settings) -> dict[str, Any]:
    return asyncio.run(_run_prune(settings))


def mark_request_error_log_retention_started(
    connection: Redis,
    interval_seconds: int = _RETENTION_INTERVAL_SECONDS,
) -> None:
    """Delay the next check after the startup prune."""
    connection.set(_DAILY_GATE_KEY, "1", ex=interval_seconds)


def enqueue_due_request_error_log_retention(
    connection: Redis,
    interval_seconds: int = _RETENTION_INTERVAL_SECONDS,
) -> bool:
    """Enqueue one cleanup pass when the daily gate expires."""
    claimed = connection.set(_DAILY_GATE_KEY, "1", nx=True, ex=interval_seconds)
    if not claimed:
        return False
    try:
        Queue("maintenance", connection=connection).enqueue(
            process_request_error_log_retention,
            job_timeout=_JOB_TIMEOUT_SECONDS,
            result_ttl=86_400,
            failure_ttl=604_800,
        )
    except Exception:
        connection.delete(_DAILY_GATE_KEY)
        raise
    return True


def process_request_error_log_retention() -> dict[str, Any]:
    """Run one cleanup pass; the maintenance worker owns future scheduling."""
    return run_request_error_log_retention_sync(get_settings())


def process_periodic_request_error_log_retention(interval: int) -> dict[str, Any]:
    """Drain a delayed job registered by the previous implementation."""
    return process_request_error_log_retention()
