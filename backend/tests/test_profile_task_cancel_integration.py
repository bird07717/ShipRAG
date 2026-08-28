"""Real-PostgreSQL tests for DOCUMENT_PROFILE_GENERATE cancellation.

Covers the cooperative-cancellation state machine: request_cancel flips an
active task to CANCELLED, a cancelled task no longer blocks new generation
submissions, and terminal transitions are never overwritten by the worker.
These tests are skipped unless ``SUPPORT_TEST_DATABASE_URL`` points at a
migrated database.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.common.errors import ApiError
from app.core.config import Settings
from app.document_profiles.repository import DocumentProfileRepository
from app.document_profiles.service import DocumentProfileService
from app.tasks.repository import TaskRecordRepository

pytestmark = pytest.mark.asyncio


def _database_url() -> str:
    url = os.getenv("SUPPORT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SUPPORT_TEST_DATABASE_URL is not configured")
    return url


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=SecretStr(_database_url()),
        redis_url=SecretStr("redis://:password@127.0.0.1:1/0"),
    )


@asynccontextmanager
async def _engine_cm() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@asynccontextmanager
async def _knowledge_base() -> AsyncIterator[tuple[AsyncEngine, UUID]]:
    knowledge_id = uuid4()
    async with _engine_cm() as engine:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO knowledge_base (id, name, status) VALUES (:id, :name, 'ENABLED')"
                ),
                {"id": knowledge_id, "name": f"profile-cancel-{knowledge_id}"},
            )
        try:
            yield engine, knowledge_id
        finally:
            async with engine.begin() as connection:
                await connection.execute(
                    text("DELETE FROM knowledge_base WHERE id = :id"), {"id": knowledge_id}
                )


async def _create_running_profile_task(
    engine: AsyncEngine, knowledge_id: UUID, *, status: str = "RUNNING"
) -> UUID:
    task_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO task_record (id, task_type, status, stage, kb_id, metadata)
                VALUES (:id, 'DOCUMENT_PROFILE_GENERATE', :status, 'GENERATING', :kb_id, '{}')
                """
            ),
            {"id": task_id, "status": status, "kb_id": knowledge_id},
        )
    return task_id


async def _task_status(engine: AsyncEngine, task_id: UUID) -> Any:
    async with engine.connect() as connection:
        return await connection.scalar(
            text("SELECT status FROM task_record WHERE id = :id"), {"id": task_id}
        )


async def test_request_cancel_marks_running_task_cancelled() -> None:
    async with _knowledge_base() as (engine, knowledge_id):
        task_id = await _create_running_profile_task(engine, knowledge_id)
        records = TaskRecordRepository(engine)

        assert await records.request_cancel(task_id) is True

        assert await _task_status(engine, task_id) == "CANCELLED"
        # Idempotent: cancelling again reports no transition.
        assert await records.request_cancel(task_id) is False
        assert await _task_status(engine, task_id) == "CANCELLED"


async def test_request_cancel_rejects_terminal_task() -> None:
    async with _knowledge_base() as (engine, knowledge_id):
        task_id = await _create_running_profile_task(engine, knowledge_id, status="SUCCEEDED")
        records = TaskRecordRepository(engine)

        assert await records.request_cancel(task_id) is False
        assert await _task_status(engine, task_id) == "SUCCEEDED"


async def test_succeed_and_fail_never_overwrite_cancelled() -> None:
    async with _knowledge_base() as (engine, knowledge_id):
        task_id = await _create_running_profile_task(engine, knowledge_id)
        records = TaskRecordRepository(engine)
        await records.request_cancel(task_id)

        await records.succeed(task_id, metadata={"generated_count": 1})
        await records.fail(task_id, ApiError("LATE_FAILURE", "迟到的失败", 500))

        assert await _task_status(engine, task_id) == "CANCELLED"


async def test_cancelled_task_no_longer_blocks_new_generation() -> None:
    async with _knowledge_base() as (engine, knowledge_id):
        orphan = await _create_running_profile_task(engine, knowledge_id)
        settings = _settings()
        service = DocumentProfileService(DocumentProfileRepository(engine), settings)

        # The zombie task is replayed while active.
        replay = await service.request_generation(knowledge_id)
        assert str(replay["task_id"]) == str(orphan)
        assert replay["replayed"] is True

        assert await TaskRecordRepository(engine).request_cancel(orphan) is True

        # After cancellation a fresh submission creates a new task instead of
        # replaying the cancelled one.  Enqueue is expected to fail (no Redis
        # in this test); the task is marked FAILED by the bookkeeping path.
        with pytest.raises(ApiError) as exc_info:
            await service.request_generation(knowledge_id)
        assert exc_info.value.code == "UPSTREAM_UNAVAILABLE"
        fresh = await _fetch_active_profile_task(engine, knowledge_id)
        assert fresh is not None
        assert str(fresh["id"]) != str(orphan)
        assert fresh["status"] == "FAILED"


async def _fetch_active_profile_task(engine: AsyncEngine, knowledge_id: UUID) -> Any:
    async with engine.connect() as connection:
        return (
            (
                await connection.execute(
                    text(
                        "SELECT id, status FROM task_record "
                        "WHERE kb_id = :kb_id AND task_type = 'DOCUMENT_PROFILE_GENERATE' "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"kb_id": knowledge_id},
                )
            )
            .mappings()
            .one_or_none()
        )
