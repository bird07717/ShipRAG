from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_answer_reviews import records  # noqa: F401
from test_conversations_postgres_integration import _insert_active_index

from app.core.config import Settings
from app.rag.repository import RagRepository


async def _load_v2(fixture_records, **kwargs):
    repo = RagRepository(fixture_records.engine, Settings(_env_file=None, app_env="test"))
    async with fixture_records.engine.connect() as connection:
        return await repo._v2_history(connection, fixture_records.conv, **kwargs)


async def test_v2_history_uses_explicit_complete_pairs_and_ignores_support_focus(
    records,  # noqa: F811
) -> None:
    async with records.engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE message SET rag_user_message_id=:user_id, focus_epoch=41 "
                "WHERE id=:assistant_id"
            ),
            {"user_id": records.messages[0], "assistant_id": records.messages[1]},
        )
        await connection.execute(
            text("UPDATE message SET focus_epoch=99 WHERE id=:user_id"),
            {"user_id": records.messages[0]},
        )
        await connection.execute(
            text(
                "UPDATE rag_trace SET retrieval_result=CAST(:result AS jsonb) "
                "WHERE trace_id=:trace_id"
            ),
            {
                "trace_id": records.trace,
                "result": (
                    '{"doc_routing":{"generation":{"answer":{"used_document_ids":["'
                    + str(records.document)
                    + '"]}}}}'
                ),
            },
        )

    history, diagnostics = await _load_v2(records)

    assert history == [
        {"role": "USER", "content": "设备如何开机"},
        {"role": "ASSISTANT", "content": "按电源键"},
        {"role": "USER", "content": "下一步怎么操作"},
        {"role": "ASSISTANT", "content": "**《原文档》**"},
    ]
    assert diagnostics["strategy"] == "v2_complete_rounds"
    assert diagnostics["selected_message_ids"] == [str(item) for item in records.messages]
    assert diagnostics["previous_document_ids"] == [str(records.document)]
    assert all("message_id" not in item for item in history)


@pytest.mark.parametrize("input_guard", ["GREETING", "META_ACK"])
async def test_v2_history_excludes_completed_input_guard_shortcut(
    records,  # noqa: F811
    input_guard: str,
) -> None:
    async with records.engine.begin() as connection:
        await connection.execute(
            text("UPDATE message SET rag_user_message_id=:user_id WHERE id=:assistant_id"),
            {"user_id": records.messages[0], "assistant_id": records.messages[1]},
        )
        await connection.execute(
            text(
                "UPDATE message SET rag_history_context=CAST(:context AS jsonb) "
                "WHERE id=:assistant_id"
            ),
            {
                "assistant_id": records.messages[3],
                "context": json.dumps({"input_guard": input_guard}),
            },
        )
        await connection.execute(
            text(
                "UPDATE rag_trace SET retrieval_result='{}'::jsonb, "
                "runtime_snapshot='{}'::jsonb, citation_result=CAST(:result AS jsonb) "
                "WHERE trace_id=:trace_id"
            ),
            {
                "trace_id": records.trace,
                "result": json.dumps({"status": "NOT_REQUIRED", "shortcut": input_guard}),
            },
        )

    history, diagnostics = await _load_v2(records)

    assert history == [
        {"role": "USER", "content": "设备如何开机"},
        {"role": "ASSISTANT", "content": "按电源键"},
    ]
    assert diagnostics["previous_document_ids"] == []
    assert diagnostics["excluded"] == [
        {"message_id": str(records.messages[3]), "reason": "NON_TECHNICAL_SHORTCUT"}
    ]


