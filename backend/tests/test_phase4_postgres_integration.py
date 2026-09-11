"""Phase 4 integration tests: rebase, REMOVE drafts, rollbacks and GC guards.

These tests run against a real PostgreSQL database (``PHASE4_TEST_DATABASE_URL``)
so transactional reference checks, ON CONFLICT restores and pointer switches
are exercised exactly as in production.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.common.errors import ApiError
from app.core.config import Settings
from app.ingestion.repository import IngestionRepository
from app.rag.repository import RagRepository
from app.releases.models import PROFILE_ENRICHMENT_VERSION
from app.releases.repository import ReleaseRepository

pytestmark = pytest.mark.asyncio

FILE_HASH_A1 = "a" * 64
FILE_HASH_A2 = "b" * 64
FILE_HASH_B = "c" * 64
FILE_HASH_C = "d" * 64


@asynccontextmanager
async def _engine() -> AsyncIterator[AsyncEngine]:
    database_url = os.getenv("PHASE4_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PHASE4_TEST_DATABASE_URL is not configured")
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@asynccontextmanager
async def _knowledge_base(engine: AsyncEngine) -> AsyncIterator[UUID]:
    knowledge_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO knowledge_base (id, name) VALUES (:id, :name)"),
            {"id": knowledge_id, "name": f"phase4-{knowledge_id}"},
        )
    try:
        yield knowledge_id
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM knowledge_base WHERE id = :id"), {"id": knowledge_id}
            )


async def _insert_index(
    engine: AsyncEngine,
    knowledge_id: UUID,
    version: int,
    status: str,
    *,
    created_at: datetime | None = None,
) -> UUID:
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
                    embedding_model_name, embedding_dimension, build_reason, created_at
                ) VALUES (
                    :id, :kb_id, :version, :status, :model_id,
                    'phase4-model', 1024, 'INITIAL', COALESCE(:created_at, now())
                )
                """
            ),
            {
                "id": index_id,
                "kb_id": knowledge_id,
                "version": version,
                "status": status,
                "model_id": model_id,
                "created_at": created_at,
            },
        )
    return index_id


async def _insert_document(
    engine: AsyncEngine, knowledge_id: UUID, name: str, file_hash: str
) -> UUID:
    document_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO document_source (
                    id, kb_id, filename, display_name, minio_bucket,
                    minio_object_key, file_hash, file_size, mime_type
                ) VALUES (
                    :id, :kb_id, :filename, :display_name, 'test',
                    :object_key, :file_hash, 1, 'application/docx'
                )
                """
            ),
            {
                "id": document_id,
                "kb_id": knowledge_id,
                "filename": f"{name}.docx",
                "display_name": name,
                "object_key": f"phase4/{document_id}",
                "file_hash": file_hash,
            },
        )
    return document_id


async def _link_document(
    engine: AsyncEngine, index_id: UUID, document_id: UUID, file_hash: str
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO index_document (id, index_id, document_id, source_hash, status)
                VALUES (:id, :index_id, :document_id, :source_hash, 'READY')
                """
            ),
            {
                "id": uuid4(),
                "index_id": index_id,
                "document_id": document_id,
                "source_hash": file_hash,
            },
        )


async def test_document_rename_is_visible_to_future_catalog_reads_without_rebuild() -> None:
    async with _engine() as engine:
        async with _knowledge_base(engine) as knowledge_id:
            document_id = await _insert_document(engine, knowledge_id, "旧名称", FILE_HASH_A1)
            await _insert_document(engine, knowledge_id, "占用名称", FILE_HASH_A2)
            settings = Settings(_env_file=None, app_env="test")
            ingestion_repository = IngestionRepository(engine, settings)
            rag_repository = RagRepository(engine, settings)

            updated = await ingestion_repository.rename_document(document_id, "新名称")
            catalog = await rag_repository.list_kb_documents(knowledge_id)

            assert updated["display_name"] == "新名称"
            assert updated["filename"] == "旧名称.docx"
            assert {item["document_id"]: item["title"] for item in catalog}[str(document_id)] == (
                "新名称"
            )
            with pytest.raises(ApiError) as raised:
                await ingestion_repository.rename_document(document_id, "占用名称")
            assert raised.value.code == "DOCUMENT_NAME_CONFLICT"


