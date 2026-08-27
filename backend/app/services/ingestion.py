from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import tempfile
from pathlib import PurePath
from typing import Any
from uuid import UUID, uuid4

from fastapi import UploadFile
from minio import Minio

from app.common.errors import ApiError
from app.common.text import repair_utf8_mojibake
from app.core.config import Settings
from app.ingestion.repository import BuildReference, DocumentCreateResult, IngestionRepository
from app.ingestion.validation import DocxLimits, DocxValidationError, validate_docx_package
from app.tasks.ingestion import enqueue_index_build
from app.tasks.maintenance import enqueue_maintenance_task
from app.tasks.repository import TaskRecordRepository

logger = logging.getLogger(__name__)


class IngestionService:
    def __init__(self, repository: IngestionRepository, minio: Minio, settings: Settings) -> None:
        self.repository = repository
        self.minio = minio
        self.settings = settings

    async def upload_document(
        self,
        *,
        knowledge_id: UUID,
        upload: UploadFile,
        display_name: str | None,
        request_build: bool,
        idempotency_key: str,
    ) -> dict[str, Any]:
        raw_filename = upload.filename or ""
        filename = repair_utf8_mojibake(PurePath(raw_filename).name)
        if not filename:
            raise ApiError("VALIDATION_ERROR", "必须提供 Word 文件名", 422)

        hasher = hashlib.sha256()
        size = 0
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as staged:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > self.settings.m2_docx_max_bytes:
                    raise ApiError("VALIDATION_ERROR", "DOCX 文件超过大小限制", 422)
                hasher.update(chunk)
                staged.write(chunk)
            file_hash = hasher.hexdigest()
            limits = DocxLimits(
                max_bytes=self.settings.m2_docx_max_bytes,
                max_entries=self.settings.m2_docx_max_entries,
                max_uncompressed_bytes=self.settings.m2_docx_max_uncompressed_bytes,
                max_entry_bytes=self.settings.m2_docx_max_entry_bytes,
                max_compression_ratio=self.settings.m2_docx_max_compression_ratio,
            )
            try:
                validate_docx_package(staged, filename, size, limits)
            except DocxValidationError as exc:
                raise ApiError("VALIDATION_ERROR", str(exc), 422) from exc

            normalized_display_name = repair_utf8_mojibake(
                (display_name or PurePath(filename).stem).strip()
            )
            if not normalized_display_name:
                raise ApiError("VALIDATION_ERROR", "文档显示名称不能为空", 422)
            request_hash = hashlib.sha256(
                json.dumps(
                    {
                        "knowledge_id": str(knowledge_id),
                        "file_hash": file_hash,
                        "display_name": normalized_display_name,
                        "request_build": request_build,
                    },
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            operation = f"upload_document:{knowledge_id}"
            replay = await self.repository.find_idempotent_response(
                operation, idempotency_key, request_hash
            )
            if replay is not None:
                build_data = replay.get("build_request")
                if isinstance(build_data, dict):
                    build = BuildReference(
                        requested=bool(build_data.get("requested")),
                        coalesced=bool(build_data.get("coalesced")),
                        index_id=(
                            UUID(str(build_data["index_id"]))
                            if build_data.get("index_id")
                            else None
                        ),
                        task_id=(
                            UUID(str(build_data["task_id"])) if build_data.get("task_id") else None
                        ),
                        rebuild_required=bool(build_data.get("rebuild_required")),
                        replayed=True,
                    )
                    await self._enqueue_reference(build)
                return replay

            document_id = uuid4()
            object_key = f"knowledge-bases/{knowledge_id}/documents/{document_id}/source.docx"
            staged.seek(0)
            await asyncio.to_thread(
                self.minio.put_object,
                self.settings.minio_document_bucket,
                object_key,
                staged,
                size,
                content_type=(
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
            )
            created = False
            try:
                result = await self.repository.create_document(
                    knowledge_id=knowledge_id,
                    document_id=document_id,
                    filename=filename,
                    display_name=normalized_display_name,
                    object_key=object_key,
                    file_hash=file_hash,
                    file_size=size,
                    request_build=request_build,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
                created = result.created
            except Exception:
                await self._remove_object(object_key)
                raise

        if not created:
            await self._remove_object(object_key)
        await self._enqueue_build(result)
        return result.response

    async def _enqueue_build(self, result: DocumentCreateResult) -> None:
        build = result.build
        if (
            not result.created
            or not build.requested
            or build.coalesced
            or build.index_id is None
            or build.task_id is None
        ):
            return
        try:
            await asyncio.to_thread(
                enqueue_index_build,
                self.settings,
                build.index_id,
                build.task_id,
            )
        except Exception as exc:
            await self.repository.mark_build_enqueue_failed(build.index_id, build.task_id)
            raise ApiError("UPSTREAM_UNAVAILABLE", "异步任务队列不可用", 503) from exc

    async def request_build(
        self,
        knowledge_id: UUID,
        *,
        reason: str,
        activate_on_success: bool,
        idempotency_key: str,
    ) -> BuildReference:
        build = await self.repository.request_build_idempotent(
            knowledge_id,
            reason,
            activate_on_success=activate_on_success,
            idempotency_key=idempotency_key,
        )
        await self._enqueue_reference(build)
        return build

    async def retry_build(self, index_id: UUID, *, activate_on_success: bool) -> BuildReference:
        build = await self.repository.retry_index(index_id, activate_on_success=activate_on_success)
        await self._enqueue_reference(build)
        return build

    async def delete_document(
        self, document_id: UUID, *, request_build: bool = True
    ) -> dict[str, Any]:
        result = await self.repository.delete_document(document_id, request_build=request_build)
        await self._enqueue_reference(result.build)
        return {
            "document_id": result.document_id,
            "deleted": True,
            "build_request": {
                "requested": result.build.requested,
                "coalesced": result.build.coalesced,
                "index_id": result.build.index_id,
                "task_id": result.build.task_id,
                "rebuild_required": result.build.rebuild_required,
            },
        }

    async def request_knowledge_base_deletion(self, knowledge_id: UUID) -> dict[str, Any]:
        submission = await self.repository.request_knowledge_base_deletion(knowledge_id)
        if submission["replayed"]:
            return submission
        task_id = UUID(str(submission["task_id"]))
        try:
            await asyncio.to_thread(enqueue_maintenance_task, self.settings, task_id)
        except Exception as exc:
            await TaskRecordRepository(self.repository.engine).fail(task_id, exc)
            raise ApiError("UPSTREAM_UNAVAILABLE", "异步任务队列不可用", 503) from exc
        return submission

    async def request_profile_rebuild(
        self, knowledge_id: UUID, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Submit the Knowledge-level Profile rebuild shortcut.

        The repository resolves the target candidate atomically; this layer
        only delivers the resulting task to its RQ queue and mirrors the
        existing enqueue-failure bookkeeping (queue failures must not leave a
        QUEUED task that no worker will ever pick up).
        """
        resolution = await self.repository.resolve_profile_rebuild(knowledge_id, idempotency_key)
        if resolution["queue"] is None:
            # Replayed or intent-merged task: it is already enqueued.
            return resolution
        task_id = UUID(str(resolution["task_id"]))
        index_id = UUID(str(resolution["index_id"]))
        try:
            if resolution["queue"] == "index_build":
                await asyncio.to_thread(enqueue_index_build, self.settings, index_id, task_id)
            else:
                await asyncio.to_thread(enqueue_maintenance_task, self.settings, task_id)
        except Exception as exc:
            if resolution["queue"] == "index_build":
                await self.repository.mark_build_enqueue_failed(index_id, task_id)
            else:
                await TaskRecordRepository(self.repository.engine).fail(task_id, exc)
            raise ApiError("UPSTREAM_UNAVAILABLE", "异步任务队列不可用", 503) from exc
        return resolution

    async def execute_knowledge_base_deletion(
        self, task: dict[str, Any], task_records: TaskRecordRepository
    ) -> dict[str, Any]:
        task_id = UUID(str(task["id"]))
        metadata = dict(task.get("metadata") or {})
        knowledge_id = UUID(str(metadata["knowledge_id"]))
        storage_objects = await self.repository.list_knowledge_base_cleanup_objects(task_id)
        total = int(metadata.get("cleanup_total") or len(storage_objects))
        completed = max(0, total - len(storage_objects))
        for item in storage_objects:
            bucket = str(item["minio_bucket"])
            object_key = str(item["minio_object_key"])
            try:
                await self._remove_minio_object(bucket, object_key)
            except Exception as exc:
                await self.repository.mark_knowledge_base_cleanup_object_failed(
                    task_id, bucket, object_key, exc
                )
                logger.warning(
                    "Failed to remove object while deleting knowledge base %s",
                    knowledge_id,
                    exc_info=True,
                )
                raise
            await self.repository.mark_knowledge_base_cleanup_object_deleted(
                task_id, bucket, object_key
            )
            completed += 1
            await task_records.progress(
                task_id,
                stage="DELETING_STORAGE",
                progress=(5 + int(85 * completed / total)) if total else 90,
                metadata={"cleanup_completed": completed, "cleanup_total": total},
            )
        await task_records.progress(task_id, stage="PURGING_DATABASE", progress=95)
        deleted = await self.repository.finalize_knowledge_base_deletion(knowledge_id)
        return {
            "knowledge_id": str(knowledge_id),
            "deleted": deleted,
            "cleanup_completed": completed,
            "cleanup_total": total,
        }

    async def collect_garbage(self, kb_id: UUID | None = None) -> dict[str, Any]:
        """Collect garbage for obsolete indexes.

        Two-phase: mark DELETING, then physically delete (DB CASCADE + MinIO).
        Also recovers indexes stuck in DELETING from a previous crash.
        Long-idle conversations past the retention window are purged first so
        their frozen index references stop blocking collection; the purged ids
        are reported back for audit.
        """
        retention_count = self.settings.index_gc_retention_count
        retention_days = self.settings.index_gc_retention_days

        purged_conversation_ids = await self.repository.purge_expired_conversations(
            self.settings.conversation_retention_days, kb_id=kb_id
        )
        stuck = await self.repository.find_deleting_indexes()
        deletable = await self.repository.find_deletable_indexes(retention_count, retention_days)
        if kb_id is not None:
            stuck = [item for item in stuck if item.kb_id == kb_id]
            deletable = [item for item in deletable if item.kb_id == kb_id]

        deleted_ids: list[str] = []
        skipped_ids: list[str] = []
        for item in stuck + deletable:
            index_id = item.index_id
            if item.status != "DELETING":
                marked = await self.repository.mark_index_deleting(index_id)
                if marked is None:
                    continue
            image_assets = await self.repository.list_image_assets_for_index(index_id)
            deleted = await self.repository.delete_index(index_id)
            if not deleted:
                # A pointer appeared after the DELETING mark; the repository
                # restored the index instead of destroying protected data.
                skipped_ids.append(str(index_id))
                continue
            for asset in image_assets:
                await self._remove_minio_object(asset["minio_bucket"], asset["minio_object_key"])
            deleted_ids.append(str(index_id))

        return {
            "deleted_index_ids": deleted_ids,
            "deleted_count": len(deleted_ids),
            "skipped_index_ids": skipped_ids,
            "skipped_count": len(skipped_ids),
            "purged_conversation_ids": purged_conversation_ids[:100],
            "purged_conversation_count": len(purged_conversation_ids),
        }

    async def delete_index(self, index_id: UUID) -> dict[str, Any]:
        """Force delete a specific index regardless of retention policy."""
        index = await self.repository.get_index(index_id)
        if index["status"] in ("ACTIVE", "BUILDING"):
            raise ApiError("FORBIDDEN", "不能删除 ACTIVE 或 BUILDING 状态的索引", 409)

        if index["status"] != "DELETING":
            marked = await self.repository.mark_index_deleting(index_id)
            if marked is None:
                raise ApiError("INDEX_NOT_FOUND", "索引不存在或仍被线上引用", 404)

        image_assets = await self.repository.list_image_assets_for_index(index_id)
        deleted = await self.repository.delete_index(index_id)
        if not deleted:
            raise ApiError(
                "INDEX_REFERENCE_PROTECTED", "索引仍被线上版本或冻结会话引用，已取消删除", 409
            )
        for asset in image_assets:
            await self._remove_minio_object(asset["minio_bucket"], asset["minio_object_key"])
        return {"deleted_index_ids": [str(index_id)], "deleted_count": 1}

    async def _enqueue_reference(self, build: BuildReference) -> None:
        if (
            not build.requested
            or build.coalesced
            or build.index_id is None
            or build.task_id is None
        ):
            return
        if build.replayed and not await self.repository.recover_failed_enqueue(
            build.index_id, build.task_id
        ):
            return
        try:
            await asyncio.to_thread(
                enqueue_index_build,
                self.settings,
                build.index_id,
                build.task_id,
            )
        except Exception as exc:
            await self.repository.mark_build_enqueue_failed(build.index_id, build.task_id)
            raise ApiError("UPSTREAM_UNAVAILABLE", "异步任务队列不可用", 503) from exc

    async def _remove_object(self, object_key: str) -> None:
        await asyncio.to_thread(
            self.minio.remove_object, self.settings.minio_document_bucket, object_key
        )

    async def _remove_minio_object(self, bucket: str, object_key: str) -> None:
        await asyncio.to_thread(self.minio.remove_object, bucket, object_key)
