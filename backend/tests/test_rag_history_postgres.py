from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from test_answer_reviews import records  # noqa: F401
from test_conversations_postgres_integration import _insert_active_index

from app.common.errors import ApiError
from app.core.config import Settings
from app.rag.repository import RagRepository


async def load(r, epoch=0, **kwargs):
    repo = RagRepository(r.engine, Settings(_env_file=None))
    async with r.engine.connect() as conn:
        return await repo._focused_history(conn, r.conv, epoch, **kwargs)


async def load_v2(r, **kwargs):
    repo = RagRepository(
        r.engine,
        Settings(_env_file=None, app_env="test", m3_rag_pipeline_mode="v2"),
    )
    async with r.engine.connect() as conn:
        return await repo._v2_history(conn, r.conv, **kwargs)


async def test_explicit_pair_and_legacy_assistant_link_survive_public_snapshot_changes(
    records,  # noqa: F811
):
    r = records
    async with r.engine.begin() as conn:
        await conn.execute(
            text("UPDATE message SET focus_epoch=0 WHERE conversation_id=:id"), {"id": r.conv}
        )
        await conn.execute(
            text("UPDATE message SET rag_user_message_id=:u WHERE id=:a"),
            {"u": r.messages[0], "a": r.messages[1]},
        )
    history, _ = await load(r)
    assert [m["message_id"] for m in history] == list(map(str, r.messages))
    async with r.engine.begin() as conn:
        await conn.execute(
            text("UPDATE message SET response_payload='{}'::jsonb WHERE conversation_id=:id"),
            {"id": r.conv},
        )
        await conn.execute(
            text("UPDATE rag_trace SET runtime_snapshot='{}'::jsonb WHERE trace_id=:id"),
            {"id": r.trace},
        )
    assert (await load(r))[0] == history


async def test_v2_history_uses_explicit_complete_pairs_and_ignores_focus_epochs(
    records,  # noqa: F811
):
    async with records.engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE message SET rag_user_message_id=:user_id, focus_epoch=41 "
                "WHERE id=:assistant_id"
            ),
            {"user_id": records.messages[0], "assistant_id": records.messages[1]},
        )
        await conn.execute(
            text("UPDATE message SET focus_epoch=99 WHERE id=:user_id"),
            {"user_id": records.messages[0]},
        )

    history, diagnostics = await load_v2(records)

    assert history == [
        {"role": "USER", "content": "设备如何开机"},
        {"role": "ASSISTANT", "content": "按电源键"},
        {"role": "USER", "content": "下一步怎么操作"},
        {"role": "ASSISTANT", "content": "**《原文档》**"},
    ]
    assert diagnostics["strategy"] == "v2_complete_rounds"
    assert diagnostics["selected_message_ids"] == [str(item) for item in records.messages]
    assert all("message_id" not in item for item in history)