async def _insert_elements(engine: AsyncEngine, index_id: UUID, document_id: UUID) -> None:
    async with engine.begin() as connection:
        index_document_id = (
            await connection.execute(
                text(
                    """
                    SELECT id FROM index_document
                    WHERE index_id = :index_id AND document_id = :document_id
                    """
                ),
                {"index_id": index_id, "document_id": document_id},
            )
        ).scalar_one()
        await connection.execute(
            text(
                """
                INSERT INTO document_element (id, index_id, index_document_id,
                    document_id, sequence_no, element_type, content)
                VALUES (:id, :index_id, :index_document_id, :document_id, 1, 'TEXT', '正文')
                """
            ),
            {
                "id": uuid4(),
                "index_id": index_id,
                "index_document_id": index_document_id,
                "document_id": document_id,
            },
        )


async def _insert_release(
    engine: AsyncEngine,
    knowledge_id: UUID,
    index_id: UUID,
    status: str = "DRAFT",
) -> UUID:
    release_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO knowledge_release (id, kb_id, index_id, status)
                VALUES (:id, :kb_id, :index_id, :status)
                """
            ),
            {
                "id": release_id,
                "kb_id": knowledge_id,
                "index_id": index_id,
                "status": status,
            },
        )
    return release_id


async def _insert_draft(
    engine: AsyncEngine,
    release_id: UUID,
    knowledge_id: UUID,
    document_id: UUID,
    *,
    operation: str = "CREATE",
    status: str = "APPROVED",
    problem_code: str = "PHASE4_CODE",
    target_profile_id: UUID | None = None,
    aliases: list[str] | None = None,
) -> UUID:
    draft_id = uuid4()

    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO problem_profile_draft (
                    id, release_id, kb_id, document_id, operation,
                    target_profile_id, problem_code, canonical_question,
                    aliases, keywords, generation_source, status
                ) VALUES (
                    :id, :release_id, :kb_id, :document_id, :operation,
                    :target_profile_id, :problem_code, :canonical_question,
                    CAST(:aliases AS jsonb), '[]'::jsonb, 'MANUAL', :status
                )
                """
            ),
            {
                "id": draft_id,
                "release_id": release_id,
                "kb_id": knowledge_id,
                "document_id": document_id,
                "operation": operation,
                "target_profile_id": target_profile_id,
                "problem_code": problem_code,
                "canonical_question": f"{problem_code} 的标准问题？",
                "aliases": json.dumps(aliases or [], ensure_ascii=False),
                "status": status,
            },
        )
    return draft_id


