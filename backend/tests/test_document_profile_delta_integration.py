"""Real-PostgreSQL tests for incremental document Profile generation.

Covers the content-hash delta filter (unchanged documents are skipped,
manual edits are preserved, changed content regenerates) and the
request_generation zombie guard (heartbeat-stale RUNNING tasks are failed
instead of replayed).  Skipped unless ``SUPPORT_TEST_DATABASE_URL`` points
at a migrated database.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, ClassVar
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.common.errors import ApiError
from app.core.config import Settings
from app.document_profiles.generator import GeneratedProfile
from app.document_profiles.repository import DocumentProfileRepository
from app.document_profiles.service import DocumentProfileService

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
async def _knowledge_base() -> AsyncIterator[tuple[AsyncEngine, UUID]]:
    knowledge_id = uuid4()
    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO knowledge_base (id, name, status) VALUES (:id, :name, 'ENABLED')"
                ),
                {"id": knowledge_id, "name": f"profile-delta-{knowledge_id}"},
            )
        try:
            yield engine, knowledge_id
        finally:
            async with engine.begin() as connection:
                await connection.execute(
                    text("DELETE FROM knowledge_base WHERE id = :id"), {"id": knowledge_id}
                )
    finally:
        await engine.dispose()


async def _create_active_index(engine: AsyncEngine, knowledge_id: UUID) -> UUID:
    index_id = uuid4()
    async with engine.begin() as connection:
        model_id = (
            await connection.execute(
                text("SELECT id FROM model_config WHERE model_type = 'EMBEDDING' LIMIT 1")
            )
        ).scalar_one()
        await connection.execute(
            text(
                """
                INSERT INTO knowledge_index (
                    id, kb_id, version, status, embedding_model_id,
                    embedding_model_name, embedding_dimension, build_reason
                ) VALUES (
                    :id, :kb_id, 1, 'ACTIVE', :model_id, 'delta-test-model', 1024, 'INITIAL'
                )
                """
            ),
            {"id": index_id, "kb_id": knowledge_id, "model_id": model_id},
        )
        await connection.execute(
            text("UPDATE knowledge_base SET active_index_id = :index_id WHERE id = :kb_id"),
            {"index_id": index_id, "kb_id": knowledge_id},
        )
    return index_id


async def _create_document(
    engine: AsyncEngine, knowledge_id: UUID, index_id: UUID, name: str, content: str
) -> UUID:
    document_id = uuid4()
    index_document_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO document_source (
                    id, kb_id, filename, display_name, minio_bucket, minio_object_key,
                    file_hash, file_size, mime_type
                ) VALUES (
                    :id, :kb_id, :filename, :name, 'bucket', :key, :hash, 1,
                    'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                )
                """
            ),
            {
                "id": document_id,
                "kb_id": knowledge_id,
                "filename": f"{name}.docx",
                "name": name,
                "key": f"delta/{document_id}/source.docx",
                "hash": hashlib.sha256(name.encode()).hexdigest(),
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO index_document (id, index_id, document_id, source_hash, status)
                VALUES (:id, :index_id, :document_id, :hash, 'READY')
                """
            ),
            {
                "id": index_document_id,
                "index_id": index_id,
                "document_id": document_id,
                "hash": hashlib.sha256(name.encode()).hexdigest(),
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO document_element (
                    id, index_id, index_document_id, document_id, element_type, sequence_no, content
                ) VALUES (:id, :index_id, :index_document_id, :document_id, 'TEXT', 1, :content)
                """
            ),
            {
                "id": uuid4(),
                "index_id": index_id,
                "index_document_id": index_document_id,
                "document_id": document_id,
                "content": content,
            },
        )
    return document_id


class _StubGenerator:
    """Deterministic generator: no LLM, distinct intents per content."""

    # When set, generate() returns an upstream-error fallback once per document.
    fail_next: ClassVar[set[str]] = set()

    def __init__(self, settings: Settings) -> None:
        del settings

    async def generate(self, document: dict[str, Any], provider: Any) -> tuple[Any, ...]:
        content = str(document["content"])
        document_key = str(document["document_id"])
        profile = GeneratedProfile(
            problem_code=f"DOC_{document_key[:8].upper()}",
            canonical_question=f"如何处理 {content[:12]}？",
            aliases=[],
            keywords=[],
        )
        if document_key in _StubGenerator.fail_next:
            _StubGenerator.fail_next.discard(document_key)
            return (
                profile,
                "FALLBACK",
                "UPSTREAM_UNAVAILABLE",
                "FALLBACK",
                ["离线范围提取或生成失败：UPSTREAM_UNAVAILABLE"],
            )
        return profile, "AUTO", None, "PASS", []


def _stub_fail_for(*document_ids: UUID) -> None:
    _StubGenerator.fail_next.update(str(doc_id) for doc_id in document_ids)


