from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from redis import Redis
from rq import Queue
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import Settings, get_settings
from app.core.resources import AppResources

logger = logging.getLogger(__name__)

_PERIODIC_SENTINEL_KEY = "rq:periodic-assistant-request-retention"
_RETENTION_INTERVAL_SECONDS = 86_400
_JOB_TIMEOUT_SECONDS = 600


async def delete_expired_assistant_requests(
    connection: AsyncConnection,
    *,
    retention_days: int,
) -> int:
    result = await connection.execute(
        text(
            """
            DELETE FROM assistant_request
            WHERE conversation_id IS NULL
              AND status IN ('COMPLETED', 'FAILED', 'CANCELLED')
              AND updated_at < now() - make_interval(days => :retention_days)
            """
        ),
        {"retention_days": retention_days},
    )
    return max(result.rowcount or 0, 0)


async def _run_prune(settings: Settings) -> dict[str, Any]:
    """Delete only expired terminal requests with no associated conversation."""
    if settings.assistant_request_retention_days <= 0:
        return {"deleted_count": 0}

    resources = AppResources.create(settings)
    try:
        async with resources.database.begin() as connection:
            deleted_count = await delete_expired_assistant_requests(
                connection,
                retention_days=settings.assistant_request_retention_days,
            )
        return {"deleted_count": deleted_count}
    finally:
        await resources.close()


def run_assistant_request_retention_sync(settings: Settings) -> dict[str, Any]:
    return asyncio.run(_run_prune(settings))


def _enqueue_periodic(queue: Queue, interval_seconds: int) -> None:
    queue.enqueue_in(
        timedelta(seconds=interval_seconds),
        process_periodic_assistant_request_retention,
        interval=interval_seconds,
        job_timeout=_JOB_TIMEOUT_SECONDS,
        result_ttl=86_400,
        failure_ttl=604_800,
    )


def ensure_periodic_assistant_request_retention(
    connection: Redis,
    interval_seconds: int = _RETENTION_INTERVAL_SECONDS,
) -> bool:
    """Register one fleet-wide delayed-job chain for Assistant retention."""
    queue = Queue("maintenance", connection=connection)
    claimed = connection.set(_PERIODIC_SENTINEL_KEY, "1", nx=True, ex=interval_seconds * 2)
    if not claimed:
        return False
    _enqueue_periodic(queue, interval_seconds)
    return True


def _reschedule_periodic(settings: Settings, interval_seconds: int) -> None:
    if settings.assistant_request_retention_days <= 0:
        return
    connection = Redis.from_url(settings.redis_url.get_secret_value())
    try:
        queue = Queue("maintenance", connection=connection)
        _enqueue_periodic(queue, interval_seconds)
        connection.set(_PERIODIC_SENTINEL_KEY, "1", ex=interval_seconds * 2)
    except Exception:
        logger.warning("Failed to reschedule periodic Assistant request retention", exc_info=True)
    finally:
        connection.close()


def process_periodic_assistant_request_retention(interval: int) -> dict[str, Any]:
    """Run one retention pass and keep the delayed-job chain alive."""
    settings = get_settings()
    if settings.assistant_request_retention_days <= 0:
        return {"deleted_count": 0}
    try:
        return run_assistant_request_retention_sync(settings)
    finally:
        _reschedule_periodic(settings, interval)
