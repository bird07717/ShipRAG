from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings
from app.gateway.repository import GatewayRepository
from app.ingestion.repository import IngestionRepository
from app.rag.repository import RagRepository
from app.support.repository import SupportRepository


@asynccontextmanager
async def _engine():
    database_url = os.getenv("PHASE4_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PHASE4_TEST_DATABASE_URL is not configured")
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@asynccontextmanager
async def _knowledge_base(engine: AsyncEngine):
    knowledge_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO knowledge_base (id, name) VALUES (:id, :name)"),
            {"id": knowledge_id, "name": f"conversations-{knowledge_id}"},
        )
    try:
        yield knowledge_id
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM knowledge_base WHERE id = :id"), {"id": knowledge_id}
            )


async def _insert_active_index(engine: AsyncEngine, knowledge_id: UUID) -> UUID:
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
                    :id, :kb_id, 1, 'ACTIVE', :model_id,
                    'conversations-test-model', 1024, 'INITIAL'
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


async def _insert_message(
    engine: AsyncEngine,
    conversation_id: UUID,
    sequence: int,
    role: str,
    content: str,
    status: str,
) -> UUID:
    message_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO message (
                    id, conversation_id, sequence_no, role, content, status
                ) VALUES (:id, :conversation_id, :sequence, :role, :content, :status)
                """
            ),
            {
                "id": message_id,
                "conversation_id": conversation_id,
                "sequence": sequence,
                "role": role,
                "content": content,
                "status": status,
            },
        )
    return message_id


async def _fetch_one(engine: AsyncEngine, query: str, **params: Any) -> dict[str, Any]:
    async with engine.connect() as connection:
        row = (await connection.execute(text(query), params)).mappings().one_or_none()
    assert row is not None
    return dict(row)


@pytest.mark.asyncio
async def test_begin_turn_persists_conversation_user_and_title() -> None:
    async with _engine() as engine:
        async with _knowledge_base(engine) as knowledge_id:
            index_id = await _insert_active_index(engine, knowledge_id)
            settings = Settings(_env_file=None, app_env="test")
            rag_repository = RagRepository(engine, settings)
            support_repository = SupportRepository(engine, settings)

            rag_turn = await rag_repository.begin_turn(
                knowledge_id=knowledge_id,
                conversation_id=None,
                question="如何更换保险丝？",
                request_id="conversations-test-rag",
                user_id="platform-user-7",
            )
            support_turn = await support_repository.begin_turn(
                knowledge_id=knowledge_id,
                conversation_id=None,
                question="VAU 线缆如何检查？",
                request_id="conversations-test-support",
                user_id="platform-user-7",
                idempotency_key=None,
                request_hash="0" * 64,
            )

            rag_row = await _fetch_one(
                engine,
                "SELECT user_id, title, rag_index_id FROM conversation WHERE id = :id",
                id=rag_turn.conversation_id,
            )
            support_row = await _fetch_one(
                engine,
                "SELECT user_id, title, support_index_id FROM conversation WHERE id = :id",
                id=support_turn.conversation_id,
            )
            assert rag_row["user_id"] == "platform-user-7"
            assert rag_row["title"] == "如何更换保险丝？"
            assert rag_row["rag_index_id"] == index_id
            assert support_row["user_id"] == "platform-user-7"
            assert support_row["title"] == "VAU 线缆如何检查？"


@pytest.mark.asyncio
async def test_conversation_lifecycle_list_messages_delete() -> None:
    async with _engine() as engine:
        async with _knowledge_base(engine) as knowledge_id:
            index_id = await _insert_active_index(engine, knowledge_id)
            settings = Settings(_env_file=None, app_env="test")
            repository = GatewayRepository(engine, settings)
            ingestion = IngestionRepository(engine, settings)

            mine = uuid4()
            other = uuid4()
            anonymous = uuid4()
            async with engine.begin() as connection:
                for conversation_id, owner in (
                    (mine, "user-a"),
                    (other, "user-b"),
                    (anonymous, None),
                ):
                    await connection.execute(
                        text(
                            """
                            INSERT INTO conversation (
                                id, knowledge_id, user_id, title, rag_index_id
                            ) VALUES (
                                :id, :kb_id, :owner, :title, :index_id
                            )
                            """
                        ),
                        {
                            "id": conversation_id,
                            "kb_id": knowledge_id,
                            "owner": owner,
                            "title": f"会话-{owner or 'anon'}",
                            "index_id": index_id,
                        },
                    )
            await _insert_message(engine, mine, 1, "USER", "第一问", "COMPLETED")
            await _insert_message(engine, mine, 2, "ASSISTANT", "第一答", "COMPLETED")
            await _insert_message(engine, mine, 3, "ASSISTANT", "", "STREAMING")
            await _insert_message(engine, mine, 4, "ASSISTANT", "失败回复", "FAILED")

            scoped = await repository.list_conversations(knowledge_id, "user-a", 50)
            assert {row["id"] for row in scoped} == {mine}
            assert scoped[0]["message_count"] == 2
            everything = await repository.list_conversations(knowledge_id, None, 50)
            assert {row["id"] for row in everything} == {mine, other, anonymous}

            messages = await repository.list_conversation_messages(mine, 100)
            assert [item["status"] for item in messages] == [
                "COMPLETED",
                "COMPLETED",
                "FAILED",
            ]
            assert [item["role"] for item in messages] == [
                "USER",
                "ASSISTANT",
                "ASSISTANT",
            ]

            trace_id = uuid4()
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO rag_trace (
                            id, trace_id, request_id, kb_id, index_id,
                            conversation_id, question, mode, status, engine, stage
                        ) VALUES (
                            :id, :trace_id, 'conversations-test', :kb_id, :index_id,
                            :conversation_id, '第一问', 'CHAT', 'RUNNING', 'RAG', 'prepare'
                        )
                        """
                    ),
                    {
                        "id": uuid4(),
                        "trace_id": trace_id,
                        "kb_id": knowledge_id,
                        "index_id": index_id,
                        "conversation_id": mine,
                    },
                )

            blockers = await ingestion.get_index_deletion_blockers(index_id)
            assert blockers is not None
            assert blockers["frozen_conversation_count"] == 3

            assert await repository.delete_conversation(mine) is True

            remaining_messages = await _fetch_one(
                engine,
                "SELECT count(*) AS total FROM message WHERE conversation_id = :id",
                id=mine,
            )
            trace_row = await _fetch_one(
                engine,
                "SELECT conversation_id FROM rag_trace WHERE trace_id = :trace_id",
                trace_id=trace_id,
            )
            assert remaining_messages["total"] == 0
            assert trace_row["conversation_id"] is None
            after = await ingestion.get_index_deletion_blockers(index_id)
            assert after is not None
            assert after["frozen_conversation_count"] == 2
            assert await repository.delete_conversation(mine) is False