async def _insert_live_profile(
    engine: AsyncEngine,
    knowledge_id: UUID,
    document_id: UUID,
    problem_code: str,
    *,
    enabled: bool = True,
    canonical_question: str | None = None,
) -> UUID:
    profile_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO document_problem_profile (
                    id, document_id, kb_id, problem_code, canonical_question,
                    aliases, keywords, profile_enrichment_version, enabled
                ) VALUES (
                    :id, :document_id, :kb_id, :problem_code, :canonical_question,
                    '["人工别名"]'::jsonb, '[]'::jsonb, :version, :enabled
                )
                """
            ),
            {
                "id": profile_id,
                "document_id": document_id,
                "kb_id": knowledge_id,
                "problem_code": problem_code,
                "canonical_question": canonical_question or f"{problem_code} 的标准问题？",
                "version": PROFILE_ENRICHMENT_VERSION,
                "enabled": enabled,
            },
        )
    return profile_id


async def _fetch_one(engine: AsyncEngine, query: str, **params: Any) -> dict[str, Any]:
    async with engine.connect() as connection:
        row = (await connection.execute(text(query), params)).mappings().one_or_none()
    return dict(row) if row is not None else {}


async def _seed_rebase_scenario(engine: AsyncEngine, knowledge_id: UUID) -> dict[str, UUID]:
    """Old candidate on v1 (docs A, B); current RAG index v2 (docs A', C)."""
    old_index = await _insert_index(engine, knowledge_id, 1, "DEPRECATED")
    new_index = await _insert_index(engine, knowledge_id, 2, "ACTIVE")
    document_a = await _insert_document(engine, knowledge_id, "文档A", FILE_HASH_A1)
    document_b = await _insert_document(engine, knowledge_id, "文档B", FILE_HASH_B)
    document_c = await _insert_document(engine, knowledge_id, "文档C", FILE_HASH_C)
    await _link_document(engine, old_index, document_a, FILE_HASH_A1)
    await _link_document(engine, old_index, document_b, FILE_HASH_B)
    # v2 rebuilds document A (new hash) and drops document B, adds C.
    await _link_document(engine, new_index, document_a, FILE_HASH_A2)
    await _link_document(engine, new_index, document_c, FILE_HASH_C)
    await _insert_elements(engine, new_index, document_a)
    await _insert_elements(engine, new_index, document_c)
    profile_b = await _insert_live_profile(engine, knowledge_id, document_b, "DOC_B_PROBLEM")
    source_release = await _insert_release(engine, knowledge_id, old_index)
    await _insert_draft(
        engine,
        source_release,
        knowledge_id,
        document_a,
        operation="CREATE",
        status="APPROVED",
        problem_code="DOC_A_PROBLEM",
        aliases=["人工编辑的别名"],
    )
    await _insert_draft(
        engine,
        source_release,
        knowledge_id,
        document_b,
        operation="UPDATE",
        status="APPROVED",
        problem_code="DOC_B_PROBLEM",
        target_profile_id=profile_b,
    )
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                UPDATE knowledge_base
                SET rag_active_index_id = :index_id, active_index_id = :index_id
                WHERE id = :kb_id
                """
            ),
            {"index_id": new_index, "kb_id": knowledge_id},
        )
    return {
        "old_index": old_index,
        "new_index": new_index,
        "document_a": document_a,
        "document_b": document_b,
        "document_c": document_c,
        "profile_b": profile_b,
        "source_release": source_release,
    }


async def test_rebase_reuses_human_drafts_and_suggests_remove() -> None:
    async with _engine() as engine:
        async with _knowledge_base(engine) as knowledge_id:
            seeded = await _seed_rebase_scenario(engine, knowledge_id)
            repository = ReleaseRepository(engine)

            report = await repository.rebase_release(seeded["source_release"])

            assert report["target_index_id"] == str(seeded["new_index"])
            assert report["reused_draft_count"] == 0  # document A changed on v2
            assert report["regenerate_document_count"] == 1
            assert report["remove_suggestion_count"] == 1
            assert report["create_suggestion_count"] == 1

            source = await _fetch_one(
                engine,
                "SELECT status FROM knowledge_release WHERE id = :id",
                id=seeded["source_release"],
            )
            assert source["status"] == "CANCELLED"
            target = await _fetch_one(
                engine,
                """
                SELECT id, status FROM knowledge_release
                WHERE index_id = :index_id
                """,
                index_id=seeded["new_index"],
            )
            assert target["status"] == "DRAFT"

            drafts = [
                await _fetch_one(
                    engine,
                    "SELECT * FROM problem_profile_draft WHERE id = :id",
                    id=row_id,
                )
                for row_id in (
                    await _fetch_one(
                        engine,
                        """
                        SELECT array_agg(id) AS ids FROM problem_profile_draft
                        WHERE release_id = :release_id
                        """,
                        release_id=target["id"],
                    )
                )["ids"]
            ]
            removed = next(
                row for row in drafts if UUID(str(row["document_id"])) == seeded["document_b"]
            )
            assert removed["operation"] == "REMOVE"
            assert UUID(str(removed["target_profile_id"])) == seeded["profile_b"]
            assert removed["status"] == "DRAFT"
            assert removed["quality_status"] == "REVIEW_REQUIRED"
            changed = next(
                row for row in drafts if UUID(str(row["document_id"])) == seeded["document_a"]
            )
            assert changed["status"] == "DRAFT"
            assert changed["quality_status"] == "REVIEW_REQUIRED"
            assert len(drafts) == 2  # document C awaits backfill generation


async def test_rebase_reuses_unchanged_human_edits_verbatim() -> None:
    async with _engine() as engine:
        async with _knowledge_base(engine) as knowledge_id:
            old_index = await _insert_index(engine, knowledge_id, 1, "DEPRECATED")
            new_index = await _insert_index(engine, knowledge_id, 2, "ACTIVE")
            document = await _insert_document(engine, knowledge_id, "稳定文档", FILE_HASH_A1)
            await _link_document(engine, old_index, document, FILE_HASH_A1)
            await _link_document(engine, new_index, document, FILE_HASH_A1)
            await _insert_elements(engine, new_index, document)
            source_release = await _insert_release(engine, knowledge_id, old_index)
            await _insert_draft(
                engine,
                source_release,
                knowledge_id,
                document,
                operation="CREATE",
                status="APPROVED",
                problem_code="STABLE_PROBLEM",
                aliases=["人工确认的别名"],
            )
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        UPDATE knowledge_base
                        SET rag_active_index_id = :index_id, active_index_id = :index_id
                        WHERE id = :kb_id
                        """
                    ),
                    {"index_id": new_index, "kb_id": knowledge_id},
                )

            report = await ReleaseRepository(engine).rebase_release(source_release)

            assert report["reused_draft_count"] == 1
            reused = await _fetch_one(
                engine,
                """
                SELECT d.* FROM problem_profile_draft d
                JOIN knowledge_release r ON r.id = d.release_id
                WHERE r.index_id = :index_id
                """,
                index_id=new_index,
            )
            assert reused["status"] == "APPROVED"
            assert reused["aliases"] == ["人工确认的别名"]
            assert reused["problem_code"] == "STABLE_PROBLEM"


