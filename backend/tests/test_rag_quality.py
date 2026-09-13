from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from test_chat_doc_routing_flow import (
    DOC_A,
    DOC_B,
    TITLE_B,
    _candidate,
    _huge_document,
    _RecordingLlmProvider,
    _RoutingRepository,
    _service,
    _turn,
)
from test_chat_repository_state import _repository
from test_prompt_budget import _candidate as budget_candidate
from test_prompt_budget import _service as budget_service
from test_prompt_budget import _TrimRepository

from app.common.text import estimate_tokens
from app.rag.evaluation import evaluate_content, response_text
from app.rag.rerank import RerankError
from app.services.rag import _is_fact_question


@pytest.mark.asyncio
async def test_trace_keeps_retrieval_context_and_actual_generation_snapshot_separate():
    repo, connection = _repository([])
    turn = _turn()
    turn = replace(turn, retrieval=replace(turn.retrieval, context_max_chunks=2))
    await repo.save_prepared(
        turn,
        embedding_provider="fake",
        embedding_model="test",
        vector_candidates=[],
        bm25_candidates=[],
        fusion_candidates=[],
        rerank_candidates=[],
        rerank_result={},
        selected_context=[{"document_id": "retrieved"}],
        prompt="retrieval prompt",
        timings={},
    )
    assert json.loads(connection.executed[-1][1]["effective_retrieval"])["context_max_chunks"] == 2
    generation = {
        "kind": "CHUNK_RAG",
        "prompt_sha256": "actual-hash",
        "prompt_tokens": 10,
        "sources": [{"document_id": "confirmed"}],
    }
    await repo.complete_turn(
        turn,
        answer="answer",
        sources=[],
        usage={},
        citation_result={},
        timings={},
        prompt="actual prompt",
        doc_routing={"generation": generation},
    )
    sql, params = next(
        (sql, params) for sql, params in connection.executed if params and "generation" in params
    )
    assert json.loads(params["generation"]) == generation
    assert params["prompt"] == "actual prompt"
    assert "selected_context =" not in sql


@pytest.mark.parametrize(
    "question,expected",
    [
        ("电池电压是多少", True),
        ("这个按钮在哪里", True),
        ("为什么报警", True),
        ("完整步骤是什么", False),
        ("怎么升级", False),
        ("如何更换LRU", False),
    ],
)
def test_fact_intent_does_not_take_over_full_procedures(question, expected):
    assert _is_fact_question(question) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,kind", [("off", "DELIVER"), ("shadow", "DELIVER"), ("enabled", "DOC_QA")]
)
async def test_fact_qa_experiment_preserves_focus_and_delivery_default(mode, kind):
    repo = _RoutingRepository(turn=_turn(), candidates=[_candidate(DOC_A, 1), _candidate(DOC_A, 2)])
    service = _service(repo, _RecordingLlmProvider([]))
    service.settings = service.settings.model_copy(update={"m3_rag_fact_qa_mode": mode})
    prepared = await service.prepare(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=None,
        question="升级所需U盘格式是什么",
        request_id="fact",
    )
    plan, error = await service._safely_plan_chat_turn(
        prepared, "升级所需U盘格式是什么", "产品", "支持"
    )
    assert error is None and plan.kind == kind
    assert repo.focus_updates == [DOC_A]
    assert prepared.turn.context_version == 1
    assert (plan.routing.get("fact_qa") or {}).get("mode") == (None if mode == "off" else mode)


@pytest.mark.asyncio
async def test_fact_mode_never_changes_explicit_clarification_confirmation():
    repo = _RoutingRepository(
        turn=_turn(
            chat_context={
                "pending_options": [{"document_id": str(DOC_A), "title": "升级所需U盘格式是什么"}]
            }
        ),
        candidates=[_candidate(DOC_A, 1)],
    )
    service = _service(repo, _RecordingLlmProvider([]))
    service.settings = service.settings.model_copy(update={"m3_rag_fact_qa_mode": "enabled"})
    prepared = await service.prepare(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=None,
        question="升级所需U盘格式是什么",
        request_id="confirm",
    )
    plan, error = await service._safely_plan_chat_turn(
        prepared, "升级所需U盘格式是什么", "产品", "支持"
    )
    assert error is None and plan.kind == "DELIVER" and "fact_qa" not in plan.routing


