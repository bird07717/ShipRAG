"""Real-PostgreSQL tests for the Knowledge Profile rebuild orchestration.

Covers the three candidate-resolution branches of
``IngestionRepository.resolve_profile_rebuild``, the per-document
profile-context query, the active-Profile-task uniqueness guarantee, and the
build-then-refresh intent handling in ``app.tasks.ingestion``.  These tests
are skipped unless ``SUPPORT_TEST_DATABASE_URL`` points at a migrated
database; Redis (``PROFILE_TEST_REDIS_URL``) is only needed for the paths
that actually enqueue RQ jobs.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.common.errors import ApiError
from app.core.config import Settings
from app.ingestion.pipeline import IndexBuildResult
from app.ingestion.repository import IngestionRepository
from app.releases.repository import ReleaseRepository
from app.releases.service import ReleaseService
from app.services.ingestion import IngestionService
from app.tasks.ingestion import _submit_post_build_profile

pytestmark = pytest.mark.asyncio


def _database_url() -> str:
    url = os.getenv("SUPPORT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SUPPORT_TEST_DATABASE_URL is not configured")
    return url


def _redis_url() -> str:
    return os.getenv(
        "PROFILE_TEST_REDIS_URL",
        "redis://:rag-redis-password@127.0.0.1:16379/15",
    )


def _settings(*, redis_url: str | None = None) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=SecretStr(_database_url()),
        redis_url=SecretStr(redis_url or _redis_url()),
    )


@asynccontextmanager
async def _engine_cm() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _fetch_one(engine: AsyncEngine, sql: str, **parameters: Any) -> Any:
    async with engine.connect() as connection:
        return (
            await connection.execute(text(sql), parameters)
        ).mappings().one_or_none()


@asynccontextmanager
async def _knowledge_base(
    *, status: str = "ENABLED", with_document: bool = True
) -> AsyncIterator[tuple[AsyncEngine, UUID, UUID | None]]:
    """Create an isolated KB (+one document) that is fully removed afterwards."""
    knowledge_id = uuid4()
    document_id = uuid4() if with_document else None
    async with _engine_cm() as engine:
        async with engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO knowledge_base (id, name, status) VALUES (:id, :name, :status)"),
                {"id": knowledge_id, "name": f"profile-rebuild-{knowledge_id}", "status": status},
            )
            if document_id is not None:
                await connection.execute(
                    text(
                        """
                        INSERT INTO document_source (
                            id, kb_id, filename, display_name, minio_bucket,
                            minio_object_key, file_hash, file_size, mime_type
                        ) VALUES (
                            :id, :kb_id, 'test.docx', 'Profile 重建测试文档', 'test',
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
        try:
            yield engine, knowledge_id, document_id
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
    row = await _fetch_one(
        engine,
        "SELECT id, model_name FROM model_config WHERE model_type = 'EMBEDDING' "
        "AND enabled LIMIT 1",
    )
    if row is None:
        pytest.skip("no enabled EMBEDDING model_config in the test database")
    return dict(row)


async def _create_index(
    engine: AsyncEngine,
    knowledge_id: UUID,
    version: int,
    status: str,
) -> UUID:
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


async def _create_release(
    engine: AsyncEngine, knowledge_id: UUID, index_id: UUID, status: str = "DRAFT"
) -> UUID:
    release_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO knowledge_release (id, kb_id, index_id, status) "
                "VALUES (:id, :kb_id, :index_id, :status)"
            ),
            {"id": release_id, "kb_id": knowledge_id, "index_id": index_id, "status": status},
        )
    return release_id


