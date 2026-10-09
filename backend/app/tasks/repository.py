from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


class TaskRecordRepository:
    """Small shared writer for the durable task status exposed by the API."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    @staticmethod
    async def create_in_connection(
        connection: AsyncConnection,
        *,
        task_type: str,
        kb_id: UUID | None,
        index_id: UUID | None = None,
        document_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> UUID:
        task_id = uuid4()
        await connection.execute(
            text(
                """
                INSERT INTO task_record (
                    id, task_type, status, stage, progress, kb_id, index_id, document_id, metadata
                ) VALUES (
                    :id, :task_type, 'QUEUED', 'QUEUED', 0,
                    :kb_id, :index_id, :document_id, CAST(:metadata AS jsonb)
                )
                """
            ),
            {
                "id": task_id,
                "task_type": task_type,
                "kb_id": kb_id,
                "index_id": index_id,
                "document_id": document_id,
                "metadata": json.dumps(metadata or {}, ensure_ascii=False),
            },
        )
        return task_id

    async def create(
        self,
        *,
        task_type: str,
        kb_id: UUID | None,
        index_id: UUID | None = None,
        document_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> UUID:
        async with self.engine.begin() as connection:
            return await self.create_in_connection(
                connection,
                task_type=task_type,
                kb_id=kb_id,
                index_id=index_id,
                document_id=document_id,
                metadata=metadata,
            )

    @staticmethod
    async def create_profile_task_in_connection(
        connection: AsyncConnection,
        *,
        task_type: str,
        kb_id: UUID | None,
        index_id: UUID,
        metadata: dict[str, Any] | None = None,
    ) -> UUID | None:
        """Insert a PROFILE_* task guarded by uq_task_record_active_profile_per_index.

        Returns None when a concurrent submission already won the single
        active-Profile-task slot for the index; the caller then replays the
        winning task instead of creating a parallel generation.
        """
        task_id = uuid4()
        result = await connection.execute(
            text(
                """
                INSERT INTO task_record (
                    id, task_type, status, stage, progress, kb_id, index_id, document_id, metadata
                ) VALUES (
                    :id, :task_type, 'QUEUED', 'QUEUED', 0,
                    :kb_id, :index_id, NULL, CAST(:metadata AS jsonb)
                )
                ON CONFLICT (index_id) WHERE (
                    task_type IN ('PROFILE_GENERATE', 'PROFILE_BACKFILL',
                                  'PROFILE_REFRESH', 'PROFILE_REGENERATE')
                    AND status IN ('QUEUED', 'RUNNING')
                ) DO NOTHING
                RETURNING id
                """
            ),
            {
                "id": task_id,
                "task_type": task_type,
                "kb_id": kb_id,
                "index_id": index_id,
                "metadata": json.dumps(metadata or {}, ensure_ascii=False),
            },
        )
        inserted = result.scalar_one_or_none()
        return UUID(str(inserted)) if inserted is not None else None

    @staticmethod
    async def create_rag_document_card_task_in_connection(
        connection: AsyncConnection,
        *,
        kb_id: UUID,
        index_id: UUID,
        document_id: UUID | None,
        metadata: dict[str, Any] | None = None,
    ) -> UUID | None:
        """Create one observable card-generation task per frozen index."""
        task_id = uuid4()
        result = await connection.execute(
            text(
                """
                INSERT INTO task_record (
                    id, task_type, status, stage, progress, kb_id, index_id, document_id, metadata
                ) VALUES (
                    :id, 'RAG_DOCUMENT_CARD_GENERATE', 'QUEUED', 'QUEUED', 0,
                    :kb_id, :index_id, :document_id, CAST(:metadata AS jsonb)
                )
                ON CONFLICT (index_id) WHERE (
                    task_type = 'RAG_DOCUMENT_CARD_GENERATE'
                    AND status IN ('QUEUED', 'RUNNING')
                ) DO NOTHING
                RETURNING id
                """
            ),
            {
                "id": task_id,
                "kb_id": kb_id,
                "index_id": index_id,
                "document_id": document_id,
                "metadata": json.dumps(metadata or {}, ensure_ascii=False),
            },
        )
        inserted = result.scalar_one_or_none()
        return UUID(str(inserted)) if inserted is not None else None

    async def merge_metadata(self, task_id: UUID, metadata: dict[str, Any]) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE task_record
                    SET metadata = metadata || CAST(:metadata AS jsonb), updated_at = now()
                    WHERE id = :id
                    """
                ),
                {"id": task_id, "metadata": json.dumps(metadata, ensure_ascii=False)},
            )

    async def get(self, task_id: UUID) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text("SELECT * FROM task_record WHERE id = :id"), {"id": task_id}
            )
            row = result.mappings().one_or_none()
        return dict(row) if row is not None else None

    async def status(self, task_id: UUID) -> str | None:
        """Cheap cooperative-cancellation probe used inside worker loops."""
        async with self.engine.connect() as connection:
            status = await connection.scalar(
                text("SELECT status FROM task_record WHERE id = :id"), {"id": task_id}
            )
        return str(status) if status is not None else None

    async def request_cancel(self, task_id: UUID) -> bool:
        """Terminal-cancel an active task; returns False when already terminal.

        The worker polls task_record.status between documents, so a cancelled
        row is enough to stop an in-flight run; no queue surgery is needed.
        """
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE task_record
                    SET status = 'CANCELLED', stage = 'CANCELLED', finished_at = now(),
                        updated_at = now(),
                        metadata = metadata || CAST(:metadata AS jsonb)
                    WHERE id = :id AND status IN ('QUEUED', 'RUNNING')
                    """
                ),
                {"id": task_id, "metadata": json.dumps({"cancelled": True})},
            )
            return (result.rowcount or 0) > 0

    async def prune_terminal_tasks(
        self,
        retention_days: int,
        *,
        per_kb_floor: int = 10,
        kb_id: UUID | None = None,
    ) -> int:
        """Delete terminal task records past the retention window.

        SUCCEEDED/FAILED/CANCELLED rows older than ``retention_days`` are
        pruned by the periodic GC sweep; the newest ``per_kb_floor`` terminal
        rows of every knowledge base survive the window so recent history
        stays inspectable even for dormant KBs.  Active (QUEUED/RUNNING) rows
        are never touched.  ``kb_id`` limits the prune to one knowledge base
        (per-knowledge GC); global tasks without a knowledge base are then
        left alone.  Returns the number of deleted rows.
        """
        scope = " AND kb_id = :kb_id" if kb_id is not None else ""
        parameters: dict[str, Any] = {
            "retention_days": retention_days,
            "per_kb_floor": per_kb_floor,
        }
        if kb_id is not None:
            parameters["kb_id"] = kb_id
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    DELETE FROM task_record
                    WHERE status IN ('SUCCEEDED', 'FAILED', 'CANCELLED')
                      AND finished_at < now() - INTERVAL '1 day' * :retention_days
                      AND id NOT IN (
                          SELECT id FROM (
                              SELECT t.id, row_number() OVER (
                                  PARTITION BY t.kb_id ORDER BY t.created_at DESC
                              ) AS rn
                              FROM task_record t
                              WHERE t.status IN ('SUCCEEDED', 'FAILED', 'CANCELLED')
                                AND t.kb_id IS NOT NULL
                          ) ranked
                          WHERE ranked.rn <= :per_kb_floor
                      )
                    """
                    + scope
                ),
                parameters,
            )
            return int(result.rowcount or 0)

    async def list_active_by_type(self, task_type: str) -> list[dict[str, Any]]:
        """Active tasks of one type, for orphan reconciliation at worker start."""
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT id, status, kb_id, created_at, updated_at
                        FROM task_record
                        WHERE task_type = :task_type AND status IN ('QUEUED', 'RUNNING')
                        ORDER BY created_at
                        """
                        ),
                        {"task_type": task_type},
                    )
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]

    async def start(self, task_id: UUID, *, stage: str) -> bool:
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE task_record
                    SET status = 'RUNNING', stage = :stage, progress = GREATEST(progress, 1),
                        attempt = attempt + 1, started_at = COALESCE(started_at, now()),
                        updated_at = now(), error_code = NULL, error_message = NULL
                    WHERE id = :id AND status IN ('QUEUED', 'FAILED')
                    RETURNING id
                    """
                ),
                {"id": task_id, "stage": stage},
            )
            return result.scalar_one_or_none() is not None

    async def progress(
        self,
        task_id: UUID,
        *,
        stage: str,
        progress: int,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE task_record
                    SET stage = :stage, progress = :progress, updated_at = now(),
                        metadata = metadata || CAST(:metadata AS jsonb)
                    WHERE id = :id AND status = 'RUNNING'
                    """
                ),
                {
                    "id": task_id,
                    "stage": stage,
                    "progress": max(0, min(progress, 99)),
                    "metadata": json.dumps(metadata or {}, ensure_ascii=False),
                },
            )

    async def succeed(self, task_id: UUID, *, metadata: dict[str, Any] | None = None) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE task_record
                    SET status = 'SUCCEEDED', stage = 'COMPLETED', progress = 100,
                        finished_at = now(), updated_at = now(), error_code = NULL,
                        error_message = NULL, metadata = metadata || CAST(:metadata AS jsonb)
                    WHERE id = :id AND status = 'RUNNING'
                    """
                ),
                {"id": task_id, "metadata": json.dumps(metadata or {}, ensure_ascii=False)},
            )

    async def fail(self, task_id: UUID, error: Exception) -> None:
        # ApiError carries a structured, admin-readable message; raw exceptions
        # degrade to their class name + str().
        api_message = getattr(error, "message", None)
        error_code = str(getattr(error, "code", None) or type(error).__name__)[:100]
        error_message = (api_message or str(error))[:500] or "后台任务执行失败"
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE task_record
                    SET status = 'FAILED', stage = 'FAILED', error_code = :error_code,
                        error_message = :error_message, finished_at = now(), updated_at = now()
                    WHERE id = :id AND status IN ('QUEUED', 'RUNNING')
                    """
                ),
                {
                    "id": task_id,
                    "error_code": error_code,
                    "error_message": error_message,
                },
            )
