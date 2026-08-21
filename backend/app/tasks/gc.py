from __future__ import annotations

import asyncio
import logging
from typing import Any

from redis import Redis
from rq import Queue

from app.core.config import Settings, get_settings
from app.core.resources import AppResources
from app.ingestion.repository import IngestionRepository
from app.services.ingestion import IngestionService

logger = logging.getLogger(__name__)


def enqueue_index_gc(settings: Settings) -> None:
    connection = Redis.from_url(settings.redis_url.get_secret_value())
    try:
        queue = Queue("maintenance", connection=connection)
        queue.enqueue(
            process_gc_task,
            job_timeout=600,
            result_ttl=86_400,
            failure_ttl=604_800,
        )
    finally:
        connection.close()


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
