from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from redis import Redis
from rq import Queue

from app.core.config import Settings, get_settings
from app.core.resources import AppResources
from app.ingestion.repository import IngestionRepository
from app.services.ingestion import IngestionService

logger = logging.getLogger(__name__)

# RQ 2.x has no native recurring jobs; periodic GC is a self-rescheduling
# delayed-job chain.  The sentinel key guards against duplicate chains when
# several workers restart at once, and its TTL lets a broken chain (crashed
# worker) heal on the next worker startup.
_PERIODIC_GC_SENTINEL_KEY = "rq:periodic-index-gc"
_GC_JOB_TIMEOUT_SECONDS = 600


def _enqueue_periodic(queue: Queue, interval_seconds: int) -> None:
    queue.enqueue_in(
        timedelta(seconds=interval_seconds),
        process_periodic_gc_task,
        interval=interval_seconds,
        job_timeout=_GC_JOB_TIMEOUT_SECONDS,
        result_ttl=86_400,
        failure_ttl=604_800,
    )


def ensure_periodic_index_gc(connection: Redis, interval_seconds: int) -> bool:
    """Register the next periodic GC run if no chain is already scheduled.

    Returns True when this call won the sentinel and scheduled a run.  The
    sentinel TTL is twice the interval, refreshed on every reschedule, so a
    dead chain self-heals on worker restart.
    """
    queue = Queue("maintenance", connection=connection)
    claimed = connection.set(_PERIODIC_GC_SENTINEL_KEY, "1", nx=True, ex=interval_seconds * 2)
    if not claimed:
        return False
    _enqueue_periodic(queue, interval_seconds)
    return True


def enqueue_index_gc(settings: Settings) -> None:
    connection = Redis.from_url(settings.redis_url.get_secret_value())
    try:
        queue = Queue("maintenance", connection=connection)
        queue.enqueue(
            process_gc_task,
            job_timeout=_GC_JOB_TIMEOUT_SECONDS,
            result_ttl=86_400,
            failure_ttl=604_800,
        )
    finally:
        connection.close()


def _reschedule_periodic(settings: Settings, interval_seconds: int) -> None:
    connection = Redis.from_url(settings.redis_url.get_secret_value())
    try:
        queue = Queue("maintenance", connection=connection)
        _enqueue_periodic(queue, interval_seconds)
        # Set (not expire) so a lapsed sentinel is restored instead of
        # silently no-oping and allowing a duplicate chain on restart.
        connection.set(_PERIODIC_GC_SENTINEL_KEY, "1", ex=interval_seconds * 2)
    except Exception:
        # Once the sentinel lapses, the next worker startup re-registers
        # the chain instead of silently stopping GC.
        logger.warning("Failed to reschedule periodic index GC", exc_info=True)
    finally:
        connection.close()


def process_periodic_gc_task(interval: int) -> dict[str, Any]:
    """Run one fleet-wide GC pass and schedule the next occurrence.

    The next run is registered in ``finally`` so a failed GC pass does not
    break the chain.
    """
    settings = get_settings()
    try:
        return run_index_gc_sync(settings)
    finally:
        _reschedule_periodic(settings, interval)


async def _run_gc(settings: Settings) -> dict[str, Any]:
    resources = AppResources.create(settings)
    repository = IngestionRepository(resources.database, settings)
    service = IngestionService(repository, resources.minio, settings)
    try:
        result = await service.collect_garbage()
        if result["deleted_count"]:
            logger.info(
                "Index GC deleted %s obsolete index(es): %s",
                result["deleted_count"],
                ", ".join(result["deleted_index_ids"]),
            )
        return result
    finally:
        await resources.close()


def run_index_gc_sync(settings: Settings) -> dict[str, Any]:
    return asyncio.run(_run_gc(settings))


def process_gc_task() -> dict[str, Any]:
    return asyncio.run(_run_gc(get_settings()))
