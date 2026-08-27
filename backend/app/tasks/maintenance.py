from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from redis import Redis
from rq import Queue

from app.common.errors import ApiError
from app.core.config import Settings, get_settings
from app.core.resources import AppResources
from app.ingestion.repository import IngestionRepository
from app.tasks.repository import TaskRecordRepository


def enqueue_maintenance_task(settings: Settings, task_id: UUID) -> None:
    connection = Redis.from_url(settings.redis_url.get_secret_value())
    try:
        Queue("maintenance", connection=connection).enqueue(
            process_maintenance_task,
            str(task_id),
            job_id=str(task_id),
            job_timeout=settings.rq_index_job_timeout_seconds,
            result_ttl=86_400,
            failure_ttl=604_800,
        )
    finally:
        connection.close()


async def _run_maintenance_task(task_id: UUID) -> dict[str, Any]:
    settings = get_settings()
    resources = AppResources.create(settings)
    records = TaskRecordRepository(resources.database)
    try:
        task = await records.get(task_id)
        if task is None:
            raise ApiError("TASK_NOT_FOUND", "后台任务不存在", 404)
        if task["status"] == "SUCCEEDED":
            return {"task_id": str(task_id), "status": "SUCCEEDED"}

        task_type = str(task["task_type"])
        started = await records.start(
            task_id,
            stage="DELETING_STORAGE" if task_type == "KNOWLEDGE_BASE_DELETE" else "GENERATING",
        )
        if not started:
            current = await records.get(task_id)
            status = str(current["status"] if current else "MISSING")
            return {"task_id": str(task_id), "status": status}
        try:
            if task_type == "KNOWLEDGE_BASE_DELETE":
                from app.services.ingestion import IngestionService

                service = IngestionService(
                    IngestionRepository(resources.database, settings), resources.minio, settings
                )
                result = await service.execute_knowledge_base_deletion(task, records)
            elif task_type in {
                "PROFILE_GENERATE",
                "PROFILE_BACKFILL",
                "PROFILE_REFRESH",
                "PROFILE_REGENERATE",
            }:
                from app.releases.repository import ReleaseRepository
                from app.releases.service import ReleaseService

                result = await ReleaseService(
                    ReleaseRepository(resources.database), settings
                ).execute_profile_task(task, records)
            elif task_type == "DOCUMENT_PROFILE_GENERATE":
                from app.document_profiles.repository import DocumentProfileRepository
                from app.document_profiles.service import DocumentProfileService

                result = await DocumentProfileService(
                    DocumentProfileRepository(resources.database), settings
                ).execute_generation_task(task, records)
            else:
                raise ApiError("TASK_TYPE_NOT_SUPPORTED", f"不支持的后台任务类型：{task_type}", 409)
        except Exception as exc:
            await records.fail(task_id, exc)
            raise
        await records.succeed(task_id, metadata=result)
        return {"task_id": str(task_id), **result}
    finally:
        await resources.close()


def process_maintenance_task(task_id: str) -> dict[str, Any]:
    return asyncio.run(_run_maintenance_task(UUID(task_id)))
