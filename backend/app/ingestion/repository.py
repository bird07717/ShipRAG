from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.common.errors import ApiError
from app.core.config import Settings


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
                                     WHERE i.id = kb.active_index_id), 0) AS active_chunk_count,
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
                                     WHERE i.id = kb.active_index_id), 0) AS active_chunk_count,
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

            document_result = await connection.execute(
                text(
                    """
                    INSERT INTO document_source (
                        id, kb_id, filename, display_name, minio_bucket, minio_object_key,
                        file_hash, file_size, mime_type
                    ) VALUES (
                        :id, :kb_id, :filename, :display_name, :bucket, :object_key,
                        :file_hash, :file_size,
                        'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
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
                },
            )
            document = dict(document_result.mappings().one())
            build = (
                await self._request_build_locked(
                    connection, knowledge_id, "DOCUMENT_CHANGED", activate_on_success=True
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
            build = (
                await self._request_build_locked(
                    connection,
                    UUID(str(document["kb_id"])),
                    "DOCUMENT_CHANGED",
                    activate_on_success=True,
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
        activate_on_success: bool = True,
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
                SELECT id, file_hash FROM document_source
                WHERE kb_id = :kb_id AND status = 'STORED'
                ORDER BY created_at, id
                """
            ),
            {"kb_id": knowledge_id},
        )
        documents = list(documents_result.mappings())
        if not documents:
            raise ApiError("VALIDATION_ERROR", "知识库没有可构建文档", 409)

        # 查询上一版 Active Index，判断哪些文档可以复用解析结果
        prev_index_result = await connection.execute(
            text("SELECT active_index_id FROM knowledge_base WHERE id = :kb_id"),
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
            if prev_model_name == model["model_name"]:
                prev_docs_result = await connection.execute(
                    text(
                        """
                        SELECT document_id, source_hash FROM index_document
                        WHERE index_id = :index_id AND status = 'READY'
                        """
                    ),
                    {"index_id": prev_index_id},
                )
                prev_hashes = {
                    row["document_id"]: row["source_hash"] for row in prev_docs_result.mappings()
                }
                for document in documents:
                    doc_id = document["id"]
                    if doc_id in prev_hashes and prev_hashes[doc_id] == document["file_hash"]:
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
                "metadata": json.dumps({"document_count": len(documents)}),
            },
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

    async def create_followup_build_if_needed(self, knowledge_id: UUID) -> BuildReference | None:
        async with self.engine.begin() as connection:
            kb_result = await connection.execute(
                text("SELECT rebuild_required FROM knowledge_base WHERE id = :id FOR UPDATE"),
                {"id": knowledge_id},
            )
            rebuild_required = kb_result.scalar_one_or_none()
            if rebuild_required is not True:
                return None
            building_result = await connection.execute(
                text(
                    """
                    SELECT id FROM knowledge_index
                    WHERE kb_id = :kb_id AND status = 'BUILDING'
                    """
                ),
                {"kb_id": knowledge_id},
            )
            if building_result.scalar_one_or_none() is not None:
                return None
            return await self._create_build_locked(
                connection,
                knowledge_id,
                "DOCUMENT_CHANGED",
                activate_on_success=True,
            )

    async def get_active_index_id(self, knowledge_id: UUID) -> UUID | None:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text("SELECT active_index_id FROM knowledge_base WHERE id = :id"),
                {"id": knowledge_id},
            )
            return result.scalar_one_or_none()

    async def list_documents(self, knowledge_id: UUID) -> list[dict[str, Any]]:
        await self.get_knowledge_base(knowledge_id)
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT * FROM document_source
                    WHERE kb_id = :kb_id AND status = 'STORED'
                    ORDER BY created_at DESC
                    """
                ),
                {"kb_id": knowledge_id},
            )
            return [self._document_response(dict(row)) for row in result.mappings()]

    async def get_document(self, document_id: UUID) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text("SELECT * FROM document_source WHERE id = :id"), {"id": document_id}
            )
            row = result.mappings().one_or_none()
        if row is None:
            raise ApiError("DOCUMENT_NOT_FOUND", "文档不存在", 404)
        return self._document_response(dict(row))

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

    async def activate_index(self, index_id: UUID) -> dict[str, Any]:
        """Publish a READY snapshot and retire the prior ACTIVE snapshot atomically."""
        async with self.engine.begin() as connection:
            target_result = await connection.execute(
                text(
                    """
                    SELECT i.*, kb.active_index_id
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
                    SET active_index_id = :index_id, updated_at = now()
                    WHERE id = :kb_id
                    """
                ),
                {"index_id": index_id, "kb_id": target["kb_id"]},
            )
            return dict(activated)

    async def retry_index(
        self, index_id: UUID, *, activate_on_success: bool = True
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
            if failed["status"] != "FAILED":
                raise ApiError("INDEX_NOT_READY", "只有 FAILED 索引可以重试", 409)
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
                    SELECT t.id AS task_id, t.index_id, i.kb_id
                    FROM task_record t
                    JOIN knowledge_index i ON i.id = t.index_id
                    WHERE t.task_type = 'INDEX_BUILD'
                      AND t.status IN ('QUEUED', 'RUNNING')
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
                or state["task_status"] not in {"QUEUED", "RUNNING"}
                or state["index_status"] != "BUILDING"
            ):
                return False
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

    async def find_deletable_indexes(
        self, retention_count: int, retention_days: int
    ) -> list[GarbageCollectionItem]:
        """Find DEPRECATED or FAILED indexes that exceed the retention policy."""
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
                        WHERE status IN ('DEPRECATED', 'FAILED')
                    )
                    SELECT id, kb_id, version, status
                    FROM ranked
                    WHERE rn > :retention_count
                      AND created_at < now() - INTERVAL '1 day' * :retention_days
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
                    RETURNING *
                    """
                ),
                {"index_id": index_id},
            )
            row = result.mappings().one_or_none()
            return dict(row) if row is not None else None

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
        rows must be removed first. The index must be in DELETING status."""
        async with self.engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM rag_trace WHERE index_id = :index_id"), {"index_id": index_id}
            )
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
                SELECT kb.active_index_id
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
        if knowledge["status"] == "DISABLED":
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
        return {
            "id": str(document["id"]),
            "knowledge_id": str(document["kb_id"]),
            "filename": document["filename"],
            "display_name": document["display_name"],
            "file_hash": document["file_hash"],
            "file_size": document["file_size"],
            "status": document["status"],
            "created_at": document["created_at"].isoformat(),
            "updated_at": document["updated_at"].isoformat(),
        }

    @staticmethod
    def _build_response(build: BuildReference) -> dict[str, Any]:
        return {
            "requested": build.requested,
            "coalesced": build.coalesced,
            "index_id": str(build.index_id) if build.index_id else None,
            "task_id": str(build.task_id) if build.task_id else None,
            "rebuild_required": build.rebuild_required,
        }
