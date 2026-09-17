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

from app.common.assets import reject_control_characters
from app.common.errors import ApiError
from app.common.text import repair_utf8_mojibake
from app.core.config import Settings
from app.ingestion.models import DOCX_MIME_TYPE, PDF_MIME_TYPE
from app.ingestion.repository import BuildReference, DocumentCreateResult, IngestionRepository
from app.ingestion.validation import (
    DocxLimits,
    DocxValidationError,
    PdfLimits,
    PdfValidationError,
    validate_docx_package,
    validate_pdf_document,
)
from app.tasks.ingestion import enqueue_index_build
from app.tasks.maintenance import enqueue_maintenance_task
from app.tasks.repository import TaskRecordRepository

logger = logging.getLogger(__name__)


class IngestionService:
    def __init__(self, repository: IngestionRepository, minio: Minio, settings: Settings) -> None:
        self.repository = repository
        self.minio = minio
        self.settings = settings

    async def rename_document(self, document_id: UUID, display_name: str) -> dict[str, Any]:
        normalized_display_name = repair_utf8_mojibake(display_name.strip())
        if not normalized_display_name:
            raise ApiError("VALIDATION_ERROR", "文档显示名称不能为空", 422)
        reject_control_characters(normalized_display_name, field_name="文档显示名称")
        return await self.repository.rename_document(document_id, normalized_display_name)

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
            raise ApiError("VALIDATION_ERROR", "必须提供文档文件名", 422)
        reject_control_characters(filename, field_name="文件名")

        lowered = filename.lower()
        if lowered.endswith(".docx"):
            is_pdf = False
            max_bytes = self.settings.m2_docx_max_bytes
            size_error = "DOCX 文件超过大小限制"
        elif lowered.endswith(".pdf"):
            is_pdf = True
            max_bytes = self.settings.m2_pdf_max_bytes
            size_error = "PDF 文件超过大小限制"
        else:
            raise ApiError("VALIDATION_ERROR", "仅支持 .docx 或 .pdf 文件", 422)

        hasher = hashlib.sha256()
        size = 0
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as staged:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise ApiError("VALIDATION_ERROR", size_error, 422)
                hasher.update(chunk)
                staged.write(chunk)
            file_hash = hasher.hexdigest()
            if is_pdf:
                try:
                    validate_pdf_document(
                        staged,
                        filename,
                        size,
                        PdfLimits(
                            max_bytes=self.settings.m2_pdf_max_bytes,
                            max_pages=self.settings.m2_pdf_max_pages,
                        ),
                    )
                except PdfValidationError as exc:
                    raise ApiError("VALIDATION_ERROR", str(exc), 422) from exc
            else:
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
            reject_control_characters(normalized_display_name, field_name="文档显示名称")
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
            extension = "pdf" if is_pdf else "docx"
            object_key = (
                f"knowledge-bases/{knowledge_id}/documents/{document_id}/source.{extension}"
            )
            staged.seek(0)
            await asyncio.to_thread(
                self.minio.put_object,
                self.settings.minio_document_bucket,
                object_key,
                staged,
                size,
                content_type=(PDF_MIME_TYPE if is_pdf else DOCX_MIME_TYPE),
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
                    mime_type=(PDF_MIME_TYPE if is_pdf else DOCX_MIME_TYPE),
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

    async def request_rebuild_all(
        self, *, reason: str, activate_on_success: bool = True
    ) -> list[dict[str, Any]]:
        """Fleet-wide rebuild, used when a build-bound model identity changes.

        Queue delivery failures are reported per KB but never abort the loop:
        every KB also carries rebuild_required=true so the console keeps
        surfacing the drift until a build succeeds.
        """
        builds = await self.repository.request_rebuild_for_all_knowledge_bases(
            reason, activate_on_success=activate_on_success
        )
        results: list[dict[str, Any]] = []
        for knowledge_id, build in builds:
            entry: dict[str, Any] = {
                "knowledge_id": str(knowledge_id),
                "index_id": str(build.index_id) if build.index_id else None,
                "task_id": str(build.task_id) if build.task_id else None,
                "coalesced": build.coalesced,
                "enqueued": True,
            }
            try:
                await self._enqueue_reference(build)
            except Exception:
                # Mirror the single-build path: without this the index row
                # would sit BUILDING with a task no worker will ever pick up.
                if build.index_id is not None and build.task_id is not None:
                    await self.repository.mark_build_enqueue_failed(build.index_id, build.task_id)
                entry["enqueued"] = False
            results.append(entry)
        return results

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

        purged_conversation_ids = (
            await self.repository.purge_expired_conversations(
                self.settings.conversation_retention_days, kb_id=kb_id
            )
            if self.settings.conversation_retention_days > 0
            else []
        )
        trace_retention = await self.repository.retain_traces(
            self.settings.trace_full_retention_days,
            self.settings.trace_summary_retention_days,
        )
        pruned_task_count = 0
        if self.settings.task_record_retention_days > 0:
            pruned_task_count = await TaskRecordRepository(
                self.repository.engine
            ).prune_terminal_tasks(self.settings.task_record_retention_days)
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
            # 先按前缀清理 MinIO 对象（含硬崩溃遗留的孤儿），再物理删除
            # 数据库行；中途失败则索引停留在 DELETING，下一轮 GC 重试。
            await self._remove_index_objects(index_id)
            deleted = await self.repository.delete_index(index_id)
            if not deleted:
                # A pointer appeared after the DELETING mark; the repository
                # restored the index instead of destroying protected data.
                skipped_ids.append(str(index_id))
                continue
            deleted_ids.append(str(index_id))

        return {
            "deleted_index_ids": deleted_ids,
            "deleted_count": len(deleted_ids),
            "skipped_index_ids": skipped_ids,
            "skipped_count": len(skipped_ids),
            "purged_conversation_ids": purged_conversation_ids[:100],
            "purged_conversation_count": len(purged_conversation_ids),
            "trace_retention": trace_retention,
            "pruned_task_count": pruned_task_count,
        }

    async def delete_index(self, index_id: UUID, *, force: bool = False) -> dict[str, Any]:
        """Force delete a specific index regardless of retention policy.

        ``force`` first detaches conversations frozen to this index so their
        references stop blocking deletion; the conversations survive and
        re-bind to the current active index on their next message.
        """
        index = await self.repository.get_index(index_id)
        if index["status"] in ("ACTIVE", "BUILDING"):
            raise ApiError(
                "FORBIDDEN",
                "不能删除 ACTIVE 或 BUILDING 状态的索引",
                409,
                details={"index_status": index["status"]},
            )

        released = 0
        if force:
            released = await self.repository.release_frozen_conversation_references(index_id)
            if released:
                logger.info(
                    "Force delete released %s frozen conversation reference(s) from index %s",
                    released,
                    index_id,
                )

        if index["status"] != "DELETING":
            marked = await self.repository.mark_index_deleting(index_id)
            if marked is None:
                blockers = await self.repository.get_index_deletion_blockers(index_id)
                self._raise_index_delete_blocked(blockers)

        # 前缀整删先于数据库删除：按 indexes/{index_id}/ 前缀清除全部
        # 图片对象（含无记录孤儿），失败时索引停留 DELETING 由 GC 自愈。
        await self._remove_index_objects(index_id)
        deleted = await self.repository.delete_index(index_id)
        if not deleted:
            self._raise_index_delete_blocked(
                await self.repository.get_index_deletion_blockers(index_id)
            )
        return {
            "deleted_index_ids": [str(index_id)],
            "deleted_count": 1,
            "released_conversation_count": released,
        }

    @staticmethod
    def _raise_index_delete_blocked(blockers: dict[str, Any] | None) -> None:
        if blockers is None:
            raise ApiError("INDEX_NOT_FOUND", "索引不存在", 404)

        details: dict[str, Any] = {
            key: int(blockers.get(key, 0))
            for key in (
                "active_index_pointer_count",
                "rag_active_pointer_count",
                "support_active_pointer_count",
                "support_active_release_count",
                "open_release_count",
                "active_task_count",
                "frozen_conversation_count",
            )
        }
        details["index_status"] = str(blockers["status"])
        labels: list[str] = []
        if details["active_index_pointer_count"] or details["rag_active_pointer_count"]:
            labels.append("RAG 在线索引")
        if details["support_active_pointer_count"]:
            labels.append("Support 在线索引")
        if details["support_active_release_count"]:
            labels.append("Support 在线版本")
        if details["open_release_count"]:
            labels.append(f"{details['open_release_count']} 个未取消 Release")
        if details["active_task_count"]:
            labels.append(f"{details['active_task_count']} 个运行中任务")
        if details["frozen_conversation_count"]:
            labels.append(f"{details['frozen_conversation_count']} 个冻结会话")
        if labels:
            raise ApiError(
                "INDEX_REFERENCE_PROTECTED",
                f"索引仍被引用，无法删除：{'、'.join(labels)}",
                409,
                details=details,
            )
        raise ApiError(
            "INDEX_NOT_DELETABLE",
            f"索引当前状态为 {details['index_status']}，只有 DEPRECATED 或 FAILED 索引可以删除",
            409,
            details=details,
        )

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

    async def _remove_index_objects(self, index_id: UUID) -> None:
        """Remove every image object stored under the index's MinIO prefix.

        Image objects are keyed ``indexes/{index_id}/documents/...`` so a
        prefix sweep removes both recorded assets and any orphan a previous
        crash left behind — deletion no longer depends on asset rows.  The
        sweep runs before the DB delete: if it fails midway the index stays
        DELETING and the next GC pass retries; already-removed objects are
        idempotent no-ops.
        """
        prefix = f"indexes/{index_id}/"

        def _sweep() -> int:
            removed = 0
            for obj in self.minio.list_objects(
                self.settings.minio_image_bucket, prefix=prefix, recursive=True
            ):
                self.minio.remove_object(self.settings.minio_image_bucket, obj.object_name)
                removed += 1
            return removed

        removed = await asyncio.to_thread(_sweep)
        if removed:
            logger.info("Removed %s MinIO image object(s) under %s", removed, prefix)