async def test_v2_begin_turn_preserves_support_focus_state(records) -> None:  # noqa: F811
    index_id = await _insert_active_index(records.engine, records.kb)
    async with records.engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE conversation SET focus_document_id=:document_id, focus_epoch=17, "
                "chat_context=CAST(:context AS jsonb), support_context_version=8 "
                "WHERE id=:conversation_id"
            ),
            {
                "document_id": records.document,
                "conversation_id": records.conv,
                "context": '{"support_stage":"DOCUMENT_DELIVERY"}',
            },
        )

    repository = RagRepository(records.engine, Settings(_env_file=None, app_env="test"))
    turn = await repository.begin_turn(
        knowledge_id=records.kb,
        conversation_id=records.conv,
        question="现在换一个话题",
        request_id="v5-focus-isolation",
    )

    assert turn.index_id == index_id
    async with records.engine.connect() as connection:
        support_state = (
            (
                await connection.execute(
                    text(
                        "SELECT focus_document_id, focus_epoch, chat_context, "
                        "support_context_version FROM conversation WHERE id=:id"
                    ),
                    {"id": records.conv},
                )
            )
            .mappings()
            .one()
        )
        new_messages = (
            (
                await connection.execute(
                    text(
                        "SELECT role, focus_epoch FROM message "
                        "WHERE id IN (:user_id, :assistant_id) ORDER BY sequence_no"
                    ),
                    {
                        "user_id": turn.user_message_id,
                        "assistant_id": turn.assistant_message_id,
                    },
                )
            )
            .mappings()
            .all()
        )

    assert support_state["focus_document_id"] == records.document
    assert support_state["focus_epoch"] == 17
    assert support_state["chat_context"] == {"support_stage": "DOCUMENT_DELIVERY"}
    assert support_state["support_context_version"] == 8
    assert [(row["role"], row["focus_epoch"]) for row in new_messages] == [
        ("USER", 0),
        ("ASSISTANT", 0),
    ]


async def test_v2_previous_evidence_seed_is_index_and_knowledge_bound(
    records,  # noqa: F811
) -> None:
    index_id = await _insert_active_index(records.engine, records.kb)
    async with records.engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO index_document "
                "(id, index_id, document_id, source_hash, status) "
                "VALUES (:id, :index_id, :document_id, :source_hash, 'READY')"
            ),
            {
                "id": uuid4(),
                "index_id": index_id,
                "document_id": records.document,
                "source_hash": "0" * 64,
            },
        )

    repository = RagRepository(records.engine, Settings(_env_file=None, app_env="test"))
    seeds = await repository.get_document_retrieval_seeds(
        knowledge_id=records.kb,
        index_id=index_id,
        document_ids=(records.document, records.other_document),
    )

    assert [item["document_id"] for item in seeds] == [str(records.document)]
    assert seeds[0]["document"] == "原文档"
    assert seeds[0]["candidate_source"] == "PREVIOUS_EVIDENCE"


async def test_v2_progress_and_terminal_trace_keep_only_hashed_context(
    records,  # noqa: F811
) -> None:
    await _insert_active_index(records.engine, records.kb)
    repository = RagRepository(records.engine, Settings(_env_file=None, app_env="test"))
    turn = await repository.begin_turn(
        knowledge_id=records.kb,
        conversation_id=None,
        question="操作时使用哪个端口？",
        request_id=f"v5-progress-{uuid4()}",
    )
    generation = {
        "pipeline": "V2",
        "status": "ANSWER_PENDING",
        "query_interpreter": {"relation": "FOLLOW_UP", "active_task_sha256": "a" * 64},
        "retrieval": {"query_count": 3},
        "context": {"document_count": 1, "content_sha256": "b" * 64},
    }
    selected_context = [
        {
            "document_key": "D1",
            "document_id": str(records.document),
            "content_sha256": "c" * 64,
        }
    ]

    await repository.save_v2_progress(
        turn,
        generation=generation,
        selected_context=selected_context,
        timings={"v2_document_retrieval_ms": 12},
    )
    await repository.complete_turn(
        turn,
        answer="公开回答",
        sources=[],
        usage={"pipeline": "V2"},
        citation_result={"status": "NOT_PUBLIC"},
        timings={"total_ms": 20},
        doc_routing={"pipeline": "V2", "generation": {**generation, "status": "PASSED"}},
    )

    async with records.engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text(
                        "SELECT selected_context, runtime_snapshot, status "
                        "FROM rag_trace WHERE trace_id=:trace_id"
                    ),
                    {"trace_id": turn.trace_id},
                )
            )
            .mappings()
            .one()
        )

    assert row["status"] == "COMPLETED"
    assert row["selected_context"] == selected_context
    assert row["runtime_snapshot"]["generation"]["status"] == "PASSED"
    assert "操作时使用哪个端口" not in str(row)