async def test_v2_begin_turn_does_not_load_or_write_legacy_focus_context(
    records,  # noqa: F811
):
    index_id = await _insert_active_index(records.engine, records.kb)
    async with records.engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE conversation SET focus_document_id=:document_id, focus_epoch=17, "
                "chat_context=CAST(:context AS jsonb), support_context_version=8 "
                "WHERE id=:conversation_id"
            ),
            {
                "document_id": records.document,
                "conversation_id": records.conv,
                "context": '{"rag_issue":{"origin_query":"旧问题"},"pending_options":[1]}',
            },
        )

    repo = RagRepository(
        records.engine,
        Settings(_env_file=None, app_env="test", m3_rag_pipeline_mode="v2"),
    )
    turn = await repo.begin_turn(
        knowledge_id=records.kb,
        conversation_id=records.conv,
        question="现在换一个话题",
        request_id="v2-focus-isolation",
    )

    assert turn.index_id == index_id
    assert turn.focus_document_id is None
    assert turn.focus_epoch == 0
    assert turn.context_version == 0
    assert turn.chat_context == {}
    assert turn.history == [
        {"role": "USER", "content": "下一步怎么操作"},
        {"role": "ASSISTANT", "content": "**《原文档》**"},
    ]
    assert turn.history_diagnostics["strategy"] == "v2_complete_rounds"

    async with records.engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT role, focus_epoch FROM message "
                    "WHERE id IN (:user_id, :assistant_id) ORDER BY sequence_no"
                ),
                {
                    "user_id": turn.user_message_id,
                    "assistant_id": turn.assistant_message_id,
                },
            )
        ).all()
        stored_context = (
            await conn.execute(
                text(
                    "SELECT focus_epoch, chat_context, support_context_version "
                    "FROM conversation WHERE id=:conversation_id"
                ),
                {"conversation_id": records.conv},
            )
        ).one()

    assert rows == [("USER", 0), ("ASSISTANT", 0)]
    assert stored_context.focus_epoch == 17
    assert stored_context.chat_context["rag_issue"]["origin_query"] == "旧问题"
    assert stored_context.support_context_version == 8


@pytest.mark.parametrize("status", ["FAILED", "CANCELLED", "RUNNING"])
async def test_v2_history_respects_external_request_terminal_state(
    records,  # noqa: F811
    status,
):
    async with records.engine.begin() as conn:
        await conn.execute(
            text("UPDATE assistant_request SET status=:status WHERE id=:request_id"),
            {"status": status, "request_id": records.request},
        )

    history, diagnostics = await load_v2(records)

    assert history == []
    assert diagnostics["excluded"] == [
        {"message_id": str(records.messages[3]), "reason": "INCOMPLETE_ROUND"},
        {"message_id": str(records.messages[1]), "reason": "UNLINKED_LEGACY"},
    ]


async def test_unlinked_internal_history_is_not_guessed_from_adjacency(
    records,  # noqa: F811
):
    async with records.engine.begin() as conn:
        await conn.execute(
            text("UPDATE message SET focus_epoch=0 WHERE conversation_id=:id"), {"id": records.conv}
        )
    history, diag = await load(records)
    assert [m["message_id"] for m in history] == list(map(str, records.messages[2:]))
    assert any(e["reason"] == "UNLINKED_LEGACY" for e in diag["excluded"])


async def test_unknown_legacy_epoch_is_not_assigned_to_current_topic(
    records,  # noqa: F811
):
    assert (await load(records))[0] == []


async def test_new_rag_turn_persists_pair_and_does_not_repeat_current_question(
    records,  # noqa: F811
):
    r = records
    await _insert_active_index(r.engine, r.kb)
    async with r.engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE knowledge_base SET name='VAU 测试库', "
                "description='VAU 图像配置、通信和故障排查' WHERE id=:id"
            ),
            {"id": r.kb},
        )
    repo = RagRepository(
        r.engine,
        Settings(_env_file=None, app_env="test", m3_rag_scope_max_tokens=100),
    )
    first = await repo.begin_turn(
        knowledge_id=r.kb,
        conversation_id=None,
        question="如果还未更换呢？",
        request_id="history-first",
    )
    assert first.knowledge_name == "VAU 测试库"
    assert "VAU 图像配置、通信和故障排查" in first.scope_description
    assert first.scope_tokens <= 100
    async with r.engine.connect() as conn:
        scope_snapshot = (
            await conn.execute(
                text("SELECT runtime_snapshot->'scope' FROM rag_trace WHERE trace_id=:id"),
                {"id": first.trace_id},
            )
        ).scalar_one()
    assert scope_snapshot["sha256"] == first.scope_sha256
    assert scope_snapshot["tokens"] == first.scope_tokens
    async with r.engine.begin() as conn:
        pair = (
            await conn.execute(
                text("SELECT rag_user_message_id FROM message WHERE id=:id"),
                {"id": first.assistant_message_id},
            )
        ).scalar_one()
        assert pair == first.user_message_id
        await conn.execute(
            text("UPDATE message SET status='COMPLETED',content='还不能确认' WHERE id=:id"),
            {"id": first.assistant_message_id},
        )
    second = await repo.begin_turn(
        knowledge_id=r.kb,
        conversation_id=first.conversation_id,
        question="我只是做假设",
        request_id="history-second",
    )
    assert [m["message_id"] for m in second.history] == [
        str(first.user_message_id),
        str(first.assistant_message_id),
    ]
    assert second.has_prior_messages is True
    assert all("我只是做假设" not in h["content"] for h in second.history)