async def _create_task(
    engine: AsyncEngine,
    *,
    task_type: str,
    knowledge_id: UUID,
    index_id: UUID,
    status: str = "QUEUED",
    metadata: dict[str, Any] | None = None,
) -> UUID:
    import json

    task_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO task_record (
                    id, task_type, status, stage, progress, kb_id, index_id, metadata
                ) VALUES (
                    :id, :task_type, :status, 'QUEUED', 0, :kb_id, :index_id,
                    CAST(:metadata AS jsonb)
                )
                """
            ),
            {
                "id": task_id,
                "task_type": task_type,
                "status": status,
                "kb_id": knowledge_id,
                "index_id": index_id,
                "metadata": json.dumps(metadata or {}, ensure_ascii=False),
            },
        )
    return task_id


async def _task(engine: AsyncEngine, task_id: UUID) -> dict[str, Any]:
    row = await _fetch_one(engine, "SELECT * FROM task_record WHERE id = :id", id=task_id)
    assert row is not None
    return dict(row)


def _redis_available() -> bool:
    try:
        from redis import Redis

        client = Redis.from_url(_redis_url())
        try:
            return bool(client.ping())
        finally:
            client.close()
    except Exception:
        return False


@pytest.mark.skipif(not _redis_available(), reason="test Redis is not reachable")
async def test_rebuild_resolves_to_profile_refresh_on_editable_candidate() -> None:
    async with _knowledge_base() as (engine, knowledge_id, document_id):
        assert document_id is not None
        index_id = await _create_index(engine, knowledge_id, 1, "READY")
        await _create_release(engine, knowledge_id, index_id)
        repository = IngestionRepository(engine, _settings())

        first = await repository.resolve_profile_rebuild(knowledge_id)
        second = await repository.resolve_profile_rebuild(knowledge_id)

        assert first["task_type"] == "PROFILE_REFRESH"
        assert first["status"] == "QUEUED"
        assert first["replayed"] is False
        assert first["queue"] == "maintenance"
        assert second["task_id"] == first["task_id"]
        assert second["replayed"] is True
        metadata = (await _task(engine, UUID(str(first["task_id"]))))["metadata"]
        assert metadata["source"] == "KNOWLEDGE_PROFILE_REBUILD"
        assert metadata["release_id"]


async def test_rebuild_replays_existing_active_profile_task() -> None:
    async with _knowledge_base() as (engine, knowledge_id, document_id):
        assert document_id is not None
        index_id = await _create_index(engine, knowledge_id, 1, "READY")
        await _create_release(engine, knowledge_id, index_id)
        existing_task_id = await _create_task(
            engine,
            task_type="PROFILE_GENERATE",
            knowledge_id=knowledge_id,
            index_id=index_id,
            status="RUNNING",
        )
        repository = IngestionRepository(engine, _settings())

        resolution = await repository.resolve_profile_rebuild(knowledge_id)

        assert resolution["task_id"] == existing_task_id
        assert resolution["task_type"] == "PROFILE_GENERATE"
        assert resolution["status"] == "RUNNING"
        assert resolution["replayed"] is True
        assert resolution["queue"] is None


async def test_rebuild_merges_intent_into_running_build() -> None:
    async with _knowledge_base() as (engine, knowledge_id, document_id):
        assert document_id is not None
        index_id = await _create_index(engine, knowledge_id, 1, "BUILDING")
        build_task_id = await _create_task(
            engine, task_type="INDEX_BUILD", knowledge_id=knowledge_id, index_id=index_id
        )
        repository = IngestionRepository(engine, _settings())

        resolution = await repository.resolve_profile_rebuild(knowledge_id)

        assert resolution["task_id"] == build_task_id
        assert resolution["task_type"] == "INDEX_BUILD"
        assert resolution["replayed"] is True
        assert resolution["queue"] is None
        metadata = (await _task(engine, build_task_id))["metadata"]
        assert metadata["profile_refresh_after_build"] is True


async def test_rebuild_merging_twice_keeps_single_build_task() -> None:
    async with _knowledge_base() as (engine, knowledge_id, document_id):
        assert document_id is not None
        index_id = await _create_index(engine, knowledge_id, 1, "BUILDING")
        build_task_id = await _create_task(
            engine, task_type="INDEX_BUILD", knowledge_id=knowledge_id, index_id=index_id
        )
        repository = IngestionRepository(engine, _settings())

        first = await repository.resolve_profile_rebuild(knowledge_id)
        second = await repository.resolve_profile_rebuild(knowledge_id)

        assert first["task_id"] == second["task_id"] == build_task_id
        tasks = await _fetch_one(
            engine,
            "SELECT count(*) AS total FROM task_record WHERE index_id = :index_id",
            index_id=index_id,
        )
        assert tasks["total"] == 1


async def test_rebuild_rejects_building_index_without_active_task() -> None:
    async with _knowledge_base() as (engine, knowledge_id, document_id):
        assert document_id is not None
        index_id = await _create_index(engine, knowledge_id, 1, "BUILDING")
        await _create_task(
            engine,
            task_type="INDEX_BUILD",
            knowledge_id=knowledge_id,
            index_id=index_id,
            status="FAILED",
        )
        repository = IngestionRepository(engine, _settings())

        with pytest.raises(ApiError) as raised:
            await repository.resolve_profile_rebuild(knowledge_id)

        assert raised.value.code == "INDEX_BUILD_STUCK"


async def test_rebuild_creates_candidate_build_when_no_editable_release() -> None:
    async with _knowledge_base() as (engine, knowledge_id, document_id):
        assert document_id is not None
        active_index_id = await _create_index(engine, knowledge_id, 1, "ACTIVE")
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE knowledge_base SET active_index_id = :index_id WHERE id = :id"),
                {"index_id": active_index_id, "id": knowledge_id},
            )
        await _create_release(engine, knowledge_id, active_index_id, "PUBLISHED")
        repository = IngestionRepository(engine, _settings())

        resolution = await repository.resolve_profile_rebuild(knowledge_id)

        assert resolution["task_type"] == "INDEX_BUILD"
        assert resolution["replayed"] is False
        assert resolution["queue"] == "index_build"
        new_index_id = UUID(str(resolution["index_id"]))
        task = await _task(engine, UUID(str(resolution["task_id"])))
        assert task["metadata"]["profile_refresh_after_build"] is True
        release = await _fetch_one(
            engine, "SELECT status FROM knowledge_release WHERE index_id = :index_id",
            index_id=new_index_id,
        )
        assert release is not None and release["status"] == "DRAFT"
        old_release = await _fetch_one(
            engine,
            "SELECT status FROM knowledge_release WHERE index_id = :index_id",
            index_id=active_index_id,
        )
        assert old_release is not None and old_release["status"] == "PUBLISHED"
        created_index = await _fetch_one(
            engine, "SELECT status, build_reason FROM knowledge_index WHERE id = :id",
            id=new_index_id,
        )
        assert created_index is not None
        assert created_index["status"] == "BUILDING"
        assert created_index["build_reason"] == "PROFILE_REBUILD"


@pytest.mark.parametrize("status", ["DELETING", "DISABLED"])
async def test_rebuild_rejects_unavailable_knowledge_base(status: str) -> None:
    async with _knowledge_base(status=status, with_document=False) as (
        engine,
        knowledge_id,
        _,
    ):
        repository = IngestionRepository(engine, _settings())

        with pytest.raises(ApiError) as raised:
            await repository.resolve_profile_rebuild(knowledge_id)

        assert raised.value.status_code in (403, 409)
        if status == "DELETING":
            assert raised.value.code == "KNOWLEDGE_BASE_DELETING"
        else:
            assert raised.value.code == "FORBIDDEN"


async def test_rebuild_rejects_unknown_knowledge_base() -> None:
    async with _engine_cm() as engine:
        repository = IngestionRepository(engine, _settings())

        with pytest.raises(ApiError) as raised:
            await repository.resolve_profile_rebuild(uuid4())

        assert raised.value.code == "KNOWLEDGE_BASE_NOT_FOUND"


async def test_unique_index_blocks_second_active_profile_task() -> None:
    async with _knowledge_base() as (engine, knowledge_id, document_id):
        assert document_id is not None
        index_id = await _create_index(engine, knowledge_id, 1, "READY")
        await _create_task(
            engine,
            task_type="PROFILE_REFRESH",
            knowledge_id=knowledge_id,
            index_id=index_id,
        )
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO task_record (
                            id, task_type, status, stage, kb_id, index_id
                        ) VALUES (
                            :id, 'PROFILE_REFRESH', 'QUEUED', 'QUEUED', :kb_id, :index_id
                        )
                        """
                    ),
                    {"id": uuid4(), "kb_id": knowledge_id, "index_id": index_id},
                )