@pytest.mark.asyncio
async def test_budget_preserves_strongest_chunk_before_ordering_steps():
    doc, parent = uuid4(), uuid4()
    candidates = [
        replace(
            budget_candidate(label * 400, rank=rank),
            document_id=doc,
            parent_id=parent,
            sequence_no=seq,
        )
        for label, rank, seq in [("甲", 1, 3), ("乙", 2, 1), ("丙", 3, 2)]
    ]
    repo = _TrimRepository(_turn(), candidates)
    service = budget_service(repo, token_budget=1100)
    result = await service.prepare(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=None,
        question="操作流程",
        request_id="quality-budget",
    )
    assert [c.rerank_rank for c in result.selected_candidates] == [2, 1]
    assert "甲" * 400 in result.prompt and "丙" * 400 not in result.prompt
    assert estimate_tokens(result.prompt) <= 1100
    assert repo.saved["selected_context"][1]["chunk_id"] == str(candidates[0].chunk_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("body_size", [35000, 70000])
async def test_long_focus_retrieves_only_confirmed_document_and_records_actual_prompt(body_size):
    document = _huge_document()
    document["elements"][0]["content"] = "甲" * body_size
    repo = _RoutingRepository(
        turn=_turn(focus_document_id=DOC_A),
        candidates=[_candidate(DOC_A, 1), _candidate(DOC_B, 2, title=TITLE_B)],
        document_blocks={str(DOC_A): document},
    )
    bm25 = repo.bm25_search = AsyncMock(side_effect=repo.bm25_search)
    vector = repo.vector_search = AsyncMock(side_effect=repo.vector_search)
    llm = _RecordingLlmProvider(["[MODE:PRODUCT_KNOWLEDGE]按步骤操作。[S1]"])
    service = _service(repo, llm)
    result = await service.generate(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=repo.turn.conversation_id,
        question="前提条件是什么",
        request_id="scope",
        retrieval_overrides={"context_max_chunks": 2},
    )
    assert TITLE_B not in llm.prompts[0]
    assert estimate_tokens(llm.prompts[0]) <= service.settings.m3_prompt_token_budget
    assert {s["document_id"] for s in repo.completed["sources"]} == {str(DOC_A)}
    assert {s["document_id"] for s in result["references"]} == {str(DOC_A)}
    for call in [bm25.call_args, vector.call_args]:
        assert call.kwargs["document_id"] == DOC_A
        assert call.kwargs["index_id"] == repo.turn.index_id
        assert call.kwargs["knowledge_id"] == repo.turn.knowledge_id
    generation = repo.completed["doc_routing"]["generation"]
    assert generation["kind"] == "CHUNK_RAG" and generation["prompt_sha256"]
    assert generation["sources"]
    assert all(s["document_id"] == str(DOC_A) for s in generation["sources"])


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["empty", "rerank", "timeout"])
async def test_long_focus_without_calibrated_evidence_does_not_use_other_document(failure):
    repo = _RoutingRepository(
        turn=_turn(focus_document_id=DOC_A),
        candidates=[_candidate(DOC_A, 1)],
        document_blocks={str(DOC_A): _huge_document()},
    )
    llm = _RecordingLlmProvider(["must not generate"])
    service = _service(repo, llm)
    prepared = await service.prepare(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=repo.turn.conversation_id,
        question="前提条件是什么",
        request_id="failure",
    )
    if failure == "empty":
        repo.candidates = [_candidate(DOC_B, 1, title=TITLE_B)]
    else:
        provider = service.rerank_factory(service.settings, prepared.turn.rerank)
        provider.rerank = AsyncMock(
            side_effect=TimeoutError()
            if failure == "timeout"
            else RerankError("UPSTREAM", "unavailable", degradable=True)
        )
        service.rerank_factory = lambda *_: provider
    plan, error = await service._safely_plan_chat_turn(prepared, "前提条件是什么", "产品", "支持")
    assert error is None and plan.kind == "NO_MATCH"
    result = service._result_for_fixed_plan(plan)
    assert result.response_type.value == "UNCONFIRMED"
    assert not plan.references and not result.valid_sources
    assert not repo.focus_updates and not llm.prompts


