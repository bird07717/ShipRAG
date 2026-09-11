from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from test_answer_reviews import records  # noqa: F401 -- reuse isolated PostgreSQL fixture
from test_assistant_content_consistency import document_payload

from app.api.assistant import _message, messages
from app.api.schemas import AssistantMessageResponse
from app.assistant.service import public_complete
from app.core.config import Settings
from app.gateway.repository import GatewayRepository


@pytest.mark.asyncio
@pytest.mark.parametrize("include_images", [False, True])
@pytest.mark.parametrize("long_document", [False, True])
async def test_saved_public_reply_is_authoritative_for_history_and_replay(
    records,  # noqa: F811 -- pytest injects the imported fixture
    include_images,
    long_document,
):
    original = document_payload()
    if long_document:
        original["content"][0]["text"] = "长步骤正文" * 20000
        original["content"].append({"type": "text", "text": "最后步骤必须保留。"})
    response = public_complete(
        {
            **original,
            "conversation_id": records.conv,
            "message_id": records.messages[-1],
            "trace_id": records.trace,
            "content": [
                item for item in original["content"] if include_images or item["type"] != "image"
            ],
        },
        "consistency-postgres",
    )
    repo = GatewayRepository(records.engine, Settings(_env_file=None))
    # Model the internal engine's raw blocks already saved on the message.
    async with records.engine.begin() as conn:
        await conn.execute(
            text("UPDATE message SET content_blocks=CAST(:blocks AS jsonb) WHERE id=:id"),
            {"id": records.messages[-1], "blocks": json.dumps(original["content"])},
        )
        await conn.execute(
            text("UPDATE assistant_request SET status='RUNNING' WHERE id=:id"),
            {"id": records.request},
        )
    await repo.complete_assistant_request(
        request_id=records.request,
        response=response,
        message_id=records.messages[-1],
        conversation_id=records.conv,
        trace_id=records.trace,
    )
    rows = await repo.list_assistant_messages(conversation_id=records.conv, limit=10)
    row = next(row for row in rows if row["id"] == records.messages[-1])
    history = AssistantMessageResponse.model_validate(_message(row)).model_dump()
    async with records.engine.connect() as conn:
        saved = (
            (
                await conn.execute(
                    text("SELECT status, terminal_response FROM assistant_request WHERE id=:id"),
                    {"id": records.request},
                )
            )
            .mappings()
            .one()
        )
    assert saved["status"] == "COMPLETED"
    assert saved["terminal_response"] == row["response_payload"] == response
    assert history["content_blocks"] == response["content"]
    assert history["content"] == response["answer"]
    assert any(block["type"] == "image" for block in history["content_blocks"]) is include_images
    assert "download_url" not in json.dumps(history["content_blocks"])
    assert '"url"' not in json.dumps(history["content_blocks"])
    # Raw internal data is not rewritten as a side effect of reading history.
    assert row["content_blocks"] == original["content"]


@pytest.mark.parametrize("limit", [1, 2, 3, 4, 5])
async def test_real_message_pagination_has_no_gaps(
    records,  # noqa: F811 -- pytest injects the imported fixture
    limit,
):
    repo = GatewayRepository(records.engine, Settings(_env_file=None))
    async with records.engine.begin() as conn:
        await conn.execute(
            text("UPDATE knowledge_base SET assistant_online=true WHERE id=:id"), {"id": records.kb}
        )
        await conn.execute(
            text("UPDATE conversation SET user_id='user-a' WHERE id=:id"), {"id": records.conv}
        )
    cursor, seen = None, []
    while True:
        result = await messages(records.conv, repo, "user-a", limit=limit, before=cursor)
        ids = [item["message_id"] for item in result["items"]]
        if not seen:
            assert ids == records.messages[-limit:]
        seen += ids
        cursor = result["next_before"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 4
    assert set(seen) == set(records.messages)


@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("retryable", [False, True])
async def test_failure_snapshot_matches_history_even_without_trace_error(
    records,  # noqa: F811 -- pytest injects the imported fixture
    cancelled,
    retryable,
):
    repo = GatewayRepository(records.engine, Settings(_env_file=None))
    error = {
        "request_id": "failure-test",
        "code": "CLIENT_DISCONNECTED" if cancelled else "INTERNAL_ERROR",
        "message": "回答未完成",
        "retryable": retryable,
        "partial_content": "部分回答",
        "conversation_id": str(records.conv),
        "message_id": str(records.messages[-1]),
        "trace_id": str(records.trace),
    }
    async with records.engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE assistant_request SET status='RUNNING', "
                "terminal_response='{}'::jsonb WHERE id=:id"
            ),
            {"id": records.request},
        )
        await conn.execute(
            text(
                "UPDATE message SET status='STREAMING', response_payload='{}'::jsonb WHERE id=:id"
            ),
            {"id": records.messages[-1]},
        )
        await conn.execute(
            text("UPDATE rag_trace SET status='RUNNING' WHERE trace_id=:id"), {"id": records.trace}
        )
    if cancelled:
        await repo.cancel_assistant_request(
            request_id=records.request,
            external_request_id="failure-test",
            payload=error,
            partial_content=error["partial_content"],
        )
    else:
        await repo.fail_assistant_request(request_id=records.request, payload=error)
    async with records.engine.begin() as conn:
        saved = (
            (
                await conn.execute(
                    text("SELECT status, terminal_error FROM assistant_request WHERE id=:id"),
                    {"id": records.request},
                )
            )
            .mappings()
            .one()
        )
        trace = (
            (
                await conn.execute(
                    text("SELECT status, error FROM rag_trace WHERE trace_id=:id"),
                    {"id": records.trace},
                )
            )
            .mappings()
            .one()
        )
        assert trace["status"] == saved["status"] == ("CANCELLED" if cancelled else "FAILED")
        assert saved["terminal_error"] == error
        assert all(trace["error"][k] == v for k, v in error.items())
        # Simulate trace detail expiry; the message retains its public error.
        await conn.execute(
            text("UPDATE rag_trace SET error='{}'::jsonb WHERE trace_id=:id"), {"id": records.trace}
        )
    rows = await repo.list_assistant_messages(conversation_id=records.conv, limit=10)
    row = next(row for row in rows if row["id"] == records.messages[-1])
    history = AssistantMessageResponse.model_validate(_message(row)).model_dump()
    assert row["response_payload"]["error"] == error
    assert history["content"] == error["partial_content"]
    assert history["content_blocks"] == []
    assert history["error"] == {k: error[k] for k in ("code", "message", "retryable")}
    # A late finalizer or a second failure must not replace a terminal snapshot.
    await repo.fail_assistant_request(request_id=records.request, payload={"code": "OTHER"})
    rows = await repo.list_assistant_messages(conversation_id=records.conv, limit=10)
    assert (
        next(row for row in rows if row["id"] == records.messages[-1])["response_payload"]["error"]
        == error
    )
