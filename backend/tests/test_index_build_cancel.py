"""ADR-0023：索引构建的失败即停、协作取消与 CANCELLED 终态。

构建失败不再自动排 followup；管理员可随时停止运行中的构建，停止后的
索引与 FAILED 一样可删除、可重试。本文件覆盖：
- _update_task 不覆盖 CANCELLED 终态（取消不会被进度心跳擦除）
- _mark_cancelled / _mark_failed 的落库语义（含 rebuild_required 回置）
- CANCELLED 纳入 GC 可回收集合与 retry 白名单
- 任务记录保留修剪的 SQL 契约
- Postgres 集成：排队期取消短路、文档边界停止、CANCELLED 索引重试
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from minio import Minio
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings
from app.ingestion.embedding import FakeEmbeddingProvider
from app.ingestion.pipeline import IndexPipeline
from app.ingestion.repository import IngestionRepository
from app.tasks.repository import TaskRecordRepository

pytestmark = pytest.mark.asyncio


class _Result:
    def __init__(
        self,
        *,
        row: dict[str, Any] | None = None,
        rows: list[dict[str, Any]] | None = None,
        scalar: Any = None,
        rowcount: int = 0,
    ) -> None:
        self.row = row
        self.rows = rows or []
        self.scalar = scalar
        self.rowcount = rowcount

    def mappings(self) -> _Result:
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        return self.row

    def one(self) -> dict[str, Any]:
        assert self.row is not None
        return self.row

    def scalar_one_or_none(self) -> Any:
        return self.scalar

    def scalar(self) -> Any:
        return self.scalar

    def __iter__(self) -> Any:
        return iter(self.rows)


class _Connection:
    def __init__(self, results: list[_Result]) -> None:
        self.results = list(results)
        self.executed: list[str] = []

    async def __aenter__(self) -> _Connection:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def execute(self, statement: Any, _parameters: dict[str, Any]) -> _Result:
        self.executed.append(str(statement))
        return self.results.pop(0) if self.results else _Result()

    async def scalar(self, statement: Any, _parameters: dict[str, Any] | None = None) -> Any:
        self.executed.append(str(statement))
        result = self.results.pop(0) if self.results else _Result()
        return result.scalar


class _FakeEngine:
    def __init__(self, results: list[_Result]) -> None:
        self.connection = _Connection(results)

    def begin(self) -> _Connection:
        return self.connection

    def connect(self) -> _Connection:
        return self.connection


def _pipeline(engine: _FakeEngine) -> IndexPipeline:
    return IndexPipeline(
        cast(AsyncEngine, engine),
        cast(Minio, object()),
        Settings(_env_file=None, app_env="test", m2_embedding_provider="fake"),
        embedding_provider=FakeEmbeddingProvider(1024),
    )


async def test_update_task_progress_is_ignored_for_cancelled_task() -> None:
    """进度心跳的 UPDATE 带终态守卫：CANCELLED 行不会被写回 RUNNING。"""
    engine = _FakeEngine([])
    pipeline = _pipeline(engine)

    await pipeline._update_task(uuid4(), "RUNNING", "PARSING", 42)

    sql = engine.connection.executed[0]
    assert "status IN ('QUEUED', 'RUNNING')" in sql


async def test_task_cancelled_probe_reads_task_status() -> None:
    engine = _FakeEngine([_Result(scalar="CANCELLED")])
    pipeline = _pipeline(engine)

    assert await pipeline._task_cancelled(uuid4()) is True

    engine2 = _FakeEngine([_Result(scalar="RUNNING")])
    pipeline2 = _pipeline(engine2)
    assert await pipeline2._task_cancelled(uuid4()) is False


async def test_mark_cancelled_settles_index_documents_and_rearms_flag() -> None:
    engine = _FakeEngine([])
    pipeline = _pipeline(engine)

    await pipeline._mark_cancelled(uuid4())

    executed = engine.connection.executed
    assert any(
        "SET status = 'CANCELLED', error_code = 'CANCELLED'" in sql
        and "WHERE id = :index_id AND status = 'BUILDING'" in sql
        for sql in executed
    )
    assert any(
        "WHERE index_id = :index_id AND status NOT IN ('READY', 'SKIPPED')" in sql
        for sql in executed
    )
    assert any("SET rebuild_required = true" in sql for sql in executed)


async def test_mark_failed_rearms_rebuild_required() -> None:
    engine = _FakeEngine([])
    pipeline = _pipeline(engine)

    await pipeline._mark_failed(uuid4(), uuid4(), RuntimeError("boom"))

    assert any("SET rebuild_required = true" in sql for sql in engine.connection.executed)
    assert any(
        "SET status = 'FAILED', error_code = :error_code" in sql
        for sql in engine.connection.executed
    )


async def test_find_deletable_indexes_includes_cancelled() -> None:
    engine = _FakeEngine([_Result(rows=[])])
    repository = IngestionRepository(
        cast(AsyncEngine, engine), Settings(_env_file=None, app_env="test")
    )

    await repository.find_deletable_indexes(3, 7)

    sql = engine.connection.executed[0]
    assert "IN ('DEPRECATED', 'FAILED', 'CANCELLED')" in sql


async def test_prune_terminal_tasks_sql_contract() -> None:
    engine = _FakeEngine([_Result(rowcount=2)])
    records = TaskRecordRepository(cast(AsyncEngine, engine))

    deleted = await records.prune_terminal_tasks(90, per_kb_floor=10)

    assert deleted == 2
    sql = engine.connection.executed[0]
    assert "DELETE FROM task_record" in sql
    assert "status IN ('SUCCEEDED', 'FAILED', 'CANCELLED')" in sql
    assert "row_number() OVER" in sql
    assert "PARTITION BY t.kb_id" in sql
    assert "ranked.rn <= :per_kb_floor" in sql


# ---------------------------------------------------------------------------
# Postgres 集成层
# ---------------------------------------------------------------------------


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
        redis_url=SecretStr("redis://:rag-redis-password@127.0.0.1:16379/15"),
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
                {"id": knowledge_id, "name": f"cancel-test-{knowledge_id}"},
            )
        try:
            yield engine, knowledge_id
        finally:
            async with engine.begin() as connection:
                await connection.execute(
                    text("UPDATE knowledge_base SET active_index_id = NULL WHERE id = :id"),
                    {"id": knowledge_id},
                )
                await connection.execute(
                    text("DELETE FROM knowledge_base WHERE id = :id"), {"id": knowledge_id}
                )


async def _embedding_model(engine: AsyncEngine) -> dict[str, Any]:
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text(
                        "SELECT id, model_name FROM model_config "
                        "WHERE model_type = 'EMBEDDING' AND enabled LIMIT 1"
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        pytest.skip("no enabled EMBEDDING model_config in the test database")
    return dict(row)


async def _create_index(engine: AsyncEngine, knowledge_id: UUID, version: int, status: str) -> UUID:
    model = await _embedding_model(engine)
    index_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO knowledge_index (
                    id, kb_id, version, status, embedding_model_id,
                    embedding_model_name, embedding_dimension, build_reason
                ) VALUES (
                    :id, :kb_id, :version, :status, :model_id,
                    :model_name, 1024, 'MANUAL'
                )
                """
            ),
            {
                "id": index_id,
                "kb_id": knowledge_id,
                "version": version,
                "status": status,
                "model_id": model["id"],
                "model_name": model["model_name"],
            },
        )
    return index_id