async def test_v4_prepared_trace_omits_retired_planner_snapshots(
    records,  # noqa: F811
) -> None:
    await _insert_active_index(records.engine, records.kb)
    repo = RagRepository(records.engine, Settings(_env_file=None, app_env="test"))
    turn = await repo.begin_turn(
        knowledge_id=records.kb,
        conversation_id=None,
        question="如何配置 VAU 图像",
        request_id="v4-trace",
    )
    await repo.save_prepared(
        turn,
        embedding_provider="fake",
        embedding_model="fake",
        vector_candidates=[],
        bm25_candidates=[],
        fusion_candidates=[],
        rerank_candidates=[],
        rerank_result={"status": "SKIPPED"},
        selected_context=[],
        prompt="V2 不保留全文 Prompt",
        timings={"prepare_turn_ms": 10},
    )
    await repo.complete_turn(
        turn,
        answer="公开回答",
        sources=[],
        usage={},
        citation_result={},
        timings={"prepare_turn_ms": 10, "total_ms": 20},
        doc_routing={"pipeline": "V2", "generation": {"status": "PASSED"}},
    )

    async with records.engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text(
                        """
                    SELECT runtime_snapshot, retrieval_result, status, latency
                    FROM rag_trace WHERE trace_id=:trace_id
                    """
                    ),
                    {"trace_id": turn.trace_id},
                )
            )
            .mappings()
            .one()
        )

    assert "semantic_orchestration_config" not in row["runtime_snapshot"]
    assert "semantic_orchestration" not in row["runtime_snapshot"]
    assert "response_plan" not in row["runtime_snapshot"]
    assert row["runtime_snapshot"]["generation"] == {"status": "PASSED"}
    assert row["retrieval_result"]["mode"] == "HYBRID"
    assert row["status"] == "COMPLETED"
    assert row["latency"]["total_ms"] == 20


async def test_rag_issue_is_loaded_only_when_its_message_pair_is_valid(
    records,  # noqa: F811
) -> None:
    r = records
    await _insert_active_index(r.engine, r.kb)
    repo = RagRepository(r.engine, Settings(_env_file=None, app_env="test"))
    first = await repo.begin_turn(
        knowledge_id=r.kb,
        conversation_id=None,
        question="VAU 没有图像",
        request_id="issue-first",
    )
    issue = {
        "version": 1,
        "origin_query": "VAU 没有图像",
        "origin_user_message_id": str(first.user_message_id),
        "answer_style": "TROUBLESHOOT",
        "pending_condition": {
            "question": "请确认具体型号？",
            "source_user_message_id": str(first.user_message_id),
            "source_assistant_message_id": str(first.assistant_message_id),
        },
    }
    await repo.complete_turn(
        first,
        answer="请确认具体型号？",
        sources=[],
        usage={},
        citation_result={},
        timings={},
        context_patch={"rag_issue": issue},
        pending_options_after=[],
    )
    second = await repo.begin_turn(
        knowledge_id=r.kb,
        conversation_id=first.conversation_id,
        question="PDC",
        request_id="issue-second",
    )
    assert second.chat_context["rag_issue"] == issue

    continued_issue = {
        **issue,
        "origin_query": "VAU 没有图像\n用户补充条件：PDC",
        "pending_condition": {
            "question": "请再确认软件版本？",
            "source_user_message_id": str(second.user_message_id),
            "source_assistant_message_id": str(second.assistant_message_id),
        },
    }
    await repo.complete_turn(
        second,
        answer="请再确认软件版本？",
        sources=[],
        usage={},
        citation_result={},
        timings={},
        context_patch={"rag_issue": continued_issue},
        pending_options_after=[],
    )
    third = await repo.begin_turn(
        knowledge_id=r.kb,
        conversation_id=first.conversation_id,
        question="V2.1",
        request_id="issue-third",
    )
    assert third.chat_context["rag_issue"] == continued_issue

    async with r.engine.begin() as conn:
        await conn.execute(
            text("UPDATE message SET rag_user_message_id=NULL WHERE id=:id"),
            {"id": second.assistant_message_id},
        )
    fourth = await repo.begin_turn(
        knowledge_id=r.kb,
        conversation_id=first.conversation_id,
        question="PDC",
        request_id="issue-invalid-source",
    )
    assert "rag_issue" not in fourth.chat_context