async def test_rebase_rejects_current_or_published_targets() -> None:
    async with _engine() as engine:
        async with _knowledge_base(engine) as knowledge_id:
            index = await _insert_index(engine, knowledge_id, 1, "ACTIVE")
            release = await _insert_release(engine, knowledge_id, index)
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        UPDATE knowledge_base
                        SET rag_active_index_id = :index_id, active_index_id = :index_id
                        WHERE id = :kb_id
                        """
                    ),
                    {"index_id": index, "kb_id": knowledge_id},
                )
            with pytest.raises(ApiError) as raised:
                await ReleaseRepository(engine).rebase_release(release)
            assert raised.value.code == "RELEASE_ALREADY_CURRENT"

            new_index = await _insert_index(engine, knowledge_id, 2, "DEPRECATED")
            await _insert_release(engine, knowledge_id, new_index, status="PUBLISHED")
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        UPDATE knowledge_base
                        SET rag_active_index_id = :index_id, active_index_id = :index_id
                        WHERE id = :kb_id
                        """
                    ),
                    {"index_id": new_index, "kb_id": knowledge_id},
                )
            with pytest.raises(ApiError) as raised:
                await ReleaseRepository(engine).rebase_release(release)
            assert raised.value.code == "RELEASE_TARGET_NOT_EDITABLE"