async def _create_task(
    engine: AsyncEngine,
    *,
    task_type: str,
    knowledge_id: UUID,
    index_id: UUID,
    status: str = "QUEUED",
) -> UUID:
    task_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO task_record (
                    id, task_type, status, stage, progress, kb_id, index_id, metadata
                ) VALUES (
                    :id, :task_type, :status, 'QUEUED', 0, :kb_id, :index_id, '{}'::jsonb
                )
                """
            ),
            {
                "id": task_id,
                "task_type": task_type,
                "status": status,
                "kb_id": knowledge_id,
                "index_id": index_id,
            },
        )
    return task_id


async def _fetch_one(engine: AsyncEngine, sql: str, **parameters: Any) -> Any:
    async with engine.connect() as connection:
        return (await connection.execute(text(sql), parameters)).mappings().one_or_none()


async def test_run_short_circuits_when_cancelled_while_queued() -> None:
    """排队期被取消的构建不消耗任何模型调用，直接落 CANCELLED 终态。"""
    async with _knowledge_base() as (engine, knowledge_id):
        index_id = await _create_index(engine, knowledge_id, 1, "BUILDING")
        task_id = await _create_task(
            engine,
            task_type="INDEX_BUILD",
            knowledge_id=knowledge_id,
            index_id=index_id,
            status="CANCELLED",
        )
        pipeline = IndexPipeline(
            engine,
            cast(Minio, object()),
            _settings(),
            embedding_provider=FakeEmbeddingProvider(1024),
        )

        result = await pipeline.run(index_id, task_id)

        assert result.cancelled is True
        assert result.document_count == 0
        index = await _fetch_one(
            engine, "SELECT status, error_code FROM knowledge_index WHERE id = :id", id=index_id
        )
        assert index["status"] == "CANCELLED"
        assert index["error_code"] == "CANCELLED"
        kb = await _fetch_one(
            engine, "SELECT rebuild_required FROM knowledge_base WHERE id = :id", id=knowledge_id
        )
        assert kb["rebuild_required"] is True


async def test_run_stops_at_document_boundary_when_cancelled_mid_build() -> None:
    """运行中取消：正在处理的文档完成，其余文档停止，索引落 CANCELLED。"""
    async with _knowledge_base() as (engine, knowledge_id):
        index_id = await _create_index(engine, knowledge_id, 1, "BUILDING")
        task_id = await _create_task(
            engine,
            task_type="INDEX_BUILD",
            knowledge_id=knowledge_id,
            index_id=index_id,
            status="RUNNING",
        )
        pipeline = IndexPipeline(
            engine,
            cast(Minio, object()),
            _settings(),
            embedding_provider=FakeEmbeddingProvider(1024),
        )
        documents = [
            {
                "index_document_id": str(uuid4()),
                "document_id": str(uuid4()),
                "filename": f"d{i}.docx",
            }
            for i in range(2)
        ]

        async def fake_process_document(*_args: Any, **_kwargs: Any) -> None:
            # 第一份文档处理期间管理员请求取消（任务行直接落 CANCELLED，
            # 与 request_cancel 的终态语义一致）。
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE task_record SET status = 'CANCELLED', finished_at = now() "
                        "WHERE id = :id"
                    ),
                    {"id": task_id},
                )

        async def fake_load_documents(_index_id: UUID) -> list[dict[str, Any]]:
            return documents

        pipeline._process_document = fake_process_document  # type: ignore[method-assign]
        pipeline._load_documents = fake_load_documents  # type: ignore[method-assign]

        result = await pipeline.run(index_id, task_id)

        assert result.cancelled is True
        index = await _fetch_one(
            engine, "SELECT status FROM knowledge_index WHERE id = :id", id=index_id
        )
        assert index["status"] == "CANCELLED"
        kb = await _fetch_one(
            engine, "SELECT rebuild_required FROM knowledge_base WHERE id = :id", id=knowledge_id
        )
        assert kb["rebuild_required"] is True


async def test_failed_build_rearms_rebuild_required_without_followup() -> None:
    """构建失败只置位待重建标志，不产生任何新的排队构建任务。"""
    async with _knowledge_base() as (engine, knowledge_id):
        index_id = await _create_index(engine, knowledge_id, 1, "BUILDING")
        task_id = await _create_task(
            engine,
            task_type="INDEX_BUILD",
            knowledge_id=knowledge_id,
            index_id=index_id,
        )
        pipeline = IndexPipeline(
            engine,
            cast(Minio, object()),
            _settings(),
            embedding_provider=FakeEmbeddingProvider(1024),
        )

        async def fake_load_documents(_index_id: UUID) -> list[dict[str, Any]]:
            return [
                {
                    "index_document_id": str(uuid4()),
                    "document_id": str(uuid4()),
                    "filename": "bad.docx",
                }
            ]

        async def fake_process_document(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("文档解析失败")

        pipeline._load_documents = fake_load_documents  # type: ignore[method-assign]
        pipeline._process_document = fake_process_document  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="文档解析失败"):
            await pipeline.run(index_id, task_id)

        kb = await _fetch_one(
            engine, "SELECT rebuild_required FROM knowledge_base WHERE id = :id", id=knowledge_id
        )
        assert kb["rebuild_required"] is True
        index = await _fetch_one(
            engine, "SELECT status FROM knowledge_index WHERE id = :id", id=index_id
        )
        assert index["status"] == "FAILED"
        followups = await _fetch_one(
            engine,
            "SELECT count(*) AS total FROM knowledge_index WHERE kb_id = :kb_id",
            kb_id=knowledge_id,
        )
        assert followups["total"] == 1  # 只有失败的那一个索引，没有 followup


async def _add_document(engine: AsyncEngine, knowledge_id: UUID) -> UUID:
    document_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO document_source (
                    id, kb_id, filename, display_name, minio_bucket,
                    minio_object_key, file_hash, file_size, mime_type
                ) VALUES (
                    :id, :kb_id, 'test.docx', '重试测试文档', 'test',
                    :object_key, :file_hash, 1,
                    'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                )
                """
            ),
            {
                "id": document_id,
                "kb_id": knowledge_id,
                "object_key": f"integration/{document_id}",
                "file_hash": uuid4().hex * 2,
            },
        )
    return document_id


