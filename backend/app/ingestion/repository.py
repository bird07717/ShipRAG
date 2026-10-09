from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.common.errors import ApiError
from app.core.config import Settings
from app.ingestion.models import content_revision_for_mime
from app.tasks.repository import TaskRecordRepository


@dataclass(frozen=True, slots=True)
class BuildReference:
    requested: bool
    coalesced: bool
    index_id: UUID | None
    task_id: UUID | None
    rebuild_required: bool
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class DocumentCreateResult:
    response: dict[str, Any]
    created: bool
    build: BuildReference


@dataclass(frozen=True, slots=True)
class DocumentDeleteResult:
    document_id: UUID
    build: BuildReference


@dataclass(frozen=True, slots=True)
class GarbageCollectionItem:
    index_id: UUID
    kb_id: UUID
    version: int
    status: str


class IngestionRepository:
    def __init__(self, engine: AsyncEngine, settings: Settings) -> None:
        self.engine = engine
        self.settings = settings

    async def create_knowledge_base(self, name: str, description: str | None) -> dict[str, Any]:
        knowledge_id = uuid4()
        try:
            async with self.engine.begin() as connection:
                result = await connection.execute(
                    text(
                        """
                        INSERT INTO knowledge_base (id, name, description)
                        VALUES (:id, :name, :description)
                        RETURNING *
                        """
                    ),
                    {"id": knowledge_id, "name": name, "description": description},
                )
                return self._knowledge_response(dict(result.mappings().one()))
        except Exception as exc:
            if "knowledge_base_name_key" in str(exc):
                raise ApiError("VALIDATION_ERROR", "知识库名称已存在", 409) from exc
            raise

    async def list_knowledge_bases(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT kb.*,
                           (SELECT count(*) FROM document_source d
                            WHERE d.kb_id = kb.id AND d.status = 'STORED') AS document_count,
                           COALESCE((SELECT chunk_count FROM knowledge_index i
                                     WHERE i.id = COALESCE(
                                         kb.rag_active_index_id, kb.active_index_id
                                     )), 0) AS active_chunk_count,
                           (SELECT id FROM knowledge_index i WHERE i.kb_id = kb.id
                            AND i.status = 'BUILDING' LIMIT 1) AS building_index_id,
                           (SELECT status FROM knowledge_index i WHERE i.kb_id = kb.id
                            ORDER BY i.version DESC LIMIT 1) AS latest_index_status
                    FROM knowledge_base kb
                    ORDER BY kb.created_at DESC
                    """
                )
            )
            return [self._knowledge_response(dict(row)) for row in result.mappings()]

    async def get_knowledge_base(self, knowledge_id: UUID) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT kb.*,
                           (SELECT count(*) FROM document_source d
                            WHERE d.kb_id = kb.id AND d.status = 'STORED') AS document_count,
                           COALESCE((SELECT chunk_count FROM knowledge_index i
                                     WHERE i.id = COALESCE(
                                         kb.rag_active_index_id, kb.active_index_id
                                     )), 0) AS active_chunk_count,
                           (SELECT id FROM knowledge_index i WHERE i.kb_id = kb.id
                            AND i.status = 'BUILDING' LIMIT 1) AS building_index_id,
                           (SELECT status FROM knowledge_index i WHERE i.kb_id = kb.id
                            ORDER BY i.version DESC LIMIT 1) AS latest_index_status
                    FROM knowledge_base kb WHERE kb.id = :id
                    """
                ),
                {"id": knowledge_id},
            )
            row = result.mappings().one_or_none()
        if row is None:
            raise ApiError("KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在", 404)
        return self._knowledge_response(dict(row))

    async def update_knowledge_base_description(
        self,
        knowledge_id: UUID,
        *,
        description: str | None,
        expected_updated_at: datetime,
    ) -> dict[str, Any]:
        async with self.engine.begin() as connection:
            updated = await connection.execute(
                text(
                    """
                    UPDATE knowledge_base
                    SET description = :description, updated_at = now()
                    WHERE id = :id AND status <> 'DELETING'
                      AND updated_at = :expected_updated_at
                    RETURNING id
                    """
                ),
                {
                    "id": knowledge_id,
                    "description": description,
                    "expected_updated_at": expected_updated_at,
                },
            )
            if updated.scalar_one_or_none() is None:
                current = await connection.execute(
                    text("SELECT status, updated_at FROM knowledge_base WHERE id = :id"),
                    {"id": knowledge_id},
                )
                row = current.mappings().one_or_none()
                if row is None:
                    raise ApiError("KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在", 404)
                if row["status"] == "DELETING":
                    raise ApiError("KNOWLEDGE_BASE_DELETING", "知识库正在删除，不能修改", 409)
                raise ApiError(
                    "KNOWLEDGE_BASE_UPDATE_CONFLICT",
                    "知识库已被其他请求修改，请重新加载后再保存",
                    409,
                    {"current_updated_at": row["updated_at"].isoformat()},
                )
            refreshed = await connection.execute(
                text(
                    """
                    SELECT kb.*,
                           (SELECT count(*) FROM document_source d
                            WHERE d.kb_id = kb.id AND d.status = 'STORED') AS document_count,
                           COALESCE((SELECT chunk_count FROM knowledge_index i
                                     WHERE i.id = COALESCE(
                                         kb.rag_active_index_id, kb.active_index_id
                                     )), 0) AS active_chunk_count,
                           (SELECT id FROM knowledge_index i WHERE i.kb_id = kb.id
                            AND i.status = 'BUILDING' LIMIT 1) AS building_index_id,
                           (SELECT status FROM knowledge_index i WHERE i.kb_id = kb.id
                            ORDER BY i.version DESC LIMIT 1) AS latest_index_status
                    FROM knowledge_base kb WHERE kb.id = :id
                    """
                ),
                {"id": knowledge_id},
            )
            return self._knowledge_response(dict(refreshed.mappings().one()))

    async def request_knowledge_base_deletion(self, knowledge_id: UUID) -> dict[str, Any]:
        async with self.engine.begin() as connection:
            knowledge_result = await connection.execute(
                text(
                    """
                    SELECT kb.id, kb.name, kb.status,
                           EXISTS (
                               SELECT 1 FROM knowledge_index i
                               WHERE i.kb_id = kb.id AND i.status = 'BUILDING'
                           ) AS has_building_index
                    FROM knowledge_base kb
                    WHERE kb.id = :knowledge_id
                    FOR UPDATE OF kb
                    """
                ),
                {"knowledge_id": knowledge_id},
            )
            knowledge = knowledge_result.mappings().one_or_none()
            if knowledge is None:
                raise ApiError("KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在", 404)
            if knowledge["has_building_index"]:
                raise ApiError(
                    "KNOWLEDGE_BASE_BUILD_IN_PROGRESS",
                    "知识库正在构建索引，请等待任务结束后再删除",
                    409,
                )

            existing_result = await connection.execute(
                text(
                    """
                    SELECT id, status FROM task_record
                    WHERE kb_id = :knowledge_id AND task_type = 'KNOWLEDGE_BASE_DELETE'
                      AND status IN ('QUEUED', 'RUNNING')
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"knowledge_id": knowledge_id},
            )
            existing = existing_result.mappings().one_or_none()
            if existing is not None:
                return {
                    "task_id": existing["id"],
                    "task_type": "KNOWLEDGE_BASE_DELETE",
                    "status": existing["status"],
                    "replayed": True,
                }
            if knowledge["status"] == "DELETING":
                failed_result = await connection.execute(
                    text(
                        """
                        SELECT id FROM task_record
                        WHERE kb_id = :knowledge_id AND task_type = 'KNOWLEDGE_BASE_DELETE'
                          AND status = 'FAILED'
                        ORDER BY created_at DESC
                        LIMIT 1
                        FOR UPDATE
                        """
                    ),
                    {"knowledge_id": knowledge_id},
                )
                failed_task_id = failed_result.scalar_one_or_none()
                if failed_task_id is not None:
                    await connection.execute(
                        text(
                            """
                            UPDATE task_record
                            SET status = 'QUEUED', stage = 'QUEUED', progress = 0,
                                error_code = NULL, error_message = NULL, finished_at = NULL,
                                updated_at = now()
                            WHERE id = :id AND status = 'FAILED'
                            """
                        ),
                        {"id": failed_task_id},
                    )
                    return {
                        "task_id": failed_task_id,
                        "task_type": "KNOWLEDGE_BASE_DELETE",
                        "status": "QUEUED",
                        "replayed": False,
                    }
                raise ApiError(
                    "KNOWLEDGE_BASE_DELETE_FAILED",
                    "上一次知识库删除任务未完成，请在任务中心查看失败原因",
                    409,
                )

            storage_result = await connection.execute(
                text(
                    """
                    SELECT minio_bucket, minio_object_key
                    FROM document_source
                    WHERE kb_id = :knowledge_id
                    UNION
                    SELECT a.minio_bucket, a.minio_object_key
                    FROM image_asset a
                    JOIN knowledge_index i ON i.id = a.index_id
                    WHERE i.kb_id = :knowledge_id
                    """
                ),
                {"knowledge_id": knowledge_id},
            )
            storage_objects = tuple(
                (str(row["minio_bucket"]), str(row["minio_object_key"]))
                for row in storage_result.mappings()
            )
            task_id = await TaskRecordRepository.create_in_connection(
                connection,
                task_type="KNOWLEDGE_BASE_DELETE",
                kb_id=knowledge_id,
                metadata={
                    "knowledge_id": str(knowledge_id),
                    "knowledge_name": str(knowledge["name"]),
                    "cleanup_total": len(storage_objects),
                },
            )
            if storage_objects:
                await connection.execute(
                    text(
                        """
                        INSERT INTO storage_cleanup_object (task_id, minio_bucket, minio_object_key)
                        VALUES (:task_id, :minio_bucket, :minio_object_key)
                        """
                    ),
                    [
                        {
                            "task_id": task_id,
                            "minio_bucket": bucket,
                            "minio_object_key": object_key,
                        }
                        for bucket, object_key in storage_objects
                    ],
                )
            await connection.execute(
                text(
                    """
                    UPDATE knowledge_base
                    SET active_index_id = NULL,
                        rag_active_index_id = NULL,
                        support_active_index_id = NULL,
                        support_active_release_id = NULL,
                        status = 'DELETING', updated_at = now()
                    WHERE id = :knowledge_id
                    """
                ),
                {"knowledge_id": knowledge_id},
            )
        return {
            "task_id": task_id,
            "task_type": "KNOWLEDGE_BASE_DELETE",
            "status": "QUEUED",
            "replayed": False,
        }

    async def resolve_profile_rebuild(
        self, knowledge_id: UUID, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Resolve a Knowledge-level Profile rebuild into one durable task.

        The three branches mirror the candidate-resolution rules in
        docs/architecture/knowledge-profile-visibility-and-async-e2e-verification-plan.md
        §2.1, all inside a single transaction holding the knowledge base row
        lock:

        - a BUILDING candidate absorbs the intent into its INDEX_BUILD task;
        - an editable READY candidate gets a deduplicated PROFILE_REFRESH;
        - anything else starts a candidate build that refreshes Profiles on
          completion.

        The returned dict carries an internal ``queue`` key (None when the task
        is already enqueued) that the service layer strips before responding.
        """
        async with self.engine.begin() as connection:
            kb_result = await connection.execute(
                text("SELECT status FROM knowledge_base WHERE id = :id FOR UPDATE"),
                {"id": knowledge_id},
            )
            kb_status = kb_result.scalar_one_or_none()
            if kb_status is None:
                raise ApiError("KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在", 404)
            if kb_status == "DELETING":
                raise ApiError("KNOWLEDGE_BASE_DELETING", "知识库正在删除，无法重建 Profile", 409)
            if kb_status != "ENABLED":
                raise ApiError("FORBIDDEN", "知识库已停用", 403)

            building_result = await connection.execute(
                text(
                    """
                    SELECT id FROM knowledge_index
                    WHERE kb_id = :kb_id AND status = 'BUILDING'
                    FOR UPDATE
                    """
                ),
                {"kb_id": knowledge_id},
            )
            building_index_id = building_result.scalar_one_or_none()
            if building_index_id is not None:
                # A candidate build is already running: merge the post-build
                # refresh intent instead of creating a parallel generation.
                task_result = await connection.execute(
                    text(
                        """
                        SELECT id, status FROM task_record
                        WHERE index_id = :index_id AND task_type = 'INDEX_BUILD'
                          AND status IN ('QUEUED', 'RUNNING')
                        ORDER BY created_at DESC
                        LIMIT 1
                        FOR UPDATE
                        """
                    ),
                    {"index_id": building_index_id},
                )
                building_task = task_result.mappings().one_or_none()
                if building_task is None:
                    raise ApiError(
                        "INDEX_BUILD_STUCK",
                        "候选索引正在构建但构建任务已中断，请先在 Index Build 处理后再重建 Profile",
                        409,
                    )
                await connection.execute(
                    text(
                        """
                        UPDATE task_record
                        SET metadata = metadata || '{"profile_refresh_after_build": true}'::jsonb,
                            updated_at = now()
                        WHERE id = :task_id
                        """
                    ),
                    {"task_id": building_task["id"]},
                )
                return {
                    "task_id": building_task["id"],
                    "task_type": "INDEX_BUILD",
                    "status": building_task["status"],
                    "replayed": True,
                    "queue": None,
                    "index_id": building_index_id,
                }

            release_result = await connection.execute(
                text(
                    """
                    SELECT r.id AS release_id, r.index_id, i.status AS index_status
                    FROM knowledge_release r
                    JOIN knowledge_index i ON i.id = r.index_id
                    WHERE r.kb_id = :kb_id AND r.status NOT IN ('PUBLISHED', 'CANCELLED')
                    ORDER BY i.version DESC
                    LIMIT 1
                    FOR UPDATE OF r
                    """
                ),
                {"kb_id": knowledge_id},
            )
            release = release_result.mappings().one_or_none()
            if release is not None and release["index_status"] == "READY":
                index_id = UUID(str(release["index_id"]))
                active = await self._active_profile_task(connection, index_id)
                if active is not None:
                    return {
                        "task_id": active["id"],
                        "task_type": active["task_type"],
                        "status": active["status"],
                        "replayed": True,
                        "queue": None,
                        "index_id": index_id,
                    }
                metadata: dict[str, Any] = {
                    "release_id": str(release["release_id"]),
                    "knowledge_id": str(knowledge_id),
                    "source": "KNOWLEDGE_PROFILE_REBUILD",
                }
                if idempotency_key is not None:
                    metadata["idempotency_key"] = idempotency_key
                task_id = await TaskRecordRepository.create_profile_task_in_connection(
                    connection,
                    task_type="PROFILE_REFRESH",
                    kb_id=knowledge_id,
                    index_id=index_id,
                    metadata=metadata,
                )
                if task_id is None:
                    # A concurrent submission won the unique active-task slot.
                    winner = await self._active_profile_task(connection, index_id)
                    if winner is None:  # pragma: no cover - index guarantees a row
                        raise ApiError("INTERNAL_ERROR", "Profile 任务提交冲突无法解析", 500)
                    return {
                        "task_id": winner["id"],
                        "task_type": winner["task_type"],
                        "status": winner["status"],
                        "replayed": True,
                        "queue": None,
                        "index_id": index_id,
                    }
                return {
                    "task_id": task_id,
                    "task_type": "PROFILE_REFRESH",
                    "status": "QUEUED",
                    "replayed": False,
                    "queue": "maintenance",
                    "index_id": index_id,
                }

            # No editable candidate: create one.  The build task carries the
            # refresh intent in task_record.metadata so the worker — not the
            # API process — submits PROFILE_REFRESH after the build succeeds.
            build = await self._create_build_locked(
                connection,
                knowledge_id,
                "PROFILE_REBUILD",
                activate_on_success=False,
                profile_refresh_after_build=True,
            )
            return {
                "task_id": build.task_id,
                "task_type": "INDEX_BUILD",
                "status": "QUEUED",
                "replayed": False,
                "queue": "index_build",
                "index_id": build.index_id,
            }

    @staticmethod
    async def _active_profile_task(
        connection: AsyncConnection, index_id: UUID
    ) -> dict[str, Any] | None:
        result = await connection.execute(
            text(
                """
                SELECT id, task_type, status FROM task_record
                WHERE index_id = :index_id
                  AND task_type IN (
                      'PROFILE_GENERATE', 'PROFILE_BACKFILL',
                      'PROFILE_REFRESH', 'PROFILE_REGENERATE'
                  )
                  AND status IN ('QUEUED', 'RUNNING')
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"index_id": index_id},
        )
        active = result.mappings().one_or_none()
        return dict(active) if active is not None else None

    async def list_knowledge_base_cleanup_objects(self, task_id: UUID) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT minio_bucket, minio_object_key
                    FROM storage_cleanup_object
                    WHERE task_id = :task_id AND status <> 'DELETED'
                    ORDER BY created_at, minio_object_key
                    """
                ),
                {"task_id": task_id},
            )
            return [dict(row) for row in result.mappings()]

    async def mark_knowledge_base_cleanup_object_deleted(
        self, task_id: UUID, bucket: str, object_key: str
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE storage_cleanup_object
                    SET status = 'DELETED', deleted_at = now(), error_message = NULL
                    WHERE task_id = :task_id AND minio_bucket = :bucket
                      AND minio_object_key = :object_key
                    """
                ),
                {"task_id": task_id, "bucket": bucket, "object_key": object_key},
            )

    async def mark_knowledge_base_cleanup_object_failed(
        self, task_id: UUID, bucket: str, object_key: str, error: Exception
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE storage_cleanup_object
                    SET status = 'FAILED', attempts = attempts + 1, error_message = :error_message
                    WHERE task_id = :task_id AND minio_bucket = :bucket
                      AND minio_object_key = :object_key
                    """
                ),
                {
                    "task_id": task_id,
                    "bucket": bucket,
                    "object_key": object_key,
                    "error_message": str(error)[:500] or "对象删除失败",
                },
            )

    async def finalize_knowledge_base_deletion(self, knowledge_id: UUID) -> bool:
        """Purge only after the durable storage-cleanup phase succeeded."""
        async with self.engine.begin() as connection:
            knowledge_result = await connection.execute(
                text("SELECT status FROM knowledge_base WHERE id = :id FOR UPDATE"),
                {"id": knowledge_id},
            )
            status = knowledge_result.scalar_one_or_none()
            if status is None:
                return False
            if status != "DELETING":
                raise ApiError("KNOWLEDGE_BASE_DELETE_CONFLICT", "知识库删除状态已变更", 409)
            await connection.execute(
                text("DELETE FROM rag_trace WHERE kb_id = :knowledge_id"),
                {"knowledge_id": knowledge_id},
            )
            await connection.execute(
                text("DELETE FROM conversation WHERE knowledge_id = :knowledge_id"),
                {"knowledge_id": knowledge_id},
            )
            await connection.execute(
                text("DELETE FROM knowledge_index WHERE kb_id = :knowledge_id"),
                {"knowledge_id": knowledge_id},
            )
            await connection.execute(
                text("DELETE FROM document_source WHERE kb_id = :knowledge_id"),
                {"knowledge_id": knowledge_id},
            )
            await connection.execute(
                text("DELETE FROM knowledge_base WHERE id = :knowledge_id"),
                {"knowledge_id": knowledge_id},
            )
        return True

    async def find_idempotent_response(
        self, operation: str, idempotency_key: str, request_hash: str
    ) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT request_hash, response
                    FROM idempotency_record
                    WHERE operation = :operation AND idempotency_key = :key
                    """
                ),
                {"operation": operation, "key": idempotency_key},
            )
            row = result.mappings().one_or_none()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise ApiError(
                "IDEMPOTENCY_CONFLICT",
                "同一 Idempotency-Key 对应了不同请求",
                409,
            )
        response = row["response"]
        return dict(response) if isinstance(response, dict) else None

    async def create_document(
        self,
        *,
        knowledge_id: UUID,
        document_id: UUID,
        filename: str,
        display_name: str,
        object_key: str,
        file_hash: str,
        file_size: int,
        mime_type: str,
        request_build: bool,
        idempotency_key: str,
        request_hash: str,
    ) -> DocumentCreateResult:
        operation = f"upload_document:{knowledge_id}"
        async with self.engine.begin() as connection:
            kb_result = await connection.execute(
                text("SELECT * FROM knowledge_base WHERE id = :id FOR UPDATE"),
                {"id": knowledge_id},
            )
            kb = kb_result.mappings().one_or_none()
            if kb is None:
                raise ApiError("KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在", 404)
            if kb["status"] != "ENABLED":
                raise ApiError("FORBIDDEN", "知识库已停用", 403)

            replay_result = await connection.execute(
                text(
                    """
                    SELECT request_hash, response
                    FROM idempotency_record
                    WHERE operation = :operation AND idempotency_key = :key
                    FOR UPDATE
                    """
                ),
                {"operation": operation, "key": idempotency_key},
            )
            replay = replay_result.mappings().one_or_none()
            if replay is not None:
                if replay["request_hash"] != request_hash:
                    raise ApiError(
                        "IDEMPOTENCY_CONFLICT",
                        "同一 Idempotency-Key 对应了不同请求",
                        409,
                    )
                response = replay["response"]
                if not isinstance(response, dict):
                    raise ApiError("INTERNAL_ERROR", "幂等请求记录不完整", 500)
                build_data = response["build_request"]
                return DocumentCreateResult(
                    response=dict(response),
                    created=False,
                    build=BuildReference(
                        requested=bool(build_data["requested"]),
                        coalesced=bool(build_data["coalesced"]),
                        index_id=(
                            UUID(build_data["index_id"]) if build_data.get("index_id") else None
                        ),
                        task_id=(
                            UUID(build_data["task_id"]) if build_data.get("task_id") else None
                        ),
                        rebuild_required=bool(build_data["rebuild_required"]),
                    ),
                )

            duplicate = await connection.execute(
                text(
                    """
                    SELECT id FROM document_source
                    WHERE kb_id = :kb_id AND file_hash = :file_hash AND status = 'STORED'
                    """
                ),
                {"kb_id": knowledge_id, "file_hash": file_hash},
            )
            if duplicate.scalar_one_or_none() is not None:
                raise ApiError("DUPLICATE_DOCUMENT", "知识库中已存在相同文档", 409)

            duplicate_name = await connection.execute(
                text(
                    """
                    SELECT id FROM document_source
                    WHERE kb_id = :kb_id AND status = 'STORED'
                      AND lower(display_name) = lower(:display_name)
                    LIMIT 1
                    """
                ),
                {"kb_id": knowledge_id, "display_name": display_name},
            )
            if duplicate_name.scalar_one_or_none() is not None:
                raise ApiError("DOCUMENT_NAME_CONFLICT", "知识库中已存在同名文档", 409)

            document_result = await connection.execute(
                text(
                    """
                    INSERT INTO document_source (
                        id, kb_id, filename, display_name, minio_bucket, minio_object_key,
                        file_hash, file_size, mime_type
                    ) VALUES (
                        :id, :kb_id, :filename, :display_name, :bucket, :object_key,
                        :file_hash, :file_size, :mime_type
                    ) RETURNING *
                    """
                ),
                {
                    "id": document_id,
                    "kb_id": knowledge_id,
                    "filename": filename,
                    "display_name": display_name,
                    "bucket": self.settings.minio_document_bucket,
                    "object_key": object_key,
                    "file_hash": file_hash,
                    "file_size": file_size,
                    "mime_type": mime_type,
                },
            )
            document = dict(document_result.mappings().one())
            build = (
                await self._request_build_locked(
                    connection, knowledge_id, "DOCUMENT_CHANGED", activate_on_success=False
                )
                if request_build
                else BuildReference(False, False, None, None, False)
            )
            response = {
                "document": self._document_response(document),
                "build_request": self._build_response(build),
            }
            await connection.execute(
                text(
                    """
                    INSERT INTO idempotency_record (
                        id, kb_id, operation, idempotency_key, request_hash, response
                    ) VALUES (
                        :id, :kb_id, :operation, :key, :request_hash,
                        CAST(:response AS jsonb)
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "kb_id": knowledge_id,
                    "operation": operation,
                    "key": idempotency_key,
                    "request_hash": request_hash,
                    "response": json.dumps(response, ensure_ascii=False, default=str),
                },
            )
            return DocumentCreateResult(response=response, created=True, build=build)

    async def _request_build_locked(
        self,
        connection: AsyncConnection,
        knowledge_id: UUID,
        reason: str,
        *,
        activate_on_success: bool,
    ) -> BuildReference:
        building_result = await connection.execute(
            text(
                """
                SELECT id FROM knowledge_index
                WHERE kb_id = :kb_id AND status = 'BUILDING'
                FOR UPDATE
                """
            ),
            {"kb_id": knowledge_id},
        )
        building_id = building_result.scalar_one_or_none()
        if building_id is not None:
            await connection.execute(
                text(
                    """
                    UPDATE knowledge_base
                    SET rebuild_required = true, updated_at = now()
                    WHERE id = :kb_id
                    """
                ),
                {"kb_id": knowledge_id},
            )
            return BuildReference(True, True, building_id, None, True)
        return await self._create_build_locked(
            connection,
            knowledge_id,
            reason,
            activate_on_success=activate_on_success,
        )

    async def request_rebuild_for_all_knowledge_bases(
        self, reason: str, *, activate_on_success: bool
    ) -> list[tuple[UUID, BuildReference]]:
        """Create a build for every enabled KB that has documents.

        Used when a build-bound model identity changes (e.g. EMBEDDING):
        the vector space of every index is stale, so a full rebuild is not
        optional.  KBs already building coalesce via rebuild_required.
        """
        builds: list[tuple[UUID, BuildReference]] = []
        async with self.engine.begin() as connection:
            kb_rows = (
                (
                    await connection.execute(
                        text(
                            """
                    SELECT kb.id
                    FROM knowledge_base kb
                    WHERE kb.status = 'ENABLED'
                      AND EXISTS (
                          SELECT 1 FROM document_source d
                          WHERE d.kb_id = kb.id AND d.status = 'STORED'
                      )
                    ORDER BY kb.created_at
                    """
                        )
                    )
                )
                .scalars()
                .all()
            )
            for knowledge_id in kb_rows:
                build = await self._request_build_locked(
                    connection,
                    UUID(str(knowledge_id)),
                    reason,
                    activate_on_success=activate_on_success,
                )
                if build.index_id is not None and build.task_id is not None:
                    builds.append((UUID(str(knowledge_id)), build))
        return builds

    async def delete_document(
        self, document_id: UUID, *, request_build: bool = True
    ) -> DocumentDeleteResult:
        async with self.engine.begin() as connection:
            document_result = await connection.execute(
                text(
                    """
                    SELECT d.id, d.kb_id, d.status, kb.status AS kb_status
                    FROM document_source d
                    JOIN knowledge_base kb ON kb.id = d.kb_id
                    WHERE d.id = :document_id
                    FOR UPDATE OF d, kb
                    """
                ),
                {"document_id": document_id},
            )
            document = document_result.mappings().one_or_none()
            if document is None or document["status"] != "STORED":
                raise ApiError("DOCUMENT_NOT_FOUND", "文档不存在", 404)
            if document["kb_status"] != "ENABLED":
                raise ApiError("FORBIDDEN", "知识库已停用", 403)

            remaining_result = await connection.execute(
                text(
                    """
                    SELECT count(*) FROM document_source
                    WHERE kb_id = :kb_id AND status = 'STORED' AND id <> :document_id
                    """
                ),
                {"kb_id": document["kb_id"], "document_id": document_id},
            )
            if int(remaining_result.scalar_one()) == 0:
                raise ApiError("VALIDATION_ERROR", "知识库至少需要保留一份文档", 409)

            await connection.execute(
                text(
                    """
                    UPDATE document_source
                    SET status = 'DELETED', deleted_at = now(), updated_at = now()
                    WHERE id = :document_id AND status = 'STORED'
                    """
                ),
                {"document_id": document_id},
            )
            # Cards are index-bound derived data.  A soft-deleted source must
            # disappear from card review immediately even while older index
            # snapshots remain available for existing conversation history.
            await connection.execute(
                text("DELETE FROM rag_document_card WHERE document_id = :document_id"),
                {"document_id": document_id},
            )
            build = (
                await self._request_build_locked(
                    connection,
                    UUID(str(document["kb_id"])),
                    "DOCUMENT_CHANGED",
                    activate_on_success=False,
                )
                if request_build
                else BuildReference(False, False, None, None, False)
            )
            return DocumentDeleteResult(document_id=document_id, build=build)

    async def request_build(
        self,
        knowledge_id: UUID,
        reason: str = "MANUAL",
        *,
        activate_on_success: bool = False,
    ) -> BuildReference:
        async with self.engine.begin() as connection:
            kb_result = await connection.execute(
                text("SELECT status FROM knowledge_base WHERE id = :id FOR UPDATE"),
                {"id": knowledge_id},
            )
            status = kb_result.scalar_one_or_none()
            if status is None:
                raise ApiError("KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在", 404)
            if status != "ENABLED":
                raise ApiError("FORBIDDEN", "知识库已停用", 403)
            return await self._request_build_locked(
                connection,
                knowledge_id,
                reason,
                activate_on_success=activate_on_success,
            )

    async def request_build_idempotent(
        self,
        knowledge_id: UUID,
        reason: str,
        *,
        activate_on_success: bool,
        idempotency_key: str,
    ) -> BuildReference:
        operation = f"build_index:{knowledge_id}"
        request_hash = hashlib.sha256(
            json.dumps(
                {"reason": reason, "activate_on_success": activate_on_success},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        async with self.engine.begin() as connection:
            kb_result = await connection.execute(
                text("SELECT status FROM knowledge_base WHERE id = :id FOR UPDATE"),
                {"id": knowledge_id},
            )
            kb_status = kb_result.scalar_one_or_none()
            if kb_status is None:
                raise ApiError("KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在", 404)
            if kb_status != "ENABLED":
                raise ApiError("FORBIDDEN", "知识库已停用", 403)
            replay_result = await connection.execute(
                text(
                    """
                    SELECT request_hash, response FROM idempotency_record
                    WHERE operation = :operation AND idempotency_key = :key
                    FOR UPDATE
                    """
                ),
                {"operation": operation, "key": idempotency_key},
            )
            replay = replay_result.mappings().one_or_none()
            if replay is not None:
                if replay["request_hash"] != request_hash:
                    raise ApiError(
                        "IDEMPOTENCY_CONFLICT",
                        "同一 Idempotency-Key 对应了不同请求",
                        409,
                    )
                response = replay["response"]
                if not isinstance(response, dict):
                    raise ApiError("INTERNAL_ERROR", "幂等请求记录不完整", 500)
                return BuildReference(
                    requested=bool(response["requested"]),
                    coalesced=bool(response["coalesced"]),
                    index_id=UUID(response["index_id"]) if response.get("index_id") else None,
                    task_id=UUID(response["task_id"]) if response.get("task_id") else None,
                    rebuild_required=bool(response["rebuild_required"]),
                    replayed=True,
                )
            build = await self._request_build_locked(
                connection,
                knowledge_id,
                reason,
                activate_on_success=activate_on_success,
            )
            response = self._build_response(build)
            await connection.execute(
                text(
                    """
                    INSERT INTO idempotency_record (
                        id, kb_id, operation, idempotency_key, request_hash, response
                    ) VALUES (
                        :id, :kb_id, :operation, :key, :request_hash, CAST(:response AS jsonb)
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "kb_id": knowledge_id,
                    "operation": operation,
                    "key": idempotency_key,
                    "request_hash": request_hash,
                    "response": json.dumps(response, default=str),
                },
            )
            return build

    async def _create_build_locked(
        self,
        connection: AsyncConnection,
        knowledge_id: UUID,
        reason: str,
        *,
        activate_on_success: bool,
        profile_refresh_after_build: bool = False,
    ) -> BuildReference:
        version_result = await connection.execute(
            text("SELECT COALESCE(max(version), 0) + 1 FROM knowledge_index WHERE kb_id = :kb_id"),
            {"kb_id": knowledge_id},
        )
        version = int(version_result.scalar_one())
        model_result = await connection.execute(
            text(
                """
                SELECT id, model_name FROM model_config
                WHERE model_type = 'EMBEDDING' AND enabled
                """
            )
        )
        model = model_result.mappings().one_or_none()
        if model is None:
            raise ApiError("MODEL_NOT_CONFIGURED", "Embedding 模型未配置", 503)

        index_id = uuid4()
        task_id = uuid4()
        await connection.execute(
            text(
                """
                INSERT INTO knowledge_index (
                    id, kb_id, version, status, embedding_model_id, embedding_model_name,
                    embedding_dimension, build_reason, activate_on_success
                ) VALUES (
                    :id, :kb_id, :version, 'BUILDING', :model_id, :model_name,
                    :dimension, :reason, :activate_on_success
                )
                """
            ),
            {
                "id": index_id,
                "kb_id": knowledge_id,
                "version": version,
                "model_id": model["id"],
                "model_name": model["model_name"],
                "dimension": self.settings.embedding_dimension,
                "reason": reason,
                "activate_on_success": activate_on_success,
            },
        )
        documents_result = await connection.execute(
            text(
                """
                SELECT id, file_hash, mime_type FROM document_source
                WHERE kb_id = :kb_id AND status = 'STORED'
                ORDER BY created_at, id
                """
            ),
            {"kb_id": knowledge_id},
        )
        documents = list(documents_result.mappings())
        if not documents:
            raise ApiError("VALIDATION_ERROR", "知识库没有可构建文档", 409)

        # 查询上一版 RAG Active Index，判断哪些文档可以复用解析结果。
        # active_index_id is kept as a rolling-deployment compatibility alias.
        prev_index_result = await connection.execute(
            text(
                "SELECT COALESCE(rag_active_index_id, active_index_id) "
                "FROM knowledge_base WHERE id = :kb_id"
            ),
            {"kb_id": knowledge_id},
        )
        prev_index_id = prev_index_result.scalar_one_or_none()

        build_modes: dict[Any, str] = {}
        if prev_index_id is not None:
            prev_model_result = await connection.execute(
                text("SELECT embedding_model_name FROM knowledge_index WHERE id = :id"),
                {"id": prev_index_id},
            )
            prev_model_name = prev_model_result.scalar_one_or_none()
            if prev_model_name is not None:
                # This is only a provisional reuse plan. The Worker checks
                # the actual EMBEDDING/OCR/VISION fingerprint per document;
                # an enqueue-time model-name comparison cannot do that.
                # Reuse is gated per format: a stored document only reuses a
                # previous parse result when its parser revision for that
                # format still matches. Bumping the PDF revision must never
                # force DOCX documents into a full re-parse (and re-embed).
                prev_docs_result = await connection.execute(
                    text(
                        """
                        SELECT document_id, source_hash,
                               metadata->>'content_revision' AS content_revision
                        FROM index_document
                        WHERE index_id = :index_id AND status = 'READY'
                        """
                    ),
                    {"index_id": prev_index_id},
                )
                prev_docs = {
                    row["document_id"]: (row["source_hash"], row["content_revision"])
                    for row in prev_docs_result.mappings()
                }
                for document in documents:
                    doc_id = document["id"]
                    expected_revision = content_revision_for_mime(document["mime_type"])
                    previous = prev_docs.get(doc_id)
                    if (
                        previous is not None
                        and previous[0] == document["file_hash"]
                        and previous[1] == expected_revision
                    ):
                        build_modes[doc_id] = "REUSE"
                    else:
                        build_modes[doc_id] = "REBUILD"
            else:
                for document in documents:
                    build_modes[document["id"]] = "REBUILD"
        else:
            for document in documents:
                build_modes[document["id"]] = "REBUILD"

        await connection.execute(
            text(
                """
                INSERT INTO index_document (
                    id, index_id, document_id, source_hash, status, metadata
                ) VALUES (
                    :id, :index_id, :document_id, :source_hash, 'QUEUED',
                    CAST(:metadata AS jsonb)
                )
                """
            ),
            [
                {
                    "id": uuid4(),
                    "index_id": index_id,
                    "document_id": document["id"],
                    "source_hash": document["file_hash"],
                    "metadata": json.dumps({"build_mode": build_modes[document["id"]]}),
                }
                for document in documents
            ],
        )
        await connection.execute(
            text(
                """
                INSERT INTO task_record (
                    id, task_type, status, stage, progress, kb_id, index_id,
                    metadata
                ) VALUES (
                    :id, 'INDEX_BUILD', 'QUEUED', 'QUEUED', 0, :kb_id, :index_id,
                    CAST(:metadata AS jsonb)
                )
                """
            ),
            {
                "id": task_id,
                "kb_id": knowledge_id,
                "index_id": index_id,
                "metadata": json.dumps(
                    {
                        "document_count": len(documents),
                        **(
                            {"profile_refresh_after_build": True}
                            if profile_refresh_after_build
                            else {}
                        ),
                    }
                ),
            },
        )
        if not activate_on_success:
            # A staged index and its Profile drafts are one release candidate.
            # Cancelling older open candidates prevents stale releases from
            # being published after a newer index has been built.
            await self._cancel_open_releases(connection, knowledge_id)
            await connection.execute(
                text(
                    """
                    INSERT INTO knowledge_release (id, kb_id, index_id, status)
                    VALUES (:id, :kb_id, :index_id, 'DRAFT')
                    ON CONFLICT (index_id) DO NOTHING
                    """
                ),
                {"id": uuid4(), "kb_id": knowledge_id, "index_id": index_id},
            )
        await connection.execute(
            text(
                """
                UPDATE knowledge_base
                SET rebuild_required = false, updated_at = now()
                WHERE id = :kb_id
                """
            ),
            {"kb_id": knowledge_id},
        )
        return BuildReference(True, False, index_id, task_id, False)

    @staticmethod
    async def _cancel_open_releases(connection: AsyncConnection, knowledge_id: UUID) -> None:
        await connection.execute(
            text(
                """
                UPDATE knowledge_release
                SET status = 'CANCELLED', updated_at = now()
                WHERE kb_id = :kb_id
                  AND status NOT IN ('PUBLISHED', 'CANCELLED')
                """
            ),
            {"kb_id": knowledge_id},
        )

    async def get_active_index_id(self, knowledge_id: UUID) -> UUID | None:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT COALESCE(rag_active_index_id, active_index_id) "
                    "FROM knowledge_base WHERE id = :id"
                ),
                {"id": knowledge_id},
            )
            return result.scalar_one_or_none()

    async def list_documents(self, knowledge_id: UUID) -> list[dict[str, Any]]:
        await self.get_knowledge_base(knowledge_id)
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT d.*, last_idoc.status AS last_build_status,
                           last_idoc.error_message AS last_build_error,
                           last_idx.index_version AS last_build_version
                    FROM document_source d
                    LEFT JOIN LATERAL (
                        SELECT i.id AS index_id, i.version AS index_version
                        FROM knowledge_index i
                        WHERE i.kb_id = d.kb_id
                        ORDER BY i.created_at DESC, i.version DESC
                        LIMIT 1
                    ) last_idx ON true
                    LEFT JOIN LATERAL (
                        SELECT idoc.status, idoc.error_message
                        FROM index_document idoc
                        WHERE idoc.index_id = last_idx.index_id
                          AND idoc.document_id = d.id
                        LIMIT 1
                    ) last_idoc ON true
                    WHERE d.kb_id = :kb_id AND d.status = 'STORED'
                    ORDER BY d.created_at DESC
                    """
                ),
                {"kb_id": knowledge_id},
            )
            return [self._document_response(dict(row)) for row in result.mappings()]

    async def count_document_build_failures(
        self, knowledge_id: UUID, document_id: UUID, error_code: str
    ) -> int:
        """Count how often a document already failed a build with this error.

        Used by the pipeline to cap retries: after two aborted builds with the
        same error the document is skipped instead of failing every followup.
        """
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT count(*) FROM index_document idoc
                    JOIN knowledge_index i ON i.id = idoc.index_id
                    WHERE i.kb_id = :kb_id AND idoc.document_id = :document_id
                      AND idoc.status = 'FAILED' AND idoc.error_code = :error_code
                    """
                ),
                {
                    "kb_id": knowledge_id,
                    "document_id": document_id,
                    "error_code": error_code,
                },
            )
            return int(result.scalar_one_or_none() or 0)

    async def get_document(self, document_id: UUID) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text("SELECT * FROM document_source WHERE id = :id"), {"id": document_id}
            )
            row = result.mappings().one_or_none()
        if row is None:
            raise ApiError("DOCUMENT_NOT_FOUND", "文档不存在", 404)
        return self._document_response(dict(row))

    async def rename_document(self, document_id: UUID, display_name: str) -> dict[str, Any]:
        async with self.engine.begin() as connection:
            document_result = await connection.execute(
                text("SELECT kb_id FROM document_source WHERE id = :id AND status = 'STORED'"),
                {"id": document_id},
            )
            knowledge_id = document_result.scalar_one_or_none()
            if knowledge_id is None:
                raise ApiError("DOCUMENT_NOT_FOUND", "文档不存在", 404)

            knowledge_result = await connection.execute(
                text("SELECT status FROM knowledge_base WHERE id = :id FOR UPDATE"),
                {"id": knowledge_id},
            )
            knowledge_status = knowledge_result.scalar_one_or_none()
            if knowledge_status != "ENABLED":
                raise ApiError("FORBIDDEN", "知识库已停用", 403)

            locked_document = await connection.execute(
                text(
                    "SELECT id FROM document_source "
                    "WHERE id = :id AND kb_id = :kb_id AND status = 'STORED' FOR UPDATE"
                ),
                {"id": document_id, "kb_id": knowledge_id},
            )
            if locked_document.scalar_one_or_none() is None:
                raise ApiError("DOCUMENT_NOT_FOUND", "文档不存在", 404)

            duplicate = await connection.execute(
                text(
                    """
                    SELECT id FROM document_source
                    WHERE kb_id = :kb_id AND status = 'STORED' AND id <> :id
                      AND lower(display_name) = lower(:display_name)
                    LIMIT 1
                    """
                ),
                {"id": document_id, "kb_id": knowledge_id, "display_name": display_name},
            )
            if duplicate.scalar_one_or_none() is not None:
                raise ApiError("DOCUMENT_NAME_CONFLICT", "知识库中已存在同名文档", 409)

            updated = await connection.execute(
                text(
                    """
                    UPDATE document_source
                    SET display_name = :display_name, updated_at = now()
                    WHERE id = :id
                    RETURNING *
                    """
                ),
                {"id": document_id, "display_name": display_name},
            )
            return self._document_response(dict(updated.mappings().one()))

    async def get_index(self, index_id: UUID) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text("SELECT * FROM knowledge_index WHERE id = :id"), {"id": index_id}
            )
            row = result.mappings().one_or_none()
        if row is None:
            raise ApiError("INDEX_NOT_FOUND", "索引不存在", 404)
        return dict(row)

    async def list_indexes(self, knowledge_id: UUID) -> list[dict[str, Any]]:
        await self.get_knowledge_base(knowledge_id)
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT * FROM knowledge_index
                    WHERE kb_id = :kb_id
                    ORDER BY version DESC
                    """
                ),
                {"kb_id": knowledge_id},
            )
            return [dict(row) for row in result.mappings()]

    async def list_index_tasks(self, index_id: UUID) -> list[dict[str, Any]]:
        await self.get_index(index_id)
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT * FROM task_record
                    WHERE index_id = :index_id
                    ORDER BY created_at DESC
                    """
                ),
                {"index_id": index_id},
            )
            return [dict(row) for row in result.mappings()]

    async def list_tasks(
        self,
        *,
        status: str | None = None,
        knowledge_id: UUID | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Cross-index task listing for failure review (worker builds, GC,
        recovery); backed by ix_task_record_status_created."""
        clauses = ["1 = 1"]
        parameters: dict[str, Any] = {"limit": limit}
        if status is not None:
            clauses.append("status = :status")
            parameters["status"] = status
        if knowledge_id is not None:
            clauses.append("kb_id = :kb_id")
            parameters["kb_id"] = knowledge_id
        if created_from is not None:
            clauses.append("created_at >= :created_from")
            parameters["created_from"] = created_from
        if created_to is not None:
            clauses.append("created_at < :created_to")
            parameters["created_to"] = created_to
        query = f"""
            SELECT * FROM task_record
            WHERE {" AND ".join(clauses)}
            ORDER BY created_at DESC
            LIMIT :limit
        """
        async with self.engine.connect() as connection:
            result = await connection.execute(text(query), parameters)
            return [dict(row) for row in result.mappings()]

    async def activate_index(self, index_id: UUID) -> dict[str, Any]:
        """Publish a READY snapshot and retire the prior ACTIVE snapshot atomically."""
        async with self.engine.begin() as connection:
            target_result = await connection.execute(
                text(
                    """
                    SELECT i.*, COALESCE(kb.rag_active_index_id, kb.active_index_id)
                                      AS active_index_id
                    FROM knowledge_index i
                    JOIN knowledge_base kb ON kb.id = i.kb_id
                    WHERE i.id = :index_id
                    FOR UPDATE OF kb, i
                    """
                ),
                {"index_id": index_id},
            )
            target = target_result.mappings().one_or_none()
            if target is None:
                raise ApiError("INDEX_NOT_FOUND", "索引不存在", 404)
            if target["status"] == "ACTIVE" and target["active_index_id"] == index_id:
                return dict(target)
            if target["status"] != "READY":
                raise ApiError("INDEX_NOT_READY", "只有 READY 索引可以激活", 409)

            old_index_id = target["active_index_id"]
            if old_index_id is not None:
                await connection.execute(
                    text(
                        """
                        UPDATE knowledge_index SET status = 'DEPRECATED'
                        WHERE id = :old_index_id AND status = 'ACTIVE'
                        """
                    ),
                    {"old_index_id": old_index_id},
                )
            activated_result = await connection.execute(
                text(
                    """
                    UPDATE knowledge_index
                    SET status = 'ACTIVE', activated_at = now()
                    WHERE id = :index_id AND status = 'READY'
                    RETURNING *
                    """
                ),
                {"index_id": index_id},
            )
            activated = activated_result.mappings().one()
            await connection.execute(
                text(
                    """
                    UPDATE knowledge_base
                    SET rag_active_index_id = :index_id,
                        active_index_id = :index_id, updated_at = now()
                    WHERE id = :kb_id
                    """
                ),
                {"index_id": index_id, "kb_id": target["kb_id"]},
            )
            return dict(activated)

    async def rollback_rag_index(self, knowledge_id: UUID, index_id: UUID) -> dict[str, Any]:
        """Explicitly roll the RAG online index back to a chosen snapshot.

        The target version is always supplied by the caller — it is never
        inferred from timestamps or "latest successful build".  Only the RAG
        lifecycle moves: Support pointers and frozen conversation versions
        stay untouched, so existing sessions keep their snapshot while new
        sessions bind to the rollback target.
        """
        async with self.engine.begin() as connection:
            kb_result = await connection.execute(
                text(
                    """
                    SELECT status,
                           COALESCE(rag_active_index_id, active_index_id)
                               AS rag_active_index_id
                    FROM knowledge_base WHERE id = :kb_id FOR UPDATE
                    """
                ),
                {"kb_id": knowledge_id},
            )
            kb = kb_result.mappings().one_or_none()
            if kb is None:
                raise ApiError("KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在", 404)
            if kb["status"] != "ENABLED":
                raise ApiError("FORBIDDEN", "知识库已停用", 403)
            target_result = await connection.execute(
                text(
                    """
                    SELECT * FROM knowledge_index
                    WHERE id = :index_id AND kb_id = :kb_id FOR UPDATE
                    """
                ),
                {"index_id": index_id, "kb_id": knowledge_id},
            )
            target = target_result.mappings().one_or_none()
            if target is None:
                raise ApiError("INDEX_NOT_FOUND", "目标索引不存在", 404)
            if target["status"] not in ("READY", "ACTIVE", "DEPRECATED"):
                raise ApiError("INDEX_NOT_ROLLBACKABLE", "目标索引状态不可用于回滚", 409)
            current_index_id = kb["rag_active_index_id"]
            if current_index_id is not None and UUID(str(current_index_id)) == index_id:
                # Idempotent: the requested version is already online.
                return dict(target)
            if current_index_id is not None:
                await connection.execute(
                    text(
                        """
                        UPDATE knowledge_index SET status = 'DEPRECATED'
                        WHERE id = :old_index_id AND status = 'ACTIVE'
                        """
                    ),
                    {"old_index_id": current_index_id},
                )
            rolled_back = await connection.execute(
                text(
                    """
                    UPDATE knowledge_index
                    SET status = 'ACTIVE', activated_at = now()
                    WHERE id = :index_id
                      AND status IN ('READY', 'ACTIVE', 'DEPRECATED')
                    RETURNING *
                    """
                ),
                {"index_id": index_id},
            )
            row = rolled_back.mappings().one()
            # active_index_id remains the rolling compatibility alias of the
            # RAG pointer; Support pointers are deliberately not touched.
            await connection.execute(
                text(
                    """
                    UPDATE knowledge_base
                    SET rag_active_index_id = :index_id,
                        active_index_id = :index_id, updated_at = now()
                    WHERE id = :kb_id
                    """
                ),
                {"index_id": index_id, "kb_id": knowledge_id},
            )
            return dict(row)

    async def purge_expired_conversations(
        self, retention_days: int, kb_id: UUID | None = None
    ) -> list[UUID]:
        """Delete long-idle conversations past the retention window.

        Frozen conversations are what keeps old indexes alive for GC.  This
        is the auditable release valve: only conversations without any
        activity inside the retention window are removed, and the caller
        reports exactly which ids were purged.  Messages cascade; traces
        survive until their index is itself collected.  ``kb_id`` scopes the
        purge to one knowledge base; without it the whole fleet is pruned.
        """
        query = """
            DELETE FROM conversation
            WHERE last_activity_at < now() - INTERVAL '1 day' * :retention_days
        """
        parameters: dict[str, Any] = {"retention_days": retention_days}
        if kb_id is not None:
            query += " AND knowledge_id = :kb_id"
            parameters["kb_id"] = kb_id
        query += " RETURNING id"
        async with self.engine.begin() as connection:
            result = await connection.execute(text(query), parameters)
            return [UUID(str(row[0])) for row in result]

    async def retain_traces(
        self, full_retention_days: int, summary_retention_days: int
    ) -> dict[str, int]:
        """Redact detailed trace data before deleting its operations summary."""
        async with self.engine.begin() as connection:
            redacted = await connection.execute(
                text(
                    """
                    UPDATE rag_trace
                    SET retrieval_result = '{}'::jsonb, rerank_result = '{}'::jsonb,
                        selected_context = '[]'::jsonb, prompt = NULL, answer = NULL,
                        sources = '[]'::jsonb, model_usage = '{}'::jsonb,
                        runtime_snapshot = '{}'::jsonb, redacted_at = now()
                    WHERE redacted_at IS NULL
                      AND created_at < now() - INTERVAL '1 day' * :full_days
                    """
                ),
                {"full_days": full_retention_days},
            )
            deleted = await connection.execute(
                text(
                    "DELETE FROM rag_trace WHERE created_at < now() "
                    "- INTERVAL '1 day' * :summary_days"
                ),
                {"summary_days": summary_retention_days},
            )
        return {"redacted": int(redacted.rowcount or 0), "deleted": int(deleted.rowcount or 0)}

    async def retry_index(
        self, index_id: UUID, *, activate_on_success: bool = False
    ) -> BuildReference:
        async with self.engine.begin() as connection:
            failed_result = await connection.execute(
                text(
                    """
                    SELECT i.kb_id, i.status, kb.status AS kb_status
                    FROM knowledge_index i
                    JOIN knowledge_base kb ON kb.id = i.kb_id
                    WHERE i.id = :index_id
                    FOR UPDATE OF kb
                    """
                ),
                {"index_id": index_id},
            )
            failed = failed_result.mappings().one_or_none()
            if failed is None:
                raise ApiError("INDEX_NOT_FOUND", "索引不存在", 404)
            if failed["status"] not in ("FAILED", "CANCELLED"):
                raise ApiError("INDEX_NOT_READY", "只有 FAILED 或 CANCELLED 索引可以重试", 409)
            if failed["kb_status"] != "ENABLED":
                raise ApiError("FORBIDDEN", "知识库已停用", 403)
            return await self._request_build_locked(
                connection,
                UUID(str(failed["kb_id"])),
                "REPROCESS",
                activate_on_success=activate_on_success,
            )

    async def get_task(self, task_id: UUID) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text("SELECT * FROM task_record WHERE id = :id"), {"id": task_id}
            )
            row = result.mappings().one_or_none()
        if row is None:
            raise ApiError("TASK_NOT_FOUND", "任务不存在", 404)
        return dict(row)

    async def list_running_index_build_tasks(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT t.id AS task_id, t.index_id, i.kb_id, t.status AS task_status
                    FROM task_record t
                    JOIN knowledge_index i ON i.id = t.index_id
                    WHERE t.task_type = 'INDEX_BUILD'
                      AND t.status IN ('QUEUED', 'RUNNING', 'CANCELLED')
                      AND i.status = 'BUILDING'
                    ORDER BY t.created_at
                    """
                )
            )
            return [dict(row) for row in result.mappings()]

    async def mark_abandoned_index_build_failed(
        self,
        index_id: UUID,
        task_id: UUID,
        *,
        rq_status: str,
    ) -> bool:
        error_code = "ABANDONED_JOB"
        error_message = f"索引构建执行进程已丢失，RQ 状态：{rq_status}"
        async with self.engine.begin() as connection:
            state_result = await connection.execute(
                text(
                    """
                    SELECT t.status AS task_status, i.status AS index_status
                    FROM task_record t
                    JOIN knowledge_index i ON i.id = t.index_id
                    WHERE t.id = :task_id AND i.id = :index_id
                    FOR UPDATE OF t, i
                    """
                ),
                {"task_id": task_id, "index_id": index_id},
            )
            state = state_result.mappings().one_or_none()
            if (
                state is None
                or state["task_status"] not in {"QUEUED", "RUNNING", "CANCELLED"}
                or state["index_status"] != "BUILDING"
            ):
                return False
            # 任务行已是 CANCELLED（管理员请求取消后执行进程死亡，构建
            # 循环没来得及在文档边界收尾）：索引按取消语义落 CANCELLED
            # 而不是 FAILED，任务行保持终态不动。
            if state["task_status"] == "CANCELLED":
                await connection.execute(
                    text(
                        """
                        UPDATE index_document
                        SET status = 'CANCELLED', error_code = 'CANCELLED',
                            error_message = '用户中止构建', finished_at = now()
                        WHERE index_id = :index_id AND status NOT IN ('READY', 'SKIPPED')
                        """
                    ),
                    {"index_id": index_id},
                )
                await connection.execute(
                    text(
                        """
                        UPDATE knowledge_index
                        SET status = 'CANCELLED', error_code = 'CANCELLED',
                            error_message = '用户中止构建', finished_at = now()
                        WHERE id = :index_id AND status = 'BUILDING'
                        """
                    ),
                    {"index_id": index_id},
                )
                await connection.execute(
                    text(
                        """
                        UPDATE knowledge_base
                        SET rebuild_required = true, updated_at = now()
                        WHERE id = (SELECT kb_id FROM knowledge_index WHERE id = :index_id)
                        """
                    ),
                    {"index_id": index_id},
                )
                return True
            parameters = {
                "index_id": index_id,
                "task_id": task_id,
                "error_code": error_code,
                "error_message": error_message,
            }
            await connection.execute(
                text(
                    """
                    UPDATE index_document
                    SET status = 'FAILED', error_code = :error_code,
                        error_message = :error_message, finished_at = now()
                    WHERE index_id = :index_id AND status <> 'READY'
                    """
                ),
                parameters,
            )
            await connection.execute(
                text(
                    """
                    UPDATE knowledge_index
                    SET status = 'FAILED', error_code = :error_code,
                        error_message = :error_message, finished_at = now()
                    WHERE id = :index_id AND status = 'BUILDING'
                    """
                ),
                parameters,
            )
            # 中断的构建同样意味着知识库内容与活动索引不一致：置位
            # rebuild_required 让管理台持续提示待重建（ADR-0024：不自动
            # 重试，由管理员决定处置）。
            await connection.execute(
                text(
                    """
                    UPDATE knowledge_base
                    SET rebuild_required = true, updated_at = now()
                    WHERE id = (SELECT kb_id FROM knowledge_index WHERE id = :index_id)
                    """
                ),
                parameters,
            )
            await connection.execute(
                text(
                    """
                    UPDATE task_record
                    SET status = 'FAILED', stage = 'FAILED', error_code = :error_code,
                        error_message = :error_message, finished_at = now(), updated_at = now(),
                        metadata = metadata || jsonb_build_object(
                            'rq_status', CAST(:rq_status AS text)
                        )
                    WHERE id = :task_id AND status IN ('QUEUED', 'RUNNING')
                    """
                ),
                {**parameters, "rq_status": rq_status},
            )
            return True

    async def mark_build_enqueue_failed(self, index_id: UUID, task_id: UUID) -> None:
        async with self.engine.begin() as connection:
            parameters = {
                "index_id": index_id,
                "task_id": task_id,
                "error_code": "QUEUE_UNAVAILABLE",
                "error_message": "构建任务投递失败",
            }
            await connection.execute(
                text(
                    """
                    UPDATE knowledge_index
                    SET status = 'FAILED', error_code = :error_code,
                        error_message = :error_message, finished_at = now()
                    WHERE id = :index_id AND status = 'BUILDING'
                    """
                ),
                parameters,
            )
            await connection.execute(
                text(
                    """
                    UPDATE task_record
                    SET status = 'FAILED', stage = 'QUEUE', error_code = :error_code,
                        error_message = :error_message, finished_at = now(), updated_at = now()
                    WHERE id = :task_id
                    """
                ),
                parameters,
            )

    async def recover_failed_enqueue(self, index_id: UUID, task_id: UUID) -> bool:
        """Return a queue-delivery failure to its pre-enqueue state exactly once."""
        async with self.engine.begin() as connection:
            state_result = await connection.execute(
                text(
                    """
                    SELECT i.status AS index_status, i.error_code AS index_error_code,
                           t.status AS task_status, t.stage AS task_stage,
                           t.error_code AS task_error_code
                    FROM knowledge_index i
                    JOIN task_record t ON t.index_id = i.id
                    WHERE i.id = :index_id AND t.id = :task_id
                    FOR UPDATE OF i, t
                    """
                ),
                {"index_id": index_id, "task_id": task_id},
            )
            state = state_result.mappings().one_or_none()
            if (
                state is None
                or state["index_status"] != "FAILED"
                or state["index_error_code"] != "QUEUE_UNAVAILABLE"
                or state["task_status"] != "FAILED"
                or state["task_stage"] != "QUEUE"
                or state["task_error_code"] != "QUEUE_UNAVAILABLE"
            ):
                return False
            await connection.execute(
                text(
                    """
                    UPDATE knowledge_index
                    SET status = 'BUILDING', error_code = NULL, error_message = NULL,
                        finished_at = NULL
                    WHERE id = :index_id
                    """
                ),
                {"index_id": index_id},
            )
            await connection.execute(
                text(
                    """
                    UPDATE task_record
                    SET status = 'QUEUED', stage = 'QUEUED', progress = 0,
                        error_code = NULL, error_message = NULL,
                        started_at = NULL, finished_at = NULL, updated_at = now()
                    WHERE id = :task_id
                    """
                ),
                {"task_id": task_id},
            )
            return True

    async def find_deletable_indexes(
        self, retention_count: int, retention_days: int
    ) -> list[GarbageCollectionItem]:
        """Find DEPRECATED, FAILED or CANCELLED indexes that exceed the retention policy."""
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    WITH ranked AS (
                        SELECT id, kb_id, version, status, created_at,
                               ROW_NUMBER() OVER (
                                   PARTITION BY kb_id ORDER BY version DESC
                               ) AS rn
                        FROM knowledge_index
                        WHERE status IN ('DEPRECATED', 'FAILED', 'CANCELLED')
                    )
                    SELECT id, kb_id, version, status
                    FROM ranked
                    WHERE rn > :retention_count
                      AND created_at < now() - INTERVAL '1 day' * :retention_days
                       AND NOT EXISTS (
                           SELECT 1 FROM knowledge_base kb
                           WHERE ranked.id IN (
                               kb.active_index_id, kb.rag_active_index_id,
                               kb.support_active_index_id
                           )
                       )
                       AND NOT EXISTS (
                           SELECT 1 FROM knowledge_base kb2
                           JOIN knowledge_release active_release
                             ON active_release.id = kb2.support_active_release_id
                           WHERE active_release.index_id = ranked.id
                       )
                       AND NOT EXISTS (
                           SELECT 1 FROM knowledge_release r
                           WHERE r.index_id = ranked.id
                             AND r.status <> 'CANCELLED'
                       )
                       AND NOT EXISTS (
                           SELECT 1 FROM task_record t
                           WHERE t.index_id = ranked.id
                             AND t.status IN ('QUEUED', 'RUNNING')
                       )
                       AND NOT EXISTS (
                           SELECT 1 FROM conversation c
                           WHERE ranked.id IN (
                               c.rag_index_id, c.support_index_id, c.focus_index_id
                           )
                       )
                       AND kb_id NOT IN (
                           SELECT kb_id FROM knowledge_index WHERE status = 'BUILDING'
                       )
                     ORDER BY created_at
                    """
                ),
                {
                    "retention_count": retention_count,
                    "retention_days": retention_days,
                },
            )
            return [
                GarbageCollectionItem(
                    index_id=UUID(str(row["id"])),
                    kb_id=UUID(str(row["kb_id"])),
                    version=int(row["version"]),
                    status=row["status"],
                )
                for row in result.mappings()
            ]

    async def find_deleting_indexes(self) -> list[GarbageCollectionItem]:
        """Find indexes stuck in DELETING state for crash recovery."""
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, kb_id, version, status
                    FROM knowledge_index
                    WHERE status = 'DELETING'
                    ORDER BY created_at
                    """
                ),
            )
            return [
                GarbageCollectionItem(
                    index_id=UUID(str(row["id"])),
                    kb_id=UUID(str(row["kb_id"])),
                    version=int(row["version"]),
                    status=row["status"],
                )
                for row in result.mappings()
            ]

    async def mark_index_deleting(self, index_id: UUID) -> dict[str, Any] | None:
        """Atomically mark a DEPRECATED or FAILED index as DELETING."""
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE knowledge_index
                    SET status = 'DELETING'
                    WHERE id = :index_id AND status IN ('DEPRECATED', 'FAILED')
                      AND NOT EXISTS (
                          SELECT 1 FROM knowledge_base kb
                          WHERE knowledge_index.id IN (
                              kb.active_index_id, kb.rag_active_index_id,
                              kb.support_active_index_id
                          )
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM knowledge_base kb2
                          JOIN knowledge_release active_release
                            ON active_release.id = kb2.support_active_release_id
                          WHERE active_release.index_id = knowledge_index.id
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM knowledge_release r
                          WHERE r.index_id = knowledge_index.id
                            AND r.status <> 'CANCELLED'
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM task_record t
                          WHERE t.index_id = knowledge_index.id
                            AND t.status IN ('QUEUED', 'RUNNING')
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM conversation c
                          WHERE knowledge_index.id IN (
                              c.rag_index_id, c.support_index_id, c.focus_index_id
                          )
                      )
                    RETURNING *
                    """
                ),
                {"index_id": index_id},
            )
            row = result.mappings().one_or_none()
            return dict(row) if row is not None else None

    async def get_index_deletion_blockers(self, index_id: UUID) -> dict[str, Any] | None:
        """Return the live references that protect an index from deletion.

        The same predicates are used by :meth:`mark_index_deleting` and the
        final delete transaction.  Keeping this diagnostic query separate
        lets the API explain a rejected delete without weakening either
        atomic protection point.
        """
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT i.status,
                           (SELECT count(*) FROM knowledge_base kb
                            WHERE kb.active_index_id = i.id) AS active_index_pointer_count,
                           (SELECT count(*) FROM knowledge_base kb
                            WHERE kb.rag_active_index_id = i.id) AS rag_active_pointer_count,
                           (SELECT count(*) FROM knowledge_base kb
                            WHERE kb.support_active_index_id = i.id)
                              AS support_active_pointer_count,
                           (SELECT count(*) FROM knowledge_base kb
                            JOIN knowledge_release r
                              ON r.id = kb.support_active_release_id
                            WHERE r.index_id = i.id) AS support_active_release_count,
                           (SELECT count(*) FROM knowledge_release r
                            WHERE r.index_id = i.id AND r.status <> 'CANCELLED')
                              AS open_release_count,
                           (SELECT count(*) FROM task_record t
                            WHERE t.index_id = i.id
                              AND t.status IN ('QUEUED', 'RUNNING')) AS active_task_count,
                           (SELECT count(*) FROM conversation c
                            WHERE i.id IN (
                                c.rag_index_id, c.support_index_id, c.focus_index_id
                            )) AS frozen_conversation_count
                    FROM knowledge_index i
                    WHERE i.id = :index_id
                    """
                ),
                {"index_id": index_id},
            )
            row = result.mappings().one_or_none()
            return dict(row) if row is not None else None

    async def release_frozen_conversation_references(self, index_id: UUID) -> int:
        """Detach conversations frozen to this index so it can be force-deleted.

        RAG and Support conversations re-bind to the current active index on
        their next message; focused Support conversations reset to generic
        Support mode. Messages
        survive untouched.  Returns the number of released column
        references; a conversation frozen on the same index in several
        columns counts once per column.
        """
        async with self.engine.begin() as connection:
            rag = await connection.execute(
                text("UPDATE conversation SET rag_index_id = NULL WHERE rag_index_id = :index_id"),
                {"index_id": index_id},
            )
            support = await connection.execute(
                text(
                    """
                    UPDATE conversation
                    SET support_index_id = NULL, support_release_id = NULL
                    WHERE support_index_id = :index_id
                    """
                ),
                {"index_id": index_id},
            )
            focus = await connection.execute(
                text(
                    """
                    UPDATE conversation
                    SET focus_document_id = NULL, focus_index_id = NULL,
                        chat_context = '{}'::jsonb, pending_switch_options = '[]'::jsonb
                    WHERE focus_index_id = :index_id
                    """
                ),
                {"index_id": index_id},
            )
            return sum(int(result.rowcount or 0) for result in (rag, support, focus))

    async def list_image_assets_for_index(self, index_id: UUID) -> list[dict[str, str]]:
        """List MinIO bucket/object_key pairs for image assets belonging to an index."""
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT DISTINCT minio_bucket, minio_object_key
                    FROM image_asset
                    WHERE index_id = :index_id
                    """
                ),
                {"index_id": index_id},
            )
            return [dict(row) for row in result.mappings()]

    async def delete_index(self, index_id: UUID) -> bool:
        """Physically delete an index row. CASCADE handles child tables.
        rag_trace references the index with ON DELETE RESTRICT, so those
        rows must be removed first. The index must be in DELETING status
        and is re-checked against live references inside the same
        transaction: a pointer that appeared after the DELETING mark
        restores the index instead of destroying protected data."""
        async with self.engine.begin() as connection:
            referenced = await connection.execute(
                text(
                    """
                    SELECT 1
                    WHERE EXISTS (
                        SELECT 1 FROM knowledge_base kb
                        WHERE :index_id IN (
                            kb.active_index_id, kb.rag_active_index_id,
                            kb.support_active_index_id
                        )
                    )
                    OR EXISTS (
                        SELECT 1 FROM knowledge_base kb2
                        JOIN knowledge_release active_release
                          ON active_release.id = kb2.support_active_release_id
                        WHERE active_release.index_id = :index_id
                    )
                    OR EXISTS (
                        SELECT 1 FROM knowledge_release r
                        WHERE r.index_id = :index_id AND r.status <> 'CANCELLED'
                    )
                    OR EXISTS (
                        SELECT 1 FROM task_record t
                        WHERE t.index_id = :index_id
                          AND t.status IN ('QUEUED', 'RUNNING')
                    )
                    OR EXISTS (
                        SELECT 1 FROM conversation c
                        WHERE :index_id IN (
                            c.rag_index_id, c.support_index_id, c.focus_index_id
                        )
                    )
                    """
                ),
                {"index_id": index_id},
            )
            if referenced.scalar_one_or_none() is not None:
                await connection.execute(
                    text(
                        """
                        UPDATE knowledge_index SET status = 'DEPRECATED'
                        WHERE id = :index_id AND status = 'DELETING'
                        """
                    ),
                    {"index_id": index_id},
                )
                return False
            result = await connection.execute(
                text(
                    """
                    DELETE FROM knowledge_index
                    WHERE id = :index_id AND status = 'DELETING'
                    """
                ),
                {"index_id": index_id},
            )
            return result.rowcount > 0

    async def _resolve_index_for_document(
        self, connection: AsyncConnection, document_id: UUID, index_id: UUID | None
    ) -> UUID:
        if index_id is not None:
            result = await connection.execute(
                text(
                    """
                    SELECT id FROM index_document
                    WHERE document_id = :document_id AND index_id = :index_id
                    """
                ),
                {"document_id": document_id, "index_id": index_id},
            )
            if result.scalar_one_or_none() is None:
                raise ApiError("INDEX_NOT_FOUND", "文档在指定索引中不存在", 404)
            return index_id
        result = await connection.execute(
            text(
                """
                SELECT COALESCE(kb.rag_active_index_id, kb.active_index_id)
                FROM document_source d JOIN knowledge_base kb ON kb.id = d.kb_id
                WHERE d.id = :document_id
                """
            ),
            {"document_id": document_id},
        )
        active_index_id = result.scalar_one_or_none()
        if active_index_id is None:
            raise ApiError("KNOWLEDGE_BASE_NOT_READY", "知识库尚无 Active Index", 409)
        return UUID(str(active_index_id))

    async def list_elements(
        self, document_id: UUID, index_id: UUID | None
    ) -> tuple[UUID, list[dict[str, Any]]]:
        async with self.engine.connect() as connection:
            resolved = await self._resolve_index_for_document(connection, document_id, index_id)
            result = await connection.execute(
                text(
                    """
                    SELECT e.*, a.id AS image_asset_id, a.minio_bucket AS image_bucket,
                           a.minio_object_key AS image_object_key, a.mime_type AS image_mime_type,
                           a.ocr_text, a.vision_caption, a.ocr_status, a.vision_status,
                           a.ocr_provider, a.ocr_model_name, a.ocr_error_code,
                           a.vision_provider, a.vision_model_name, a.vision_error_code,
                           a.processed_at AS image_processed_at
                    FROM document_element e
                    LEFT JOIN image_asset a ON a.element_id = e.id
                    WHERE e.document_id = :document_id AND e.index_id = :index_id
                    ORDER BY e.sequence_no
                    """
                ),
                {"document_id": document_id, "index_id": resolved},
            )
            return resolved, [dict(row) for row in result.mappings()]

    async def list_chunks(
        self, document_id: UUID, index_id: UUID | None
    ) -> tuple[UUID, list[dict[str, Any]]]:
        async with self.engine.connect() as connection:
            resolved = await self._resolve_index_for_document(connection, document_id, index_id)
            result = await connection.execute(
                text(
                    """
                    SELECT id, kb_id, index_id, index_document_id, document_id, chunk_type,
                           sequence_no, content, search_text, token_count, section_path,
                           metadata, parent_id, previous_chunk_id, next_chunk_id,
                           suspected_incomplete, incomplete_reasons, is_procedural,
                           created_at, embedding IS NOT NULL AS embedding_ready
                    FROM document_chunk
                    WHERE document_id = :document_id AND index_id = :index_id
                    ORDER BY sequence_no
                    """
                ),
                {"document_id": document_id, "index_id": resolved},
            )
            return resolved, [dict(row) for row in result.mappings()]

    async def list_parent_chunks(
        self, document_id: UUID, index_id: UUID | None
    ) -> tuple[UUID, list[dict[str, Any]]]:
        async with self.engine.connect() as connection:
            resolved = await self._resolve_index_for_document(connection, document_id, index_id)
            result = await connection.execute(
                text(
                    """
                    SELECT p.id, p.kb_id, p.index_id, p.index_document_id, p.document_id,
                           p.parent_type, p.sequence_no, p.content, p.token_count,
                           p.section_path, p.metadata, p.created_at,
                           ARRAY(
                               SELECT c.id::text FROM document_chunk c
                               WHERE c.parent_id = p.id ORDER BY c.sequence_no
                           ) AS child_chunk_ids
                    FROM document_parent_chunk p
                    WHERE p.document_id = :document_id AND p.index_id = :index_id
                    ORDER BY p.sequence_no
                    """
                ),
                {"document_id": document_id, "index_id": resolved},
            )
            return resolved, [dict(row) for row in result.mappings()]

    async def get_image_asset(self, image_asset_id: UUID) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, index_id, document_id, element_id, minio_bucket,
                           minio_object_key, mime_type, width, height, ocr_text,
                           vision_caption, ocr_status, vision_status, ocr_provider,
                           ocr_model_name, ocr_error_code, vision_provider,
                           vision_model_name, vision_error_code, processed_at,
                           metadata, created_at
                    FROM image_asset WHERE id = :id
                    """
                ),
                {"id": image_asset_id},
            )
            row = result.mappings().one_or_none()
        if row is None:
            raise ApiError("IMAGE_ASSET_NOT_FOUND", "图片资源不存在", 404)
        return dict(row)

    async def list_index_results(
        self, document_id: UUID, index_id: UUID | None
    ) -> tuple[UUID, list[dict[str, Any]]]:
        async with self.engine.connect() as connection:
            resolved = await self._resolve_index_for_document(connection, document_id, index_id)
            result = await connection.execute(
                text(
                    """
                    SELECT idoc.*, i.version AS index_version, i.status AS index_status
                    FROM index_document idoc
                    JOIN knowledge_index i ON i.id = idoc.index_id
                    WHERE idoc.document_id = :document_id AND idoc.index_id = :index_id
                    """
                ),
                {"document_id": document_id, "index_id": resolved},
            )
            return resolved, [dict(row) for row in result.mappings()]

    @staticmethod
    def _knowledge_response(knowledge: dict[str, Any]) -> dict[str, Any]:
        if knowledge["status"] == "DELETING":
            runtime_state = "DELETING"
        elif knowledge["status"] == "DISABLED":
            runtime_state = "DISABLED"
        elif knowledge.get("active_index_id") is not None:
            if knowledge.get("building_index_id") is not None or knowledge["rebuild_required"]:
                runtime_state = "UPDATING"
            elif knowledge.get("latest_index_status") == "FAILED":
                runtime_state = "DEGRADED"
            else:
                runtime_state = "READY"
        elif knowledge.get("building_index_id") is not None:
            runtime_state = "BUILDING"
        elif knowledge.get("latest_index_status") == "FAILED":
            runtime_state = "ERROR"
        else:
            runtime_state = "EMPTY"
        return {
            **knowledge,
            "runtime_state": runtime_state,
            "document_count": int(knowledge.get("document_count", 0)),
            "active_chunk_count": int(knowledge.get("active_chunk_count", 0)),
            "building_index_id": knowledge.get("building_index_id"),
        }

    @staticmethod
    def _document_response(document: dict[str, Any]) -> dict[str, Any]:
        response = {
            "id": str(document["id"]),
            "knowledge_id": str(document["kb_id"]),
            "filename": document["filename"],
            "display_name": document["display_name"],
            "file_hash": document["file_hash"],
            "file_size": document["file_size"],
            "status": document["status"],
            "created_at": document["created_at"].isoformat(),
            "updated_at": document["updated_at"].isoformat(),
            "last_build_status": document.get("last_build_status"),
            "last_build_error": document.get("last_build_error"),
            "last_build_version": document.get("last_build_version"),
        }
        return response

    @staticmethod
    def _build_response(build: BuildReference) -> dict[str, Any]:
        return {
            "requested": build.requested,
            "coalesced": build.coalesced,
            "index_id": str(build.index_id) if build.index_id else None,
            "task_id": str(build.task_id) if build.task_id else None,
            "rebuild_required": build.rebuild_required,
        }
