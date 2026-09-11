from __future__ import annotations

import asyncio
import os
from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.api.answer_reviews import get_answer_reviews
from app.assistant.retest import run_retest
from app.assistant.reviews import AnswerReviews, encoded
from app.common.errors import ApiError


@pytest_asyncio.fixture
async def records():
    url = os.getenv("SUPPORT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SUPPORT_TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url)
    kb, other_kb, conv, request, document, other_document = [uuid4() for _ in range(6)]
    message_ids = [uuid4() for _ in range(4)]
    trace = uuid4()
    response = {
        "answer": "**《原文档》**",
        "response_type": "DOC_DELIVERED",
        "document": {"document_id": str(document), "title": "原文档"},
        "content": [{"type": "text", "text": "第一步：打开设备"}],
        "references": [{"document_id": str(document), "title": "原文档"}],
    }
    async with engine.begin() as conn:
        for kid, did in [(kb, document), (other_kb, other_document)]:
            await conn.execute(
                text("INSERT INTO knowledge_base (id, name) VALUES (:id, :name)"),
                {"id": kid, "name": f"review-test-{kid}"},
            )
            await conn.execute(
                text(
                    "INSERT INTO document_source "
                    "(id, kb_id, filename, display_name, minio_bucket, minio_object_key, "
                    "file_hash, file_size, mime_type) "
                    "VALUES (:id, :kb, 't.docx', '原文档', 'test', :key, :hash, 0, 'test')"
                ),
                {"id": did, "kb": kid, "key": str(did), "hash": "0" * 64},
            )
        await conn.execute(
            text("INSERT INTO conversation (id, knowledge_id) VALUES (:id, :kb)"),
            {"id": conv, "kb": kb},
        )
        for seq, (role, content) in enumerate(
            [
                ("USER", "设备如何开机"),
                ("ASSISTANT", "按电源键"),
                ("USER", "下一步怎么操作"),
                ("ASSISTANT", "**《原文档》**"),
            ],
            1,
        ):
            await conn.execute(
                text(
                    "INSERT INTO message "
                    "(id, conversation_id, sequence_no, role, content, status, response_payload) "
                    "VALUES (:id, :conv, :seq, :role, :content, 'COMPLETED', "
                    "CAST(:payload AS jsonb))"
                ),
                {
                    "id": message_ids[seq - 1],
                    "conv": conv,
                    "seq": seq,
                    "role": role,
                    "content": content,
                    "payload": encoded(response if seq == 4 else {}),
                },
            )
        await conn.execute(
            text(
                "INSERT INTO rag_trace "
                "(id, trace_id, request_id, kb_id, conversation_id, message_id, question, "
                "status, source, engine) "
                "VALUES (:id, :trace, 'review-test', :kb, :conv, :msg, '下一步怎么操作', "
                "'COMPLETED', 'ASSISTANT_API', 'RAG')"
            ),
            {"id": uuid4(), "trace": trace, "kb": kb, "conv": conv, "msg": message_ids[3]},
        )
        await conn.execute(
            text(
                "INSERT INTO assistant_request "
                "(id, external_user_id, knowledge_id, idempotency_key, request_hash, "
                "conversation_id, "
                "user_message_id, assistant_message_id, trace_id, status, "
                "terminal_response, replay_count) "
                "VALUES (:id, 'user-a', :kb, 'key', :hash, :conv, :user, :msg, :trace, "
                "'COMPLETED', CAST(:response AS jsonb), 3)"
            ),
            {
                "id": request,
                "kb": kb,
                "hash": "1" * 64,
                "conv": conv,
                "user": message_ids[2],
                "msg": message_ids[3],
                "trace": trace,
                "response": encoded(response),
            },
        )
    try:
        yield SimpleNamespace(
            service=AnswerReviews(engine),
            engine=engine,
            kb=kb,
            other_kb=other_kb,
            conv=conv,
            request=request,
            document=document,
            other_document=other_document,
            messages=message_ids,
            trace=trace,
            response=response,
        )
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM knowledge_base WHERE id IN (:kb, :other)"),
                {"kb": kb, "other": other_kb},
            )
        await engine.dispose()


def values(**kwargs):
    return {
        "revision": 0,
        "category": "WRONG_DOCUMENT",
        "status": "OPEN",
        "expected_document_id": None,
        "expectation": "保留正确步骤",
        "notes": "",
        **kwargs,
    }


