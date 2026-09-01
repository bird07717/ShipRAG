from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from redis import Redis
from rq import Queue

from app.core.config import Settings, get_settings
from app.core.resources import AppResources
from app.document_profiles.repository import DocumentProfileRepository
from app.document_profiles.service import DocumentProfileService
from app.ingestion.embedding import create_embedding_provider
from app.ingestion.image_understanding import (
    create_ocr_provider,
    create_vision_provider,
)
from app.ingestion.model_config import load_build_models
from app.ingestion.pipeline import IndexBuildResult, IndexPipeline
from app.ingestion.repository import BuildReference, IngestionRepository
from app.tasks.repository import TaskRecordRepository

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


async def _submit_post_build_profile(
    settings: Settings,
    resources: AppResources,
    result: IndexBuildResult,
    task_id: UUID,
) -> None:
    """Generate pending document attributes after a successful RAG build.

    Profile generation is best-effort: it never creates an index-bound
    candidate and a queue/model failure never fails the completed RAG build.
    """
    if result.followup_build is not None:
        return

    index = await IngestionRepository(resources.database, settings).get_index(result.index_id)
    knowledge_id = UUID(str(index["kb_id"]))
    try:
        submission = await DocumentProfileService(
            DocumentProfileRepository(resources.database), settings
        ).request_generation(knowledge_id)
        # The index task is already terminal when this follow-up is queued.
        # Preserve the relation so clients that were tracking the RAG build
        # can continue polling the independent Profile task and refresh the
        # review workspace when it settles.
        await TaskRecordRepository(resources.database).merge_metadata(
            task_id,
            {
                "profile_task_id": str(submission["task_id"]),
                "profile_task_type": str(submission["task_type"]),
            },
        )
    except Exception:
        logger.exception(
            "Could not submit document Profile generation for index %s", result.index_id
        )


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
        except Exception:
            repository = IngestionRepository(resources.database, settings)
            index = await repository.get_index(index_id)
            followup = await repository.create_followup_build_if_needed(UUID(str(index["kb_id"])))
            _enqueue_followup(settings, followup)
            raise
        await _submit_post_build_profile(settings, resources, result, task_id)
        _enqueue_followup(settings, result.followup_build)
        return {
            "index_id": str(result.index_id),
            "document_count": result.document_count,
            "element_count": result.element_count,
            "chunk_count": result.chunk_count,
        }
    finally:
        await resources.close()


def process_index_task(index_id: str, task_id: str) -> dict[str, int | str]:
    return asyncio.run(_run_index_task(UUID(index_id), UUID(task_id)))