async def test_profile_task_submission_deduplicates_active_task() -> None:
    if not _redis_available():
        pytest.skip("test Redis is not reachable")
    async with _knowledge_base() as (engine, knowledge_id, document_id):
        assert document_id is not None
        index_id = await _create_index(engine, knowledge_id, 1, "READY")
        await _create_release(engine, knowledge_id, index_id)
        service = ReleaseService(ReleaseRepository(engine), _settings())

        first = await service.request_profile_task(index_id, task_type="PROFILE_REFRESH")
        second = await service.request_profile_task(index_id, task_type="PROFILE_REFRESH")

        assert first["replayed"] is False
        assert second["replayed"] is True
        assert second["task_id"] == first["task_id"]


async def test_document_profile_context_shapes() -> None:
    async with _knowledge_base() as (engine, knowledge_id, document_id):
        assert document_id is not None
        repository = ReleaseRepository(engine)

        missing = await repository.document_profile_context(uuid4())
        assert missing is None

        empty = await repository.document_profile_context(document_id)
        assert empty is not None
        assert empty["published_profile"] is None
        assert empty["candidate_draft"] is None

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO document_problem_profile (
                        id, document_id, kb_id, problem_code, canonical_question,
                        aliases, keywords, product_name, model_names
                    ) VALUES (
                        :id, :document_id, :kb_id, 'PDC_UPGRADE', '如何升级 PDC？',
                        '["PDC 怎么升级"]'::jsonb, '["升级"]'::jsonb, 'PDC',
                        '["VDR600"]'::jsonb
                    )
                    """
                ),
                {"id": uuid4(), "document_id": document_id, "kb_id": knowledge_id},
            )

        published_only = await repository.document_profile_context(document_id)
        assert published_only is not None
        profile = published_only["published_profile"]
        assert profile is not None
        assert profile["problem_code"] == "PDC_UPGRADE"
        assert profile["aliases"] == ["PDC 怎么升级"]
        assert "reasoning" not in profile
        assert published_only["candidate_draft"] is None

        index_id = await _create_index(engine, knowledge_id, 1, "READY")
        release_id = await _create_release(engine, knowledge_id, index_id)
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO problem_profile_draft (
                        id, release_id, kb_id, document_id, operation,
                        problem_code, canonical_question, aliases, quality_status
                    ) VALUES (
                        :id, :release_id, :kb_id, :document_id, 'UPDATE',
                        'PDC_UPGRADE', '如何升级 PDC？', '[]'::jsonb, 'REVIEW_REQUIRED'
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "release_id": release_id,
                    "kb_id": knowledge_id,
                    "document_id": document_id,
                },
            )

        both = await repository.document_profile_context(document_id)
        assert both is not None
        assert both["published_profile"] is not None
        draft = both["candidate_draft"]
        assert draft is not None
        assert draft["release_id"] == str(release_id)
        assert draft["index_version"] == 1
        assert draft["operation"] == "UPDATE"
        assert draft["quality_status"] == "REVIEW_REQUIRED"
        assert draft["status"] == "DRAFT"
        assert "reasoning" not in draft


async def test_document_profile_context_picks_newest_candidate_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with _knowledge_base() as (engine, knowledge_id, document_id):
        assert document_id is not None
        old_index = await _create_index(engine, knowledge_id, 1, "DEPRECATED")
        new_index = await _create_index(engine, knowledge_id, 2, "READY")
        old_release = await _create_release(engine, knowledge_id, old_index)
        new_release = await _create_release(engine, knowledge_id, new_index)
        for release_id in (old_release, new_release):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO problem_profile_draft (
                            id, release_id, kb_id, document_id, problem_code,
                            canonical_question
                        ) VALUES (
                            :id, :release_id, :kb_id, :document_id, :code, :question
                        )
                        """
                    ),
                    {
                        "id": uuid4(),
                        "release_id": release_id,
                        "kb_id": knowledge_id,
                        "document_id": document_id,
                        "code": f"CODE_{release_id}",
                        "question": f"问题 {release_id}？",
                    },
                )

        with caplog.at_level(logging.WARNING, logger="app.releases.repository"):
            context = await ReleaseRepository(engine).document_profile_context(document_id)

        assert context is not None
        draft = context["candidate_draft"]
        assert draft is not None
        assert draft["release_id"] == str(new_release)
        assert any("open candidate Profile drafts" in message for message in caplog.messages)


