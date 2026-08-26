from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from redis import Redis
from rq import Queue

from app.core.config import Settings, get_settings
from app.core.resources import AppResources
from app.ingestion.embedding import create_embedding_provider
from app.ingestion.image_understanding import (
    create_ocr_provider,
    create_vision_provider,
)
from app.ingestion.model_config import load_build_models
from app.ingestion.pipeline import IndexBuildResult, IndexPipeline
from app.ingestion.repository import BuildReference, IngestionRepository
from app.releases.repository import ReleaseRepository
from app.releases.service import ReleaseService
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
    """Submit the follow-on Profile task after a successful index build.

    ``profile_refresh_after_build`` marks a build that was orchestrated by the
    Knowledge-level Profile rebuild entry point.  Losing that submission must
    fail the orchestrated task with a readable error, because the user's
    requested outcome is the refresh, not the build.  The default build-time
    generation degrades to a release ``generation_error`` instead, so a queue
    or LLM issue never turns a complete candidate index into a failed build.
    """
    records = TaskRecordRepository(resources.database)
    task = await records.get(task_id)
    intent = bool(
        (dict(task.get("metadata") or {}) if task else {}).get("profile_refresh_after_build")
    )

    if result.followup_build is not None:
        # A newer candidate already superseded this release (documents changed
        # mid-build), so its release is cancelled and refreshing it would fail.
        # Carry the intent to the followup build instead.
        if intent and result.followup_build.task_id is not None:
            await records.merge_metadata(
                result.followup_build.task_id, {"profile_refresh_after_build": True}
            )
        return

    release_service = ReleaseService(ReleaseRepository(resources.database), settings)
    try:
        submission = await release_service.request_profile_task(
            result.index_id,
            task_type="PROFILE_REFRESH" if intent else "PROFILE_GENERATE",
        )
    except Exception as exc:
        if not intent:
            logger.exception("Could not submit Profile generation for index %s", result.index_id)
            release = await release_service.repository.get_release_for_index(result.index_id)
            if release is not None:
                await release_service.repository.set_generation_error(
                    UUID(str(release["id"])), f"{type(exc).__name__}: {exc}"[:500]
                )
            return
        await records.fail(task_id, exc)
        logger.error(
            "Profile refresh after orchestrated build failed for index %s: %s",
            result.index_id,
            exc,
        )
        raise

    if intent and not submission.get("replayed"):
        # Chain the Profile task to its build task so the task center can keep
        # tracking the rebuild until the refresh itself finishes.
        await records.merge_metadata(task_id, {"profile_task_id": str(submission["task_id"])})


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
