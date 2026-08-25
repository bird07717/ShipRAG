from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.rag.repository import RagRepository


@pytest.mark.asyncio
async def test_rag_fallback_reuses_support_message_pair() -> None:
    database_url = os.getenv("SUPPORT_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SUPPORT_TEST_DATABASE_URL is not configured")

    engine = create_async_engine(database_url, pool_pre_ping=True)
    knowledge_id = uuid4()
    index_id = uuid4()
    conversation_id = uuid4()
    user_message_id = uuid4()
    assistant_message_id = uuid4()
    support_trace_id = uuid4()
    try:
        async with engine.begin() as connection:
            model_id = (
                await connection.execute(
                    text("SELECT id FROM model_config WHERE model_type = 'EMBEDDING' LIMIT 1")
                )
            ).scalar_one()
            await connection.execute(
                text("INSERT INTO knowledge_base (id, name) VALUES (:id, :name)"),
                {"id": knowledge_id, "name": f"fallback-reuse-{knowledge_id}"},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO knowledge_index (
                        id, kb_id, version, status, embedding_model_id,
                        embedding_model_name, embedding_dimension, build_reason
                    ) VALUES (
                        :id, :kb_id, 1, 'ACTIVE', :model_id,
                        'deterministic-shake256', 1024, 'INITIAL'
                    )
                    """
                ),
                {"id": index_id, "kb_id": knowledge_id, "model_id": model_id},
            )
            await connection.execute(
                text("UPDATE knowledge_base SET active_index_id = :index_id WHERE id = :kb_id"),
                {"index_id": index_id, "kb_id": knowledge_id},
            )
            await connection.execute(
                text("INSERT INTO conversation (id, knowledge_id) VALUES (:id, :kb_id)"),
                {"id": conversation_id, "kb_id": knowledge_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO message (
                        id, conversation_id, sequence_no, role, content, status, response_type
                    ) VALUES
                        (:user_id, :conversation_id, 1, 'USER', :question, 'COMPLETED', NULL),
                        (:assistant_id, :conversation_id, 2, 'ASSISTANT',
                         'support no match', 'COMPLETED', 'NO_MATCH')
                    """
                ),
                {
                    "user_id": user_message_id,
                    "assistant_id": assistant_message_id,
                    "conversation_id": conversation_id,
                    "question": "无法匹配的问题",
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_trace (
                        id, trace_id, request_id, kb_id, index_id, conversation_id,
                        message_id, question, mode, status, engine, stage,
                        response_type, answer
                    ) VALUES (
                        :id, :trace_id, 'req-support-no-match', :kb_id, :index_id,
                        :conversation_id, :message_id, :question, 'CHAT', 'COMPLETED',
                        'SUPPORT', 'COMPLETED', 'NO_MATCH', 'support no match'
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "trace_id": support_trace_id,
                    "kb_id": knowledge_id,
                    "index_id": index_id,
                    "conversation_id": conversation_id,
                    "message_id": assistant_message_id,
                    "question": "无法匹配的问题",
                },
            )

        repository = RagRepository(engine, Settings(_env_file=None, app_env="test"))
        turn = await repository.begin_turn(
            knowledge_id=knowledge_id,
            conversation_id=conversation_id,
            question="无法匹配的问题",
            request_id="req-fallback-reuse",
            reuse_user_message_id=user_message_id,
            reuse_assistant_message_id=assistant_message_id,
            parent_trace_id=support_trace_id,
            fallback_reason="NO_MATCH",
        )

        assert turn.user_message_id == user_message_id
        assert turn.assistant_message_id == assistant_message_id
        assert turn.history == []
        async with engine.connect() as connection:
            count = (
                await connection.execute(
                    text("SELECT count(*) FROM message WHERE conversation_id = :id"),
                    {"id": conversation_id},
                )
            ).scalar_one()
            assistant_status = (
                await connection.execute(
                    text("SELECT status FROM message WHERE id = :id"),
                    {"id": assistant_message_id},
                )
            ).scalar_one()
            support_message_id = (
                await connection.execute(
                    text("SELECT message_id FROM rag_trace WHERE trace_id = :id"),
                    {"id": support_trace_id},
                )
            ).scalar_one()
            rag_link = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT parent_trace_id, fallback_from, fallback_reason
                        FROM rag_trace WHERE trace_id = :id
                        """
                        ),
                        {"id": turn.trace_id},
                    )
                )
                .mappings()
                .one()
            )
        assert count == 2
        assert assistant_status == "STREAMING"
        assert support_message_id is None
        assert rag_link["parent_trace_id"] == support_trace_id
        assert rag_link["fallback_from"] == "SUPPORT"
        assert rag_link["fallback_reason"] == "NO_MATCH"
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
