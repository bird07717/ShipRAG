from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from redis import Redis
from rq import Queue

from app.core.config import Settings, get_settings
from app.core.resources import AppResources
from app.ingestion.embedding import create_embedding_provider
from app.ingestion.image_understanding import create_ocr_provider, create_vision_provider
from app.ingestion.model_config import load_build_models
from app.ingestion.pipeline import IndexPipeline
from app.ingestion.repository import BuildReference, IngestionRepository
from app.releases.repository import ReleaseRepository
from app.releases.service import ReleaseService

logger = logging.getLogger(__name__)


def enqueue_index_build(settings: Settings, index_id: UUID, task_id: UUID) -> None:
    connection = Redis.from_url(settings.redis_url.get_secret_value())
    try:
        queue = Queue("index_build", connection=connection)
        queue.enqueue(
            process_index_task,
            str(index_id),
            str(task_id),
            job_id=str(task_id),
            job_timeout=settings.rq_index_job_timeout_seconds,
            result_ttl=86_400,
            failure_ttl=604_800,
        )
    finally:
        connection.close()


def _enqueue_followup(settings: Settings, followup: BuildReference | None) -> None:
    if followup is not None and followup.index_id is not None and followup.task_id is not None:
        enqueue_index_build(settings, followup.index_id, followup.task_id)


async def _run_index_task(index_id: UUID, task_id: UUID) -> dict[str, int | str]:
    settings = get_settings()
    resources = AppResources.create(settings)
    try:
        # Snapshot build-bound model configs once: admin edits during the
        # build must not leak into it (same snapshot semantics as begin_turn).
        configs = await load_build_models(resources.database, settings)
        pipeline = IndexPipeline(
            resources.database,
            resources.minio,
            settings,
            embedding_provider=create_embedding_provider(settings, configs["EMBEDDING"].snapshot),
            ocr_provider=create_ocr_provider(settings, configs["OCR"].snapshot),
            vision_provider=create_vision_provider(settings, configs["VISION"].snapshot),
            model_sources={model_type: config.source for model_type, config in configs.items()},
        )
        try:
            result = await pipeline.run(index_id, task_id)
            release_service = ReleaseService(ReleaseRepository(resources.database), settings)
            try:
                await release_service.generate_for_index(result.index_id)
            except Exception as exc:
                # Profile drafting is a review aid.  A generation failure must
                # not retroactively turn a complete, usable candidate index
                # into a failed ingestion task.
                logger.exception("Profile draft generation failed for index %s", result.index_id)
                release = await release_service.repository.get_release_for_index(result.index_id)
                if release is not None:
                    await release_service.repository.set_generation_error(
                        UUID(str(release["id"])), f"{type(exc).__name__}: {exc}"[:500]
                    )
            _enqueue_followup(settings, result.followup_build)
            return {
                "index_id": str(result.index_id),
                "document_count": result.document_count,
                "element_count": result.element_count,
                "chunk_count": result.chunk_count,
            }
        except Exception:
            repository = IngestionRepository(resources.database, settings)
            index = await repository.get_index(index_id)
            followup = await repository.create_followup_build_if_needed(UUID(str(index["kb_id"])))
            _enqueue_followup(settings, followup)
            raise
    finally:
        await resources.close()


def process_index_task(index_id: str, task_id: str) -> dict[str, int | str]:
    return asyncio.run(_run_index_task(UUID(index_id), UUID(task_id)))
