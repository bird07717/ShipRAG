from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.common.errors import ApiError
from app.core.config import Settings
from app.releases.repository import ReleaseRepository
from app.support.repository import SupportRepository

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def _repository() -> AsyncIterator[tuple[SupportRepository, UUID]]:
    database_url = os.getenv("SUPPORT_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SUPPORT_TEST_DATABASE_URL is not configured")
    engine = create_async_engine(database_url, pool_pre_ping=True)
    knowledge_id, index_id, document_id, index_document_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    async with engine.begin() as connection:
        model_id = (
            await connection.execute(
                text("SELECT id FROM model_config WHERE model_type = 'EMBEDDING' LIMIT 1")
            )
        ).scalar_one()
        await connection.execute(
            text("INSERT INTO knowledge_base (id, name) VALUES (:id, :name)"),
            {"id": knowledge_id, "name": f"support-integration-{knowledge_id}"},
        )
        await connection.execute(
            text("""
                INSERT INTO knowledge_index (
                    id, kb_id, version, status, embedding_model_id,
                    embedding_model_name, embedding_dimension, build_reason
                ) VALUES (
                    :id, :kb_id, 1, 'ACTIVE', :model_id,
                    'integration-model', 1024, 'INITIAL'
                )
            """),
            {"id": index_id, "kb_id": knowledge_id, "model_id": model_id},
        )
        await connection.execute(
            text("UPDATE knowledge_base SET active_index_id = :index_id WHERE id = :kb_id"),
            {"index_id": index_id, "kb_id": knowledge_id},
        )
        await connection.execute(
            text("""
                INSERT INTO document_source (
                    id, kb_id, filename, display_name, minio_bucket,
                    minio_object_key, file_hash, file_size, mime_type
                ) VALUES (
                    :id, :kb_id, 'test.docx', '并发测试文档', 'test',
                    :object_key, :file_hash, 1,
                    'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                )
            """),
            {
                "id": document_id,
                "kb_id": knowledge_id,
                "object_key": f"integration/{document_id}",
                "file_hash": "a" * 64,
            },
        )
        await connection.execute(
            text("""
                INSERT INTO index_document (
                    id, index_id, document_id, source_hash, status
                ) VALUES (:id, :index_id, :document_id, :source_hash, 'READY')
            """),
            {
                "id": index_document_id,
                "index_id": index_id,
                "document_id": document_id,
                "source_hash": "a" * 64,
            },
        )
        await connection.execute(
            text("""
                INSERT INTO document_element (
                    id, index_id, index_document_id, document_id,
                    element_type, sequence_no, content
                ) VALUES (
                    :id, :index_id, :index_document_id, :document_id,
                    'TEXT', 1, '集成测试内容'
                )
            """),
            {
                "id": uuid4(),
                "index_id": index_id,
                "index_document_id": index_document_id,
                "document_id": document_id,
            },
        )
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=SecretStr(database_url),
    )
    try:
        yield SupportRepository(engine, settings), knowledge_id
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE knowledge_base SET active_index_id = NULL WHERE id = :id"),
                {"id": knowledge_id},
            )
            await connection.execute(
                text("DELETE FROM knowledge_base WHERE id = :id"), {"id": knowledge_id}
            )
        await engine.dispose()


async def test_postgres_failed_idempotency_can_retry() -> None:
    async with _repository() as (repository, knowledge_id):
        kwargs = {
            "knowledge_id": knowledge_id,
            "conversation_id": None,
            "question": "测试幂等",
            "request_id": "integration-idempotency",
            "user_id": None,
            "idempotency_key": f"idem-{uuid4()}",
            "request_hash": "b" * 64,
        }
        first = await repository.begin_turn(**kwargs)
        with pytest.raises(ApiError) as running:
            await repository.begin_turn(**kwargs)
        assert running.value.code == "REQUEST_IN_PROGRESS"

        await repository.fail_turn(first, code="TEST_FAILURE", message="测试失败")
        retried = await repository.begin_turn(**kwargs)

        assert retried.trace_id != first.trace_id
        assert retried.idempotency_key == first.idempotency_key
        await repository.fail_turn(retried, code="TEST_CLEANUP", message="测试清理")


async def test_profile_generation_lists_all_documents_without_a_document_id() -> None:
    """A NULL optional UUID must be explicitly typed for asyncpg."""
    async with _repository() as (repository, knowledge_id):
        async with repository.engine.connect() as connection:
            index_id = (
                await connection.execute(
                    text("SELECT active_index_id FROM knowledge_base WHERE id = :id"),
                    {"id": knowledge_id},
                )
            ).scalar_one()

        documents = await ReleaseRepository(repository.engine).generation_documents(index_id)

        assert len(documents) == 1
        assert documents[0]["display_name"] == "并发测试文档"
        assert documents[0]["content"] == "集成测试内容"


async def test_postgres_workflow_optimistic_lock_rejects_stale_turn() -> None:
    async with _repository() as (repository, knowledge_id):
        first = await repository.begin_turn(
            knowledge_id=knowledge_id,
            conversation_id=None,
            question="第一条并发消息",
            request_id="integration-concurrency-1",
            user_id=None,
            idempotency_key=None,
            request_hash="c" * 64,
        )
        stale = await repository.begin_turn(
            knowledge_id=knowledge_id,
            conversation_id=first.conversation_id,
            question="第二条并发消息",
            request_id="integration-concurrency-2",
            user_id=None,
            idempotency_key=None,
            request_hash="d" * 64,
        )
        response = {
            "answer": "完成",
            "references": [],
            "content": [],
            "response_type": "NO_MATCH",
            "latency_ms": 1,
        }
        await repository.finish_turn(
            first,
            response=response,
            context={"support_version": 1},
            focus_document_id=None,
            trace={},
        )
        with pytest.raises(ApiError) as conflict:
            await repository.finish_turn(
                stale,
                response=response,
                context={"support_version": 1},
                focus_document_id=None,
                trace={},
            )
        assert conflict.value.code == "WORKFLOW_CONFLICT"


async def test_postgres_persists_support_clarification_response_type() -> None:
    """The longest valid Support response type must commit to rag_trace."""
    async with _repository() as (repository, knowledge_id):
        turn = await repository.begin_turn(
            knowledge_id=knowledge_id,
            conversation_id=None,
            question="设备出错了",
            request_id="integration-clarification-response-type",
            user_id=None,
            idempotency_key=None,
            request_hash="e" * 64,
        )
        response = {
            "answer": "请确认要处理的文档。",
            "references": [],
            "content": [],
            "response_type": "CLARIFICATION_REQUIRED",
            "latency_ms": 1,
        }

        await repository.finish_turn(
            turn,
            response=response,
            context={"support_version": 1, "pending_options": []},
            focus_document_id=None,
            trace={},
        )

        async with repository.engine.connect() as connection:
            response_type = (
                await connection.execute(
                    text("SELECT response_type FROM rag_trace WHERE trace_id = :trace_id"),
                    {"trace_id": turn.trace_id},
                )
            ).scalar_one()
        assert response_type == "CLARIFICATION_REQUIRED"
