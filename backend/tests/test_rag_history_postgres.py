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
    repo = RagRepository(r.engine, Settings(_env_file=None, app_env="test"))
    first = await repo.begin_turn(
        knowledge_id=r.kb,
        conversation_id=None,
        question="如果还未更换呢？",
        request_id="history-first",
    )
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
