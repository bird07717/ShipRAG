from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.gateway.repository import GatewayRepository
from app.rag.repository import RagRepository


@pytest.mark.asyncio
async def test_assistant_replay_count_is_persisted_atomically() -> None:
    database_url = os.getenv("SUPPORT_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SUPPORT_TEST_DATABASE_URL is not configured")

    engine = create_async_engine(database_url, pool_pre_ping=True)
    knowledge_id = uuid4()
    request_id = uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO knowledge_base (id, name, assistant_online) "
                    "VALUES (:id, :name, true)"
                ),
                {"id": knowledge_id, "name": f"assistant-replay-{knowledge_id}"},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO assistant_request (
                        id, external_user_id, knowledge_id, idempotency_key,
                        request_hash, status
                    ) VALUES (
                        :id, 'business-user-1', :knowledge_id, 'replay-key-1',
                        'request-hash-1', 'COMPLETED'
                    )
                    """
                ),
                {"id": request_id, "knowledge_id": knowledge_id},
            )

        repository = GatewayRepository(engine, Settings(_env_file=None, app_env="test"))
        state, replayed = await repository.reserve_assistant_request(
            external_user_id="business-user-1",
            knowledge_id=knowledge_id,
            idempotency_key="replay-key-1",
            request_hash="request-hash-1",
            conversation_id=None,
        )

        async with engine.connect() as connection:
            persisted = (
                (
                    await connection.execute(
                        text(
                            "SELECT replay_count, last_replayed_at "
                            "FROM assistant_request WHERE id = :id"
                        ),
                        {"id": request_id},
                    )
                )
                .mappings()
                .one()
            )
        assert state == "REPLAY"
        assert replayed["replay_count"] == 1
        assert persisted["replay_count"] == 1
        assert persisted["last_replayed_at"] is not None
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM knowledge_base WHERE id = :id"),
                {"id": knowledge_id},
            )
        await engine.dispose()


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
        # A regular RAG / Playground turn has no parent Trace.  Keep this in
        # the asyncpg-backed integration test: an untyped NULL used both as a
        # UUID value and in an `IS NULL` expression otherwise fails at prepare
        # time before retrieval or the LLM is reached.
        standalone_turn = await repository.begin_turn(
            knowledge_id=knowledge_id,
            conversation_id=None,
            question="普通 RAG 请求",
            request_id="req-standalone-rag",
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
            standalone_link = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT parent_trace_id, fallback_from, fallback_reason
                        FROM rag_trace WHERE trace_id = :id
                        """
                        ),
                        {"id": standalone_turn.trace_id},
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
        assert standalone_link["parent_trace_id"] is None
        assert standalone_link["fallback_from"] is None
        assert standalone_link["fallback_reason"] is None
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


@pytest.mark.asyncio
async def test_assistant_turn_context_reports_unknown_for_unresolvable_explicit_link() -> None:
    """An explicit ``rag_user_message_id`` that fails to validate must resolve
    to "unknown" (``None``), never to a guessed different message.

    Guessing here would be exactly the wrong-association bug the field was
    added to eliminate: a corrupted or self-referencing link is a data
    problem to surface, not to paper over with an unrelated message. A
    legacy turn that never recorded a link at all still gets the
    nearest-prior-USER fallback.
    """
    database_url = os.getenv("SUPPORT_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SUPPORT_TEST_DATABASE_URL is not configured")

    engine = create_async_engine(database_url, pool_pre_ping=True)
    knowledge_id = uuid4()
    conversation_id = uuid4()
    trace_id = uuid4()
    user_message_id, other_assistant_message_id, assistant_message_id = (
        uuid4(),
        uuid4(),
        uuid4(),
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO knowledge_base (id, name) VALUES (:id, :name)"),
                {"id": knowledge_id, "name": f"turn-context-{knowledge_id}"},
            )
            await connection.execute(
                text("INSERT INTO conversation (id, knowledge_id) VALUES (:id, :knowledge_id)"),
                {"id": conversation_id, "knowledge_id": knowledge_id},
            )
            for seq, (message_id, role) in enumerate(
                [
                    (user_message_id, "USER"),
                    (other_assistant_message_id, "ASSISTANT"),
                    (assistant_message_id, "ASSISTANT"),
                ],
                1,
            ):
                await connection.execute(
                    text(
                        "INSERT INTO message (id, conversation_id, sequence_no, role, "
                        "content, status) VALUES (:id, :conv, :seq, :role, 'x', 'COMPLETED')"
                    ),
                    {"id": message_id, "conv": conversation_id, "seq": seq, "role": role},
                )
            # Corrupted link: points at an ASSISTANT message, not the real
            # USER turn. A real preceding USER message still exists, which
            # is exactly what a silent fallback would wrongly return.
            await connection.execute(
                text("UPDATE message SET rag_user_message_id = :bad WHERE id = :id"),
                {"bad": other_assistant_message_id, "id": assistant_message_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO rag_trace (id, trace_id, request_id, kb_id, "
                    "conversation_id, message_id, question, status, engine) "
                    "VALUES (:id, :trace_id, 'turn-context-test', :kb_id, :conversation_id, "
                    ":message_id, 'q', 'COMPLETED', 'RAG')"
                ),
                {
                    "id": uuid4(),
                    "trace_id": trace_id,
                    "kb_id": knowledge_id,
                    "conversation_id": conversation_id,
                    "message_id": assistant_message_id,
                },
            )

        repository = GatewayRepository(engine, Settings(_env_file=None, app_env="test"))
        context = await repository.assistant_turn_context(
            conversation_id=conversation_id, trace_id=trace_id
        )
        assert context["user_message_id"] is None

        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE message SET rag_user_message_id = NULL WHERE id = :id"),
                {"id": assistant_message_id},
            )

        legacy_context = await repository.assistant_turn_context(
            conversation_id=conversation_id, trace_id=trace_id
        )
        assert legacy_context["user_message_id"] == user_message_id
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM knowledge_base WHERE id = :id"), {"id": knowledge_id}
            )
        await engine.dispose()