async def test_lists_requests_once_and_snapshots_full_response_with_history(records):
    r = records
    result = await r.service.records(
        r.kb, marked=False, status=None, query="下一步", limit=20, offset=0
    )
    assert result["total"] == 1
    assert result["items"][0]["replay_count"] == 3
    detail = await r.service.detail(r.kb, r.request)
    assert detail["snapshot"]["response"] == r.response
    assert [m["role"] for m in detail["snapshot"]["history"]] == ["USER", "ASSISTANT"]
    assert detail["snapshot"]["history_complete"] is True
    assert detail["snapshot"]["trace_id"] == str(r.trace)
    assert (
        await r.service.records(r.kb, marked=False, status=None, query="%", limit=20, offset=0)
    )["total"] == 0
    assert (
        await r.service.records(r.other_kb, marked=False, status=None, query="", limit=20, offset=0)
    )["total"] == 0


async def test_kb_boundary_and_revision_conflicts(records):
    r = records
    with pytest.raises(ApiError) as exc:
        await r.service.detail(r.other_kb, r.request)
    assert exc.value.status_code == 404
    with pytest.raises(ApiError) as exc:
        await r.service.save(r.kb, r.request, values(expected_document_id=r.other_document))
    assert exc.value.code == "EXPECTED_DOCUMENT_NOT_FOUND"
    results = await asyncio.gather(
        r.service.save(r.kb, r.request, values(expected_document_id=r.document)),
        r.service.save(r.kb, r.request, values()),
        return_exceptions=True,
    )
    assert sum(isinstance(item, dict) for item in results) == 1
    assert (
        next(item for item in results if isinstance(item, ApiError)).code
        == "ANSWER_REVIEW_CONFLICT"
    )
    with pytest.raises(ApiError):
        await r.service.save(r.other_kb, r.request, values())
    saved = await r.service.save(r.kb, r.request, values(revision=1, status="VERIFY"))
    assert saved["review"]["revision"] == 2
    assert (
        await r.service.records(r.kb, marked=True, status="VERIFY", query="", limit=20, offset=0)
    )["total"] == 1


async def test_saved_case_survives_log_retention_and_document_rename(records):
    r = records
    saved = await r.service.save(r.kb, r.request, values(expected_document_id=r.document))
    async with r.engine.begin() as conn:
        await conn.execute(
            text("UPDATE document_source SET display_name = '新名称' WHERE id = :id"),
            {"id": r.document},
        )
        await conn.execute(text("DELETE FROM conversation WHERE id = :id"), {"id": r.conv})
        await conn.execute(text("DELETE FROM rag_trace WHERE trace_id = :id"), {"id": r.trace})
    detail = await r.service.detail(r.kb, r.request)
    assert detail["snapshot"] == saved["snapshot"]
    assert detail["review"]["expected_document_title"] == "原文档"
    listed = await r.service.records(
        r.kb, marked=True, status=None, query="下一步", limit=20, offset=0
    )
    assert listed["total"] == 1
    assert listed["items"][0]["request_id"] == r.request
    assert listed["items"][0]["document"]["title"] == "原文档"


async def test_incomplete_context_refuses_retest(records):
    r = records
    async with r.engine.begin() as conn:
        await conn.execute(text("DELETE FROM message WHERE id = :id"), {"id": r.messages[1]})
    saved = await r.service.save(r.kb, r.request, values())
    assert not saved["snapshot"]["history_complete"]
    with pytest.raises(ApiError) as exc:
        await r.service.reserve_retest(r.kb, saved["review"]["id"], uuid4(), 1)
    assert exc.value.code == "RETEST_CONTEXT_MISSING"


async def test_retest_reservation_is_scoped_idempotent_and_serialized(records):
    r = records
    saved = await r.service.save(r.kb, r.request, values())
    review = saved["review"]["id"]
    run = uuid4()
    _, created = await r.service.reserve_retest(r.kb, review, run, 1)
    assert created
    same, created = await r.service.reserve_retest(r.kb, review, run, 1)
    assert not created and same["status"] == "RUNNING"
    with pytest.raises(ApiError):
        await r.service.reserve_retest(r.other_kb, review, run, 1)
    with pytest.raises(ApiError) as exc:
        await r.service.reserve_retest(r.kb, review, uuid4(), 1)
    assert exc.value.code == "RETEST_RUNNING"
    await r.service.finish_retest(run, [], None)
    same, created = await r.service.reserve_retest(r.kb, review, run, 1)
    assert not created and same["status"] == "COMPLETED"
    await r.service.save(r.kb, r.request, values(revision=1, expectation="新预期"))
    with pytest.raises(ApiError) as exc:
        await r.service.reserve_retest(r.kb, review, uuid4(), 1)
    assert exc.value.code == "ANSWER_REVIEW_CONFLICT"


