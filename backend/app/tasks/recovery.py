from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from redis import Redis
from rq import Worker
from rq.exceptions import NoSuchJobError
from rq.job import Job, JobStatus

from app.core.config import Settings
from app.core.resources import AppResources
from app.ingestion.repository import IngestionRepository
from app.tasks.ingestion import enqueue_index_build

logger = logging.getLogger(__name__)

_FAILED_JOB_STATUSES = {
    JobStatus.FAILED,
    JobStatus.STOPPED,
    JobStatus.CANCELED,
}


def _held_job_ids(connection: Redis) -> set[str]:
    return {
        job_id
        for worker in Worker.all(connection=connection)
        if (job_id := worker.get_current_job_id()) is not None
    }


def _job_status(task_id: UUID, connection: Redis) -> JobStatus | None:
    try:
        return Job.fetch(str(task_id), connection=connection).get_status(refresh=True)
    except NoSuchJobError:
        return None


async def reconcile_abandoned_index_builds(
    settings: Settings,
    connection: Redis,
    *,
    repository: IngestionRepository | None = None,
) -> int:
    resources = None
    if repository is None:
        resources = AppResources.create(settings)
        repository = IngestionRepository(resources.database, settings)
    recovered = 0
    try:
        held_job_ids = _held_job_ids(connection)
        for task in await repository.list_running_index_build_tasks():
            task_id = UUID(str(task["task_id"]))
            index_id = UUID(str(task["index_id"]))
            if str(task_id) in held_job_ids:
                continue
            status = _job_status(task_id, connection)
            if status is not None and status not in _FAILED_JOB_STATUSES:
                continue
            status_name = status.value if status is not None else "missing"
            if await repository.mark_abandoned_index_build_failed(
                index_id,
                task_id,
                rq_status=status_name,
            ):
                recovered += 1
                logger.error(
                    "Reconciled abandoned index build task_id=%s index_id=%s rq_status=%s",
                    task_id,
                    index_id,
                    status_name,
                )
                knowledge_id = UUID(str(task["kb_id"]))
                await _trigger_followup_build(settings, repository, knowledge_id)
        return recovered
    finally:
        if resources is not None:
            await resources.close()


async def _trigger_followup_build(
    settings: Settings,
    repository: IngestionRepository,
    knowledge_id: UUID,
) -> None:
    followup = await repository.create_followup_build_if_needed(knowledge_id)
    if followup is None or followup.index_id is None or followup.task_id is None:
        return
    await asyncio.to_thread(enqueue_index_build, settings, followup.index_id, followup.task_id)
