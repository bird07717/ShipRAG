from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from redis import Redis
from rq import Worker
from rq.exceptions import NoSuchJobError
from rq.job import Job, JobStatus

from app.common.errors import ApiError
from app.core.config import Settings
from app.core.resources import AppResources
from app.ingestion.repository import IngestionRepository
from app.tasks.ingestion import enqueue_index_build
from app.tasks.repository import TaskRecordRepository

logger = logging.getLogger(__name__)

_FAILED_JOB_STATUSES = {
    JobStatus.FAILED,
    JobStatus.STOPPED,
    JobStatus.CANCELED,
}

# Maintenance task types reconciled at worker startup.  KNOWLEDGE_BASE_DELETE
# is deliberately excluded: its side effects are not cooperative-cancel safe.
_MAINTENANCE_TASK_TYPES = ("DOCUMENT_PROFILE_GENERATE",)
# A QUEUED task whose RQ job is missing may simply sit between the task INSERT
# and the enqueue call; only reconcile once it is clearly stale.
_QUEUED_GRACE_SECONDS = 600.0
# RUNNING profile tasks refresh task_record per completed document, so no
# heartbeat for this long means the executing worker died mid-document.
_RUNNING_STALE_SECONDS = 1_800.0


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


def _age_seconds(row: dict[str, object], column: str) -> float | None:
    stamp = row.get(column)
    if not isinstance(stamp, datetime):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return (datetime.now(UTC) - stamp).total_seconds()


async def reconcile_abandoned_maintenance_tasks(
    settings: Settings,
    connection: Redis,
    *,
    records: TaskRecordRepository | None = None,
) -> int:
    """Fail maintenance tasks whose executing worker is gone.

    Without this, a worker crash mid DOCUMENT_PROFILE_GENERATE leaves the row
    RUNNING forever; request_generation() then replays the zombie and the
    knowledge base can never generate profiles again.
    """
    resources = None
    if records is None:
        resources = AppResources.create(settings)
        records = TaskRecordRepository(resources.database)
    recovered = 0
    try:
        held_job_ids = _held_job_ids(connection)
        for task_type in _MAINTENANCE_TASK_TYPES:
            for task in await records.list_active_by_type(task_type):
                task_id = UUID(str(task["id"]))
                if str(task_id) in held_job_ids:
                    continue
                job_status = _job_status(task_id, connection)
                row_status = str(task["status"])
                age_seconds = _age_seconds(task, "updated_at")
                error: ApiError | None = None
                if job_status is not None and job_status not in _FAILED_JOB_STATUSES:
                    # The job still exists in Redis (queued or executing on a
                    # worker we cannot see).  Only a heartbeat-stale RUNNING
                    # row is certainly unrecoverable.
                    if (
                        row_status == "RUNNING"
                        and age_seconds is not None
                        and age_seconds > _RUNNING_STALE_SECONDS
                    ):
                        error = ApiError(
                            "TASK_HEARTBEAT_TIMEOUT",
                            "任务长时间无进度心跳，已判定为孤儿任务",
                            409,
                        )
                elif (
                    job_status is None
                    and row_status == "QUEUED"
                    and age_seconds is not None
                    and age_seconds < _QUEUED_GRACE_SECONDS
                ):
                    # Task row committed a moment ago; the enqueue may simply
                    # not have happened yet.
                    continue
                else:
                    error = ApiError("ORPHANED_TASK", "后台任务失去执行者，已判定为孤儿任务", 409)
                if error is None:
                    continue
                await records.fail(task_id, error)
                recovered += 1
                rq_status = job_status.value if job_status is not None else "missing"
                logger.error(
                    "Reconciled abandoned maintenance task_id=%s type=%s kb_id=%s "
                    "row_status=%s rq_status=%s",
                    task_id,
                    task_type,
                    task.get("kb_id"),
                    row_status,
                    rq_status,
                )
        return recovered
    finally:
        if resources is not None:
            await resources.close()