@pytest.mark.parametrize("status", ["FAILED", "CANCELLED", "RUNNING"])
async def test_public_request_state_prevents_failed_or_inflight_round_reuse(
    records,  # noqa: F811
    status,
):
    async with records.engine.begin() as conn:
        await conn.execute(
            text("UPDATE assistant_request SET status=:status WHERE id=:id"),
            {"status": status, "id": records.request},
        )
    assert (await load(records))[0] == []


async def test_focus_transition_keeps_confirmation_bridge_and_excludes_old_epoch(
    records,  # noqa: F811
):
    r = records
    index_id = await _insert_active_index(r.engine, r.kb)
    repo = RagRepository(r.engine, Settings(_env_file=None))
    epoch = await repo.set_conversation_focus(
        r.conv,
        r.document,
        index_id,
        expected_context_version=0,
        user_message_id=r.messages[2],
        assistant_message_id=r.messages[3],
        origin_query="如果更换B后仍失败怎么办？",
    )
    history, _ = await load(r, epoch)
    assert len(history) == 2 and "如果更换B后仍失败" in history[0]["content"]
    with pytest.raises(Exception, match="会话状态"):
        await repo.set_conversation_focus(r.conv, r.document, index_id, expected_context_version=0)
    assert (await load(r, epoch))[0] == history


async def test_fallback_current_pair_is_excluded_and_foreign_pair_never_loaded(
    records,  # noqa: F811
):
    r = records
    assert (await load(r, excluded_message_id=r.messages[3]))[0] == []
    async with r.engine.begin() as conn:
        # A wrong association to an assistant role must not become user context.
        await conn.execute(
            text("UPDATE message SET rag_user_message_id=:u WHERE id=:a"),
            {"u": r.messages[1], "a": r.messages[3]},
        )
    assert (await load(r))[0] == []


async def test_deleted_or_unindexed_focus_cannot_supply_history(
    records,  # noqa: F811
):
    history, diag = await load(records, focus_document_id=records.document, index_id=None)
    assert history == [] and all(e["reason"] == "FOCUS_UNAVAILABLE" for e in diag["excluded"])


async def test_focus_update_rejects_incomplete_or_foreign_message_pair(records):  # noqa: F811
    index_id = await _insert_active_index(records.engine, records.kb)
    repo = RagRepository(records.engine, Settings(_env_file=None))
    with pytest.raises(ApiError, match="消息关联"):
        await repo.set_conversation_focus(
            records.conv,
            records.document,
            index_id,
            expected_context_version=0,
            user_message_id=records.messages[0],
            assistant_message_id=uuid4(),
        )
    async with records.engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT focus_document_id, support_context_version "
                    "FROM conversation WHERE id=:id"
                ),
                {"id": records.conv},
            )
        ).one()
    assert row.focus_document_id is None and row.support_context_version == 0