async def _publish_release(
    engine: AsyncEngine,
    repository: ReleaseRepository,
    release_id: UUID,
) -> dict[str, Any]:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                UPDATE knowledge_release
                SET status = 'READY_TO_PUBLISH', validation_result = '{}'::jsonb
                WHERE id = :id
                """
            ),
            {"id": release_id},
        )
        await connection.execute(
            text(
                """
                UPDATE problem_profile_draft
                SET status = 'APPROVED', quality_status = 'PASS'
                WHERE release_id = :id
                """
            ),
            {"id": release_id},
        )
    return await repository.publish(release_id)


async def test_publish_remove_draft_disables_profile_and_snapshots() -> None:
    async with _engine() as engine:
        async with _knowledge_base(engine) as knowledge_id:
            index = await _insert_index(engine, knowledge_id, 1, "ACTIVE")
            document = await _insert_document(engine, knowledge_id, "待移除", FILE_HASH_A1)
            await _link_document(engine, index, document, FILE_HASH_A1)
            profile = await _insert_live_profile(engine, knowledge_id, document, "REMOVE_ME")
            release = await _insert_release(engine, knowledge_id, index)
            await _insert_draft(
                engine,
                release,
                knowledge_id,
                document,
                operation="REMOVE",
                status="APPROVED",
                problem_code="REMOVE_ME",
                target_profile_id=profile,
            )
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        UPDATE knowledge_base
                        SET rag_active_index_id = :index_id, active_index_id = :index_id
                        WHERE id = :kb_id
                        """
                    ),
                    {"index_id": index, "kb_id": knowledge_id},
                )

            published = await _publish_release(engine, ReleaseRepository(engine), release)

            assert published["status"] == "PUBLISHED"
            disabled = await _fetch_one(
                engine,
                "SELECT enabled FROM document_problem_profile WHERE id = :id",
                id=profile,
            )
            assert disabled["enabled"] is False
            snapshot = await _fetch_one(
                engine,
                """
                SELECT count(*) AS total FROM support_release_profile
                WHERE release_id = :release_id
                """,
                release_id=release,
            )
            assert snapshot["total"] == 1
            pointers = await _fetch_one(
                engine,
                """
                SELECT support_active_release_id, support_active_index_id
                FROM knowledge_base WHERE id = :id
                """,
                id=knowledge_id,
            )
            assert str(pointers["support_active_release_id"]) == str(release)
            assert str(pointers["support_active_index_id"]) == str(index)