async def _run_generation(
    engine: AsyncEngine,
    settings: Settings,
    knowledge_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    task_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO task_record (id, task_type, status, stage, kb_id, document_id, metadata)
                VALUES (:id, 'DOCUMENT_PROFILE_GENERATE', 'RUNNING', 'GENERATING', :kb_id, NULL,
                        CAST(:metadata AS jsonb))
                """
            ),
            {
                "id": task_id,
                "kb_id": knowledge_id,
                "metadata": json.dumps({"knowledge_id": str(knowledge_id)}),
            },
        )
    monkeypatch.setattr("app.document_profiles.service.DocumentProfileGenerator", _StubGenerator)
    service = DocumentProfileService(DocumentProfileRepository(engine), settings)
    task = {"id": task_id, "kb_id": knowledge_id, "document_id": None, "metadata": {}}

    from app.tasks.repository import TaskRecordRepository

    result = await service.execute_generation_task(task, TaskRecordRepository(engine))
    return result


async def _profile_row(engine: AsyncEngine, document_id: UUID) -> dict[str, Any] | None:
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text(
                        "SELECT pending_source, pending_state, content_hash FROM document_profile"
                        " WHERE document_id = :id"
                    ),
                    {"id": document_id},
                )
            )
            .mappings()
            .one_or_none()
        )
    return dict(row) if row is not None else None


async def test_incremental_run_skips_unchanged_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _knowledge_base() as (engine, knowledge_id):
        settings = _settings()
        index_id = await _create_active_index(engine, knowledge_id)
        doc_a = await _create_document(engine, knowledge_id, index_id, "doc-a", "甲文档内容")
        doc_b = await _create_document(engine, knowledge_id, index_id, "doc-b", "乙文档内容")

        first = await _run_generation(engine, settings, knowledge_id, monkeypatch)
        assert first["generated_count"] == 2
        assert first["skipped_count"] == 0
        for doc_id in (doc_a, doc_b):
            row = await _profile_row(engine, doc_id)
            assert row is not None
            assert row["pending_state"] == "PENDING_ONLINE"
            assert (
                row["content_hash"]
                == hashlib.sha256(
                    ("甲文档内容" if doc_id == doc_a else "乙文档内容").encode()
                ).hexdigest()
            )

        # Nothing changed: the second full run performs zero LLM work.
        second = await _run_generation(engine, settings, knowledge_id, monkeypatch)
        assert second["generated_count"] == 0
        assert second["skipped_count"] == 2


async def test_changed_content_regenerates_only_that_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _knowledge_base() as (engine, knowledge_id):
        settings = _settings()
        index_id = await _create_active_index(engine, knowledge_id)
        doc_a = await _create_document(engine, knowledge_id, index_id, "doc-a", "旧内容")
        await _create_document(engine, knowledge_id, index_id, "doc-b", "稳定内容")
        await _run_generation(engine, settings, knowledge_id, monkeypatch)

        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE document_element SET content = '全新内容' WHERE document_id = :id"),
                {"id": doc_a},
            )

        result = await _run_generation(engine, settings, knowledge_id, monkeypatch)
        assert result["generated_count"] == 1
        assert result["skipped_count"] == 1
        changed = await _profile_row(engine, doc_a)
        assert changed is not None
        assert changed["content_hash"] == hashlib.sha256("全新内容".encode()).hexdigest()


async def test_manual_pending_edit_is_preserved_by_auto_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _knowledge_base() as (engine, knowledge_id):
        settings = _settings()
        index_id = await _create_active_index(engine, knowledge_id)
        doc_a = await _create_document(engine, knowledge_id, index_id, "doc-a", "内容")
        await _run_generation(engine, settings, knowledge_id, monkeypatch)

        service = DocumentProfileService(DocumentProfileRepository(engine), settings)
        from app.api.schemas import DocumentProfilePatchRequest

        await service.edit(
            knowledge_id,
            doc_a,
            DocumentProfilePatchRequest(
                problem_code="MANUAL_001",
                canonical_question="人工维护的标准问法",
                aliases=[],
                keywords=[],
            ),
        )
        manual = await _profile_row(engine, doc_a)
        assert manual is not None and manual["pending_source"] == "MANUAL"

        result = await _run_generation(engine, settings, knowledge_id, monkeypatch)
        assert result["skipped_count"] == 1
        assert result["generated_count"] == 0
        preserved = await _profile_row(engine, doc_a)
        assert preserved is not None
        assert preserved["pending_source"] == "MANUAL"


async def test_request_generation_fails_stale_zombie_instead_of_replaying() -> None:
    async with _knowledge_base() as (engine, knowledge_id):
        zombie = uuid4()
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO task_record (id, task_type, status, stage, kb_id, metadata)
                    VALUES (:id, 'DOCUMENT_PROFILE_GENERATE', 'RUNNING', 'GENERATING', :kb_id, '{}')
                    """
                ),
                {"id": zombie, "kb_id": knowledge_id},
            )
            await connection.execute(
                text(
                    "UPDATE task_record SET updated_at = now() - interval '2 hours' WHERE id = :id"
                ),
                {"id": zombie},
            )

        service = DocumentProfileService(DocumentProfileRepository(engine), _settings())
        # Enqueue fails (no Redis here): the fresh task is marked FAILED by the
        # bookkeeping path, but crucially a NEW task was created.
        with pytest.raises(ApiError) as exc_info:
            await service.request_generation(knowledge_id)
        assert exc_info.value.code == "UPSTREAM_UNAVAILABLE"

        async with engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            "SELECT id, status FROM task_record "
                            "WHERE kb_id = :kb_id AND task_type = 'DOCUMENT_PROFILE_GENERATE'"
                        ),
                        {"kb_id": knowledge_id},
                    )
                )
                .mappings()
                .all()
            )
        by_id = {str(row["id"]): str(row["status"]) for row in rows}
        assert by_id[str(zombie)] == "FAILED"
        assert sum(status == "FAILED" for status in by_id.values()) == 2