async def test_retry_index_accepts_cancelled_index() -> None:
    """CANCELLED 索引与 FAILED 一样可以手动重试，创建新的构建。"""
    async with _knowledge_base() as (engine, knowledge_id):
        await _add_document(engine, knowledge_id)
        index_id = await _create_index(engine, knowledge_id, 1, "CANCELLED")
        repository = IngestionRepository(engine, _settings())

        build = await repository.retry_index(index_id, activate_on_success=False)

        assert build.requested is True
        assert build.index_id is not None and build.index_id != index_id
        assert build.task_id is not None
        new_index = await _fetch_one(
            engine,
            "SELECT version, status FROM knowledge_index WHERE id = :id",
            id=build.index_id,
        )
        assert new_index["version"] == 2
        assert new_index["status"] == "BUILDING"
        task = await _fetch_one(
            engine, "SELECT status FROM task_record WHERE id = :id", id=build.task_id
        )
        assert task["status"] == "QUEUED"


class _PointerRestoreRepository:
    """Simulates delete_index returning False: a live pointer appeared and
    the repository restored the index instead of destroying protected data."""

    async def get_index(self, index_id: UUID) -> dict[str, Any]:
        return {"id": str(index_id), "status": "DEPRECATED"}

    async def release_frozen_conversation_references(self, index_id: UUID) -> int:
        return 0

    async def mark_index_deleting(self, index_id: UUID) -> dict[str, Any] | None:
        return {"id": str(index_id), "status": "DELETING"}

    async def get_index_deletion_blockers(self, index_id: UUID) -> dict[str, Any]:
        return {
            "status": "DEPRECATED",
            "active_index_pointer_count": 0,
            "rag_active_pointer_count": 1,
            "support_active_pointer_count": 0,
            "support_active_release_count": 0,
            "open_release_count": 0,
            "active_task_count": 0,
            "frozen_conversation_count": 0,
        }

    async def delete_index(self, index_id: UUID) -> bool:
        return False