@pytest.mark.asyncio
async def test_scoped_retrieval_obeys_remaining_turn_deadline():
    repo = _RoutingRepository(
        turn=_turn(focus_document_id=DOC_A),
        candidates=[_candidate(DOC_A, 1)],
        document_blocks={str(DOC_A): _huge_document()},
    )
    service = _service(repo, _RecordingLlmProvider([]))
    prepared = await service.prepare(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=None,
        question="前提条件是什么",
        request_id="deadline",
    )
    prepared.started_at -= service.settings.m3_turn_deadline_seconds

    async def slow(**kwargs):
        await asyncio.sleep(1)
        return []

    repo.bm25_search = slow
    plan, error = await service._safely_plan_chat_turn(prepared, "前提条件是什么", "产品", "支持")
    assert error.code == "TURN_DEADLINE_EXCEEDED"
    assert repo.failed["retryable"] is True


@pytest.mark.parametrize("response_type", ["ANSWERED", "DOC_DELIVERED", "DOCUMENT_DELIVERED"])
def test_empty_answers_cannot_pass_with_only_correct_reference(response_type):
    response = {
        "response_type": response_type,
        "answer": "",
        "content": [],
        "references": [{"document_id": "A", "title": "正确文档"}],
    }
    assert "回答正文为空" in evaluate_content({}, response)


def test_quality_assertions_read_actual_delivery_body_and_reject_mutations():
    response = {
        "response_type": "DOC_DELIVERED",
        "answer": "**《文档》**",
        "references": [{"document_id": "A", "title": "文档"}],
        "content": [
            {"type": "text", "text": "断电后等待 5 分钟，再检查报警。"},
            {"type": "table", "text": "电压 | 24 V"},
            {"type": "image", "image_asset_id": "image"},
        ],
    }
    expected = {
        "response_types": ["DOC_DELIVERED"],
        "content_must_all": ["断电", "24 V"],
        "content_must_groups": [["5分钟", "五分钟"]],
        "content_forbidden_any": ["240V"],
        "min_tables": 1,
        "min_images": 1,
        "document_ids": ["A"],
    }
    assert evaluate_content(expected, response) == []
    assert "24 V" in response_text(response)
    for mutation in [[], response["content"][:1], response["content"][1:]]:
        assert evaluate_content(expected, {**response, "content": mutation})
    assert evaluate_content(expected, {**response, "sources": [{"document_id": "B"}]})
    assert evaluate_content(expected, {**response, "response_type": "ANSWERED"})
    assert evaluate_content({"forbidden_document_ids": ["A"]}, response)
    assert evaluate_content({"content_forbidden_any": ["断电"]}, response)


def test_quality_evaluator_checks_domain_assist_boundaries():
    response = {
        "response_type": "ANSWERED",
        "answer_mode": "PRODUCT_GENERAL",
        "answer": "先检查供电。",
        "content": [{"type": "text", "text": "先检查供电。"}],
        "references": [],
        "disclaimer": "仅供技术排查参考，必要时联系海兰信专业人员。",
    }
    expected = {
        "response_types": ["ANSWERED"],
        "answer_modes": ["PRODUCT_GENERAL"],
        "require_no_reference": True,
        "require_disclaimer": True,
        "disclaimer_must_all": ["仅供技术排查参考", "海兰信专业人员"],
    }
    assert evaluate_content(expected, response) == []
    assert evaluate_content(expected, {**response, "references": [{"document_id": "A"}]})
    assert evaluate_content(expected, {**response, "disclaimer": None})


def test_title_only_delivery_is_not_a_complete_answer():
    assert evaluate_content(
        {},
        {
            "response_type": "DOC_DELIVERED",
            "answer": "**《文档》**",
            "references": [{"title": "文档"}],
        },
    ) == ["文档交付只有标题，缺少正文"]