async def test_document_profile_context_hides_published_and_cancelled_drafts() -> None:
    async with _knowledge_base() as (engine, knowledge_id, document_id):
        assert document_id is not None
        published_index = await _create_index(engine, knowledge_id, 1, "DEPRECATED")
        cancelled_index = await _create_index(engine, knowledge_id, 2, "DEPRECATED")
        for index_id, status in (
            (published_index, "PUBLISHED"),
            (cancelled_index, "CANCELLED"),
        ):
            release_id = await _create_release(engine, knowledge_id, index_id, status)
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO problem_profile_draft (
                            id, release_id, kb_id, document_id, problem_code,
                            canonical_question
                        ) VALUES (
                            :id, :release_id, :kb_id, :document_id, 'X', 'X？'
                        )
                        """
                    ),
                    {
                        "id": uuid4(),
                        "release_id": release_id,
                        "kb_id": knowledge_id,
                        "document_id": document_id,
                    },
                )

        context = await ReleaseRepository(engine).document_profile_context(document_id)

        assert context is not None
        assert context["candidate_draft"] is None


@pytest.mark.skipif(not _redis_available(), reason="test Redis is not reachable")
async def test_submit_post_build_profile_refreshes_when_intent_present() -> None:
    async with _knowledge_base() as (engine, knowledge_id, document_id):
        assert document_id is not None
        index_id = await _create_index(engine, knowledge_id, 1, "READY")
        await _create_release(engine, knowledge_id, index_id)
        build_task_id = await _create_task(
            engine,
            task_type="INDEX_BUILD",
            knowledge_id=knowledge_id,
            index_id=index_id,
            status="SUCCEEDED",
            metadata={"profile_refresh_after_build": True},
        )
        settings = _settings()

        class _Resources:
            database = engine

        await _submit_post_build_profile(
            settings,
            _Resources(),  # type: ignore[arg-type]
            IndexBuildResult(
                index_id=index_id, document_count=1, element_count=1, chunk_count=1,
                followup_build=None,
            ),
            build_task_id,
        )

        profile_task = await _fetch_one(
            engine,
            "SELECT id, task_type, status FROM task_record "
            "WHERE index_id = :index_id AND task_type LIKE 'PROFILE_%'",
            index_id=index_id,
        )
        assert profile_task is not None
        assert profile_task["task_type"] == "PROFILE_REFRESH"
        build_task = await _task(engine, build_task_id)
        assert build_task["metadata"]["profile_task_id"] == str(profile_task["id"])


@pytest.mark.skipif(not _redis_available(), reason="test Redis is not reachable")
async def test_submit_post_build_profile_generates_without_intent() -> None:
    async with _knowledge_base() as (engine, knowledge_id, document_id):
        assert document_id is not None
        index_id = await _create_index(engine, knowledge_id, 1, "READY")
        await _create_release(engine, knowledge_id, index_id)
        build_task_id = await _create_task(
            engine,
            task_type="INDEX_BUILD",
            knowledge_id=knowledge_id,
            index_id=index_id,
            status="SUCCEEDED",
        )
        settings = _settings()

        class _Resources:
            database = engine

        await _submit_post_build_profile(
            settings,
            _Resources(),  # type: ignore[arg-type]
            IndexBuildResult(
                index_id=index_id, document_count=1, element_count=1, chunk_count=1,
                followup_build=None,
            ),
            build_task_id,
        )

        profile_task = await _fetch_one(
            engine,
            "SELECT task_type FROM task_record "
            "WHERE index_id = :index_id AND task_type LIKE 'PROFILE_%'",
            index_id=index_id,
        )
        assert profile_task is not None
        assert profile_task["task_type"] == "PROFILE_GENERATE"


async def test_submit_post_build_profile_intent_failure_fails_task() -> None:
    async with _knowledge_base() as (engine, knowledge_id, document_id):
        assert document_id is not None
        index_id = await _create_index(engine, knowledge_id, 1, "READY")
        # A published release is not editable: the refresh submission fails.
        await _create_release(engine, knowledge_id, index_id, "PUBLISHED")
        build_task_id = await _create_task(
            engine,
            task_type="INDEX_BUILD",
            knowledge_id=knowledge_id,
            index_id=index_id,
            status="SUCCEEDED",
            metadata={"profile_refresh_after_build": True},
        )
        settings = _settings()

        class _Resources:
            database = engine

        with pytest.raises(ApiError):
            await _submit_post_build_profile(
                settings,
                _Resources(),  # type: ignore[arg-type]
                IndexBuildResult(
                    index_id=index_id, document_count=1, element_count=1, chunk_count=1,
                    followup_build=None,
                ),
                build_task_id,
            )

        task = await _task(engine, build_task_id)
        assert task["status"] == "FAILED"
        assert task["error_code"] == "RELEASE_NOT_EDITABLE"
        assert "已发布或已取消" in task["error_message"]


async def test_submit_post_build_profile_carries_intent_to_followup() -> None:
    from app.ingestion.repository import BuildReference

    async with _knowledge_base() as (engine, knowledge_id, document_id):
        assert document_id is not None
        index_id = await _create_index(engine, knowledge_id, 1, "READY")
        await _create_release(engine, knowledge_id, index_id)
        build_task_id = await _create_task(
            engine,
            task_type="INDEX_BUILD",
            knowledge_id=knowledge_id,
            index_id=index_id,
            status="SUCCEEDED",
            metadata={"profile_refresh_after_build": True},
        )
        followup_index_id = await _create_index(engine, knowledge_id, 2, "BUILDING")
        followup_task_id = await _create_task(
            engine,
            task_type="INDEX_BUILD",
            knowledge_id=knowledge_id,
            index_id=followup_index_id,
        )
        settings = _settings()

        class _Resources:
            database = engine

        await _submit_post_build_profile(
            settings,
            _Resources(),  # type: ignore[arg-type]
            IndexBuildResult(
                index_id=index_id, document_count=1, element_count=1, chunk_count=1,
                followup_build=BuildReference(
                    requested=True,
                    coalesced=False,
                    index_id=followup_index_id,
                    task_id=followup_task_id,
                    rebuild_required=True,
                ),
            ),
            build_task_id,
        )

        followup_task = await _task(engine, followup_task_id)
        assert followup_task["metadata"]["profile_refresh_after_build"] is True
        profile_tasks = await _fetch_one(
            engine,
            "SELECT count(*) AS total FROM task_record "
            "WHERE index_id = :index_id AND task_type LIKE 'PROFILE_%'",
            index_id=index_id,
        )
        assert profile_tasks["total"] == 0


async def test_service_rebuild_enqueue_failure_fails_task() -> None:
    async with _knowledge_base() as (engine, knowledge_id, document_id):
        assert document_id is not None
        index_id = await _create_index(engine, knowledge_id, 1, "READY")
        await _create_release(engine, knowledge_id, index_id)
        service = IngestionService(
            IngestionRepository(engine, _settings()),
            minio=None,  # type: ignore[arg-type]
            settings=_settings(redis_url="redis://:password@127.0.0.1:1/15"),
        )

        with pytest.raises(ApiError) as raised:
            await service.request_profile_rebuild(knowledge_id)

        assert raised.value.code == "UPSTREAM_UNAVAILABLE"
        task = await _fetch_one(
            engine,
            "SELECT status FROM task_record WHERE index_id = :index_id "
            "AND task_type = 'PROFILE_REFRESH'",
            index_id=index_id,
        )
        assert task is not None and task["status"] == "FAILED"