class _ListingMinio:
    def __init__(self) -> None:
        self.removed: list[str] = []

    def list_objects(self, bucket: str, prefix: str | None = None, recursive: bool = False):
        return ["indexes/keep/me.png"]

    def remove_object(self, bucket: str, object_key: str) -> None:
        self.removed.append(object_key)


async def test_delete_blocked_by_pointer_keeps_minio_objects_intact() -> None:
    """删除被指针恢复拦下时，MinIO 对象必须原封不动——前缀扫删只在
    数据库行物理删除成功之后执行，否则被保护的索引会丢掉全部图片。"""
    from app.common.errors import ApiError
    from app.services.ingestion import IngestionService

    minio = _ListingMinio()
    service = IngestionService(
        cast(Any, _PointerRestoreRepository()),
        cast(Any, minio),
        Settings(_env_file=None, app_env="test", m2_embedding_provider="fake"),
    )

    with pytest.raises(ApiError):
        await service.delete_index(uuid4(), force=False)

    assert minio.removed == []


async def test_prune_terminal_tasks_respects_window_and_floor() -> None:
    """终态超期记录被修剪；每库最新 10 条保底；活动记录绝不动。"""
    async with _knowledge_base() as (engine, knowledge_id):
        old = datetime.now(UTC) - timedelta(days=120)
        rows = []
        for i in range(12):
            rows.append(
                {
                    "id": uuid4(),
                    "task_type": "INDEX_BUILD",
                    "status": "SUCCEEDED",
                    "stage": "COMPLETED",
                    "kb_id": knowledge_id,
                    "finished_at": old - timedelta(minutes=i),
                    "created_at": old - timedelta(minutes=i),
                }
            )
        active_id = uuid4()
        rows.append(
            {
                "id": active_id,
                "task_type": "INDEX_BUILD",
                "status": "RUNNING",
                "stage": "PARSING",
                "kb_id": knowledge_id,
                "finished_at": None,
                "created_at": None,
            }
        )
        async with engine.begin() as connection:
            for row in rows:
                await connection.execute(
                    text(
                        """
                        INSERT INTO task_record (
                            id, task_type, status, stage, progress, kb_id, metadata,
                            finished_at, created_at
                        ) VALUES (
                            :id, :task_type, :status, :stage, 50, :kb_id,
                            CAST(:metadata AS jsonb), :finished_at,
                            COALESCE(:created_at, now())
                        )
                        """
                    ),
                    {
                        **row,
                        "metadata": json.dumps({"case": "prune"}),
                    },
                )
        records = TaskRecordRepository(engine)

        deleted = await records.prune_terminal_tasks(90, per_kb_floor=10)

        # 本库 12 条超期记录只修剪最旧的 2 条；全局计数受库中其他历史
        # 记录影响，不 assert 精确值。
        assert deleted >= 2
        remaining = await _fetch_one(
            engine,
            "SELECT count(*) AS total FROM task_record WHERE kb_id = :kb_id",
            kb_id=knowledge_id,
        )
        assert remaining["total"] == 11  # 10 条保底 + 1 条 RUNNING
        active = await _fetch_one(
            engine, "SELECT status FROM task_record WHERE id = :id", id=active_id
        )
        assert active["status"] == "RUNNING"