async def test_support_rollback_restores_snapshot_and_disables_newer_profiles() -> None:
    async with _engine() as engine:
        async with _knowledge_base(engine) as knowledge_id:
            repository = ReleaseRepository(engine)
            index = await _insert_index(engine, knowledge_id, 1, "DEPRECATED")
            document = await _insert_document(engine, knowledge_id, "文档A", FILE_HASH_A1)
            await _link_document(engine, index, document, FILE_HASH_A1)
            await _insert_elements(engine, index, document)
            profile = await _insert_live_profile(engine, knowledge_id, document, "ROLLBACK_CODE")
            first_release = await _insert_release(engine, knowledge_id, index)
            await _insert_draft(
                engine,
                first_release,
                knowledge_id,
                document,
                operation="UPDATE",
                status="APPROVED",
                problem_code="ROLLBACK_CODE",
                target_profile_id=profile,
                aliases=["第一版别名"],
            )
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        UPDATE knowledge_base
                        SET rag_active_index_id = :index_id, active_index_id = :index_id
                        WHERE id = :kb_id
                        """
                    ),
                    {"index_id": index, "kb_id": knowledge_id},
                )
            await _publish_release(engine, repository, first_release)
            original = await _fetch_one(
                engine,
                "SELECT aliases FROM document_problem_profile WHERE id = :id",
                id=profile,
            )

            second_index = await _insert_index(engine, knowledge_id, 2, "ACTIVE")
            document_b = await _insert_document(engine, knowledge_id, "文档B", FILE_HASH_B)
            await _link_document(engine, second_index, document_b, FILE_HASH_B)
            await _link_document(engine, second_index, document, FILE_HASH_A1)
            await _insert_elements(engine, second_index, document_b)
            await _insert_elements(engine, second_index, document)
            second_release = await _insert_release(engine, knowledge_id, second_index)
            await _insert_draft(
                engine,
                second_release,
                knowledge_id,
                document,
                operation="UPDATE",
                status="APPROVED",
                problem_code="ROLLBACK_CODE",
                target_profile_id=profile,
                aliases=["第二版别名"],
            )
            await _insert_draft(
                engine,
                second_release,
                knowledge_id,
                document_b,
                operation="CREATE",
                status="APPROVED",
                problem_code="SECOND_CODE",
            )
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        UPDATE knowledge_base
                        SET rag_active_index_id = :index_id, active_index_id = :index_id
                        WHERE id = :kb_id
                        """
                    ),
                    {"index_id": second_index, "kb_id": knowledge_id},
                )
            await _publish_release(engine, repository, second_release)
            newer = await _fetch_one(
                engine,
                "SELECT aliases FROM document_problem_profile WHERE id = :id",
                id=profile,
            )
            assert newer["aliases"] == ["第二版别名"]

            result = await repository.rollback_support_release(knowledge_id, first_release)

            assert result["support_active_release_id"] == str(first_release)
            assert result["support_active_index_id"] == str(index)
            rolled_back = await _fetch_one(
                engine,
                "SELECT aliases FROM document_problem_profile WHERE id = :id",
                id=profile,
            )
            assert rolled_back["aliases"] == original["aliases"]
            second_profile = await _fetch_one(
                engine,
                """
                SELECT enabled FROM document_problem_profile
                WHERE kb_id = :kb_id AND problem_code = 'SECOND_CODE'
                """,
                kb_id=knowledge_id,
            )
            assert second_profile["enabled"] is False
            rag_pointer = await _fetch_one(
                engine,
                """
                SELECT rag_active_index_id, support_active_release_id
                FROM knowledge_base WHERE id = :id
                """,
                id=knowledge_id,
            )
            assert str(rag_pointer["rag_active_index_id"]) == str(second_index)

            # Idempotent: rolling back to the now-active release is a no-op.
            again = await repository.rollback_support_release(knowledge_id, first_release)
            assert again["support_active_release_id"] == str(first_release)


async def test_support_rollback_requires_snapshot() -> None:
    async with _engine() as engine:
        async with _knowledge_base(engine) as knowledge_id:
            index = await _insert_index(engine, knowledge_id, 1, "ACTIVE")
            release = await _insert_release(engine, knowledge_id, index, status="PUBLISHED")
            with pytest.raises(ApiError) as raised:
                await ReleaseRepository(engine).rollback_support_release(knowledge_id, release)
            assert raised.value.code == "RELEASE_SNAPSHOT_MISSING"