async def test_retest_replays_questions_in_new_internal_conversation_and_keeps_original(records):
    r = records
    saved = await r.service.save(r.kb, r.request, values())
    conv, run = uuid4(), uuid4()
    gateway = SimpleNamespace(
        repository=SimpleNamespace(resolve_policy=AsyncMock(return_value="policy")),
        handle=AsyncMock(
            return_value={"conversation_id": str(conv), "answer": "新回答", "content": []}
        ),
    )
    result = await run_retest(
        r.service, gateway, kb=r.kb, review_id=saved["review"]["id"], run_id=run, revision=1
    )
    assert result["status"] == "COMPLETED"
    calls = gateway.handle.await_args_list
    assert [call.kwargs["question"] for call in calls] == ["设备如何开机", "下一步怎么操作"]
    assert calls[0].kwargs["conversation_id"] is None
    assert calls[1].kwargs["conversation_id"] == conv != r.conv
    assert all(call.kwargs["user_id"] == f"review-{run}" for call in calls)
    assert (await r.service.detail(r.kb, r.request))["snapshot"] == saved["snapshot"]
    await run_retest(
        r.service, gateway, kb=r.kb, review_id=saved["review"]["id"], run_id=run, revision=1
    )
    assert gateway.handle.await_count == 2


@pytest.mark.parametrize("failure", ["choices", "timeout", "exception", "cancel"])
async def test_retest_stops_on_changed_choices_and_records_failures(failure):
    snapshot = {
        "question": "1",
        "history": [
            {"role": "USER", "content": "检查设备"},
            {
                "role": "ASSISTANT",
                "response_payload": {"clarification": {"choices": [{"title": "文档A"}]}},
            },
        ],
    }
    reviews = SimpleNamespace(
        reserve_retest=AsyncMock(return_value=({"snapshot": deepcopy(snapshot)}, True)),
        finish_retest=AsyncMock(return_value={}),
        diagnostics=AsyncMock(return_value={}),
    )
    gateway = SimpleNamespace(
        repository=SimpleNamespace(resolve_policy=AsyncMock()),
        handle=AsyncMock(
            return_value={
                "conversation_id": str(uuid4()),
                "answer": "选择",
                "clarification": {"choices": [{"title": "文档B"}]},
            }
        ),
    )
    if failure != "choices":
        gateway.handle.side_effect = {
            "timeout": TimeoutError(),
            "exception": RuntimeError("private"),
            "cancel": asyncio.CancelledError(),
        }[failure]
    args = dict(kb=uuid4(), review_id=uuid4(), run_id=uuid4(), revision=1)
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await run_retest(reviews, gateway, **args)
    else:
        await run_retest(reviews, gateway, **args)
    assert gateway.handle.await_count == 1
    assert reviews.finish_retest.await_count == 1
    error = reviews.finish_retest.await_args.args[2]
    assert error and "private" not in error
    if failure == "choices":
        assert "澄清选项" in error


def test_management_routes_and_validation(client: Any):
    service = AsyncMock()
    service.records.return_value = {"items": [], "total": 0}
    service.detail.return_value = {"snapshot": {}, "review": None, "retests": []}
    service.save.return_value = service.detail.return_value
    client.app.dependency_overrides[get_answer_reviews] = lambda: service
    base = f"/api/v1/knowledge-bases/{uuid4()}/answer-records"
    headers = {"Authorization": "Bearer test-token"}
    try:
        assert client.get(base).status_code == 401
        assert client.get(base, headers=headers).json() == {"items": [], "total": 0}
        assert client.get(base + "?limit=500", headers=headers).status_code == 422
        path = base + f"/{uuid4()}"
        assert client.get(path, headers=headers).status_code == 200
        assert client.put(path + "/review", headers=headers, json=values()).status_code == 200
        assert (
            client.put(path + "/review", headers=headers, json=values(revision=-1)).status_code
            == 422
        )
        assert (
            client.put(
                path + "/review", headers=headers, json=values(category="unknown")
            ).status_code
            == 422
        )
        assert client.get(
            base.replace("/api/v1/", "/api/v1/assistant/"), headers=headers
        ).status_code in (401, 404)
    finally:
        client.app.dependency_overrides.clear()


async def test_disabled_knowledge_still_allows_management_review(records):
    r = records
    async with r.engine.begin() as conn:
        await conn.execute(
            text("UPDATE knowledge_base SET status = 'DISABLED' WHERE id = :id"), {"id": r.kb}
        )
    saved = await r.service.save(r.kb, r.request, values(category="ACCEPTABLE", status="CLOSED"))
    assert saved["review"]["status"] == "CLOSED"