async def test_request_generation_replays_fresh_running_task() -> None:
    async with _knowledge_base() as (engine, knowledge_id):
        active = uuid4()
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO task_record (id, task_type, status, stage, kb_id, metadata)
                    VALUES (:id, 'DOCUMENT_PROFILE_GENERATE', 'RUNNING', 'GENERATING', :kb_id, '{}')
                    """
                ),
                {"id": active, "kb_id": knowledge_id},
            )

        service = DocumentProfileService(DocumentProfileRepository(engine), _settings())
        replay = await service.request_generation(knowledge_id)
        assert replay["replayed"] is True
        assert str(replay["task_id"]) == str(active)


async def test_error_fallback_is_retried_by_next_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: an upstream-error fallback must not be skipped as unchanged.

    The 02:37 incident: error rows recorded a content hash, so "regenerate
    all" skipped 46 documents in one second while every profile was broken.
    """
    async with _knowledge_base() as (engine, knowledge_id):
        settings = _settings()
        index_id = await _create_active_index(engine, knowledge_id)
        doc_a = await _create_document(engine, knowledge_id, index_id, "doc-a", "内容")

        _stub_fail_for(doc_a)
        first = await _run_generation(engine, settings, knowledge_id, monkeypatch)
        assert first["generated_count"] == 1
        row = await _profile_row(engine, doc_a)
        assert row is not None
        assert row["pending_state"] == "NEEDS_ATTENTION"
        assert row["content_hash"] is None  # error result records no hash

        # Upstream recovered: the same unchanged document is regenerated.
        second = await _run_generation(engine, settings, knowledge_id, monkeypatch)
        assert second["generated_count"] == 1
        assert second["skipped_count"] == 0
        recovered = await _profile_row(engine, doc_a)
        assert recovered is not None
        assert recovered["pending_state"] == "PENDING_ONLINE"
        assert recovered["content_hash"] == hashlib.sha256("内容".encode()).hexdigest()


async def test_discarded_profile_regenerates_despite_matching_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matching hash is not enough when nothing is left to keep."""
    async with _knowledge_base() as (engine, knowledge_id):
        settings = _settings()
        index_id = await _create_active_index(engine, knowledge_id)
        doc_a = await _create_document(engine, knowledge_id, index_id, "doc-a", "内容")
        await _run_generation(engine, settings, knowledge_id, monkeypatch)

        await DocumentProfileRepository(engine).discard_pending(knowledge_id)
        row = await _profile_row(engine, doc_a)
        assert row is not None and row["pending_state"] is None

        result = await _run_generation(engine, settings, knowledge_id, monkeypatch)
        assert result["generated_count"] == 1
        assert result["skipped_count"] == 0


async def test_online_profile_with_matching_hash_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Content unchanged and a published profile exists: keep it."""
    async with _knowledge_base() as (engine, knowledge_id):
        settings = _settings()
        index_id = await _create_active_index(engine, knowledge_id)
        doc_a = await _create_document(engine, knowledge_id, index_id, "doc-a", "内容")
        await _run_generation(engine, settings, knowledge_id, monkeypatch)

        # Simulate publish: pending moves to active, hash column survives.
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE document_profile
                    SET active_payload = pending_payload, pending_payload = NULL,
                        pending_source = NULL, pending_state = NULL,
                        pending_error_code = NULL, pending_error_message = NULL
                    WHERE document_id = :id
                    """
                ),
                {"id": doc_a},
            )

        result = await _run_generation(engine, settings, knowledge_id, monkeypatch)
        assert result["generated_count"] == 0
        assert result["skipped_count"] == 1