async def test_recovery_settles_cancelled_task_whose_worker_died() -> None:
    """取消请求落地后执行进程死亡：索引与文档按取消语义落 CANCELLED，
    rebuild_required 置位，任务行保持终态——不留僵尸 BUILDING 索引。"""
    async with _knowledge_base() as (engine, knowledge_id):
        index_id = await _create_index(engine, knowledge_id, 1, "BUILDING")
        task_id = await _create_task(
            engine,
            task_type="INDEX_BUILD",
            knowledge_id=knowledge_id,
            index_id=index_id,
            status="CANCELLED",
        )
        repository = IngestionRepository(engine, _settings())

        settled = await repository.mark_abandoned_index_build_failed(
            index_id, task_id, rq_status="missing"
        )

        assert settled is True
        index = await _fetch_one(
            engine,
            "SELECT status, error_code FROM knowledge_index WHERE id = :id",
            id=index_id,
        )
        assert index["status"] == "CANCELLED"
        assert index["error_code"] == "CANCELLED"
        task = await _fetch_one(engine, "SELECT status FROM task_record WHERE id = :id", id=task_id)
        assert task["status"] == "CANCELLED"  # 任务终态不被改写
        kb = await _fetch_one(
            engine, "SELECT rebuild_required FROM knowledge_base WHERE id = :id", id=knowledge_id
        )
        assert kb["rebuild_required"] is True