async def test_rag_rollback_switches_only_rag_pointer() -> None:
    async with _engine() as engine:
        async with _knowledge_base(engine) as knowledge_id:
            first_index = await _insert_index(engine, knowledge_id, 1, "DEPRECATED")
            second_index = await _insert_index(engine, knowledge_id, 2, "ACTIVE")
            support_release = await _insert_release(engine, knowledge_id, first_index)
            conversation_id = uuid4()
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO conversation (
                            id, knowledge_id, rag_index_id, support_index_id,
                            support_release_id
                        ) VALUES (
                            :id, :kb_id, :rag_index, :support_index, :support_release
                        )
                        """
                    ),
                    {
                        "id": conversation_id,
                        "kb_id": knowledge_id,
                        "rag_index": second_index,
                        "support_index": first_index,
                        "support_release": support_release,
                    },
                )
                await connection.execute(
                    text(
                        """
                        UPDATE knowledge_base
                        SET rag_active_index_id = :index_id, active_index_id = :index_id,
                            support_active_index_id = :support_index,
                            support_active_release_id = :support_release
                        WHERE id = :kb_id
                        """
                    ),
                    {
                        "index_id": second_index,
                        "support_index": first_index,
                        "support_release": support_release,
                        "kb_id": knowledge_id,
                    },
                )
            settings = Settings(_env_file=None, app_env="test")
            repository = IngestionRepository(engine, settings)

            rolled = await repository.rollback_rag_index(knowledge_id, first_index)

            assert rolled["status"] == "ACTIVE"
            pointers = await _fetch_one(
                engine,
                """
                SELECT rag_active_index_id, active_index_id,
                       support_active_index_id, support_active_release_id
                FROM knowledge_base WHERE id = :id
                """,
                id=knowledge_id,
            )
            assert str(pointers["rag_active_index_id"]) == str(first_index)
            assert str(pointers["active_index_id"]) == str(first_index)
            assert str(pointers["support_active_index_id"]) == str(first_index)
            assert str(pointers["support_active_release_id"]) == str(support_release)
            statuses = await _index_statuses(engine, knowledge_id)
            assert statuses[str(first_index)] == "ACTIVE"
            assert statuses[str(second_index)] == "DEPRECATED"
            frozen = await _fetch_one(
                engine,
                """
                SELECT rag_index_id, support_index_id, support_release_id
                FROM conversation WHERE id = :id
                """,
                id=conversation_id,
            )
            assert str(frozen["rag_index_id"]) == str(second_index)

            # Idempotent rollback to the version that is already online.
            again = await repository.rollback_rag_index(knowledge_id, first_index)
            assert again["status"] == "ACTIVE"


async def _index_statuses(engine: AsyncEngine, knowledge_id: UUID) -> dict[str, str]:
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text("SELECT id, status FROM knowledge_index WHERE kb_id = :kb_id"),
                {"kb_id": knowledge_id},
            )
        ).mappings()
    return {str(row["id"]): str(row["status"]) for row in rows}


async def test_gc_protects_open_releases_and_active_task_references() -> None:
    async with _engine() as engine:
        async with _knowledge_base(engine) as knowledge_id:
            settings = Settings(_env_file=None, app_env="test")
            repository = IngestionRepository(engine, settings)
            active_index = await _insert_index(engine, knowledge_id, 5, "ACTIVE")
            await _insert_index(engine, knowledge_id, 4, "DEPRECATED")
            orphan = await _insert_index(
                engine, knowledge_id, 3, "DEPRECATED", created_at=datetime(2000, 1, 1, tzinfo=UTC)
            )
            open_index = await _insert_index(
                engine, knowledge_id, 2, "DEPRECATED", created_at=datetime(2000, 1, 1, tzinfo=UTC)
            )
            await _insert_release(engine, knowledge_id, open_index, status="DRAFT")
            await _insert_index(
                engine, knowledge_id, 1, "FAILED", created_at=datetime(2000, 1, 1, tzinfo=UTC)
            )

            deletable = await repository.find_deletable_indexes(0, 0)
            deletable_ids = {str(item.index_id) for item in deletable}
            assert str(orphan) in deletable_ids
            assert str(open_index) not in deletable_ids
            assert str(active_index) not in deletable_ids

            assert await repository.mark_index_deleting(orphan) is not None
            assert await repository.mark_index_deleting(open_index) is None

            # A queued task on the orphan index blocks physical deletion.
            task_id = uuid4()
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO task_record (
                            id, task_type, status, stage, kb_id, index_id
                        ) VALUES (
                            :id, 'PROFILE_GENERATE', 'RUNNING', 'GENERATING',
                            :kb_id, :index_id
                        )
                        """
                    ),
                    {"id": task_id, "kb_id": knowledge_id, "index_id": orphan},
                )
            assert await repository.delete_index(orphan) is False
            restored = await _fetch_one(
                engine,
                "SELECT status FROM knowledge_index WHERE id = :id",
                id=orphan,
            )
            assert restored["status"] == "DEPRECATED"


async def test_purge_expired_conversations_releases_frozen_references() -> None:
    async with _engine() as engine:
        async with _knowledge_base(engine) as knowledge_id:
            index = await _insert_index(engine, knowledge_id, 1, "DEPRECATED")
            conversation_id = uuid4()
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO conversation (
                            id, knowledge_id, rag_index_id, updated_at, last_activity_at
                        ) VALUES (
                            :id, :kb_id, :index_id,
                            now() - INTERVAL '30 days',
                            now() - INTERVAL '30 days'
                        )
                        """
                    ),
                    {"id": conversation_id, "kb_id": knowledge_id, "index_id": index},
                )
            settings = Settings(_env_file=None, app_env="test")
            repository = IngestionRepository(engine, settings)

            purged = await repository.purge_expired_conversations(7)

            assert str(conversation_id) in {str(item) for item in purged}
            remaining = await _fetch_one(
                engine,
                "SELECT count(*) AS total FROM conversation WHERE id = :id",
                id=conversation_id,
            )
            assert remaining["total"] == 0


async def test_release_frozen_conversation_references_unblocks_force_delete() -> None:
    async with _engine() as engine:
        async with _knowledge_base(engine) as knowledge_id:
            index = await _insert_index(engine, knowledge_id, 1, "DEPRECATED")
            document_id = await _insert_document(engine, knowledge_id, "force-delete-doc", "e" * 64)
            release_id = await _insert_release(engine, knowledge_id, index, status="CANCELLED")
            settings = Settings(_env_file=None, app_env="test")
            repository = IngestionRepository(engine, settings)

            rag_conversation = uuid4()
            focus_conversation = uuid4()
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO conversation (
                            id, knowledge_id, rag_index_id
                        ) VALUES (:id, :kb_id, :index_id)
                        """
                    ),
                    {"id": rag_conversation, "kb_id": knowledge_id, "index_id": index},
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO conversation (
                            id, knowledge_id, support_index_id, support_release_id,
                            focus_index_id, focus_document_id, chat_context,
                            pending_switch_options
                        ) VALUES (
                            :id, :kb_id, :index_id, :release_id, :index_id,
                            :document_id, '{"legacy": true}'::jsonb,
                            '[{"document_id": "stale"}]'::jsonb
                        )
                        """
                    ),
                    {
                        "id": focus_conversation,
                        "kb_id": knowledge_id,
                        "index_id": index,
                        "release_id": release_id,
                        "document_id": document_id,
                    },
                )

            blockers = await repository.get_index_deletion_blockers(index)
            assert blockers is not None
            assert blockers["frozen_conversation_count"] == 2
            assert await repository.mark_index_deleting(index) is None

            released = await repository.release_frozen_conversation_references(index)

            assert released == 3
            rag_row = await _fetch_one(
                engine,
                "SELECT rag_index_id FROM conversation WHERE id = :id",
                id=rag_conversation,
            )
            focus_row = await _fetch_one(
                engine,
                """
                SELECT support_index_id, support_release_id, focus_index_id,
                       focus_document_id, chat_context, pending_switch_options
                FROM conversation WHERE id = :id
                """,
                id=focus_conversation,
            )
            assert rag_row["rag_index_id"] is None
            assert focus_row["support_index_id"] is None
            assert focus_row["support_release_id"] is None
            assert focus_row["focus_index_id"] is None
            assert focus_row["focus_document_id"] is None
            assert focus_row["chat_context"] == {}
            assert focus_row["pending_switch_options"] == []
            after = await repository.get_index_deletion_blockers(index)
            assert after is not None
            assert after["frozen_conversation_count"] == 0
            assert await repository.mark_index_deleting(index) is not None
            assert await repository.delete_index(index) is True
