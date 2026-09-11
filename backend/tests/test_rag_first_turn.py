from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from test_chat_doc_routing_flow import (
    DOC_A,
    DOC_B,
    TITLE_A,
    TITLE_B,
    _candidate,
    _RecordingLlmProvider,
    _RoutingRepository,
    _service,
    _turn,
)

from app.rag.doc_router import DocRoutingDecision, aggregate_documents
from app.rag.first_turn import protect_aggregation, single_strong_document, title_recall_terms


def scored(values):
    return [
        replace(
            _candidate(doc, i + 1, title=TITLE_A if doc == DOC_A else TITLE_B), rerank_score=score
        )
        for i, (doc, score) in enumerate(values)
    ]


@pytest.mark.parametrize("score", [None, float("nan"), float("inf"), -1, 2, True])
def test_single_strong_rejects_uncalibrated_scores(score):
    assert (
        single_strong_document(scored([(DOC_A, score), (DOC_B, 0.1)]), floor=0.85, gap=0.18) is None
    )


@pytest.mark.parametrize(
    "values,expected",
    [
        ([(DOC_A, 0.9), (DOC_B, 0.4)], DOC_A),
        ([(DOC_A, 0.9), (DOC_A, 0.89), (DOC_B, 0.4)], DOC_A),
        ([(DOC_A, 0.9), (DOC_A, 0.5)], None),
        ([(DOC_A, 0.84), (DOC_B, 0.1)], None),
        ([(DOC_A, 0.9), (DOC_B, 0.8)], None),
    ],
)
def test_single_strong_uses_other_document_as_runner(values, expected):
    outcome = single_strong_document(scored(values), floor=0.85, gap=0.18)
    assert (outcome[0] if outcome else None) == expected


def test_conflict_protection_is_not_shared_router_change():
    candidates = scored([(DOC_A, 0.752), (DOC_B, 0.714), (DOC_B, 0.517), (DOC_B, 0.403)])
    scores, _ = aggregate_documents(candidates, max_hits=3)
    decision = DocRoutingDecision(
        action="DELIVER", document_id=DOC_B, resolved_from="AGGREGATION", doc_scores=scores
    )
    protected = protect_aggregation(decision, candidates)
    assert protected.action == "CLARIFY"
    assert [v["document_id"] for v in protected.pending_options] == [str(DOC_A), str(DOC_B)]
    assert decision.action == "DELIVER"  # shared result remains immutable
    assert (
        protect_aggregation(
            replace(decision, resolved_from="CLARIFICATION_REPLY"), candidates
        ).action
        == "DELIVER"
    )


@pytest.mark.parametrize(
    "question,expected",
    [
        ("FFC600内部电池如何更换", ["ffc600"]),
        ("PDC接线盒怎么检查", []),
        ("12345", []),
        ("DC24V故障和FFC600", ["dc24v", "ffc600"]),
        ("FFC600 ffc600", ["ffc600"]),
    ],
)
def test_title_recall_extracts_only_exact_model_codes(question, expected):
    assert title_recall_terms(question) == expected


@pytest.mark.asyncio
async def test_conflict_stores_options_without_focus_and_confirmation_delivers():
    repo = _RoutingRepository(
        turn=_turn(), candidates=[_candidate(DOC_A, 1), _candidate(DOC_B, 2, title=TITLE_B)]
    )
    service = _service(repo, _RecordingLlmProvider([]))
    prepared = await service.prepare(
        knowledge_id=repo.turn.knowledge_id,
        question="PDC的接线盒需要检查，具体步骤是什么？",
        request_id="conflict",
        conversation_id=None,
    )
    prepared.rerank_candidates = scored(
        [(DOC_A, 0.752), (DOC_B, 0.714), (DOC_B, 0.517), (DOC_B, 0.403)]
    )
    plan = await service._plan_chat_turn(
        prepared=prepared,
        question="PDC的接线盒需要检查，具体步骤是什么？",
        product_name="test",
        scope_description="test",
    )
    assert plan.kind == "ROUTING" and len(plan.pending_after) == 2
    assert plan.routing["resolved_from"] == "RAG_AGGREGATION_CONFLICT"
    assert not repo.focus_updates
    prepared.turn = replace(prepared.turn, chat_context={"pending_options": plan.pending_after})
    confirmed = await service._plan_chat_turn(
        prepared=prepared, question="第一个", product_name="test", scope_description="test"
    )
    assert confirmed.kind == "DELIVER"
    assert repo.focus_updates == [DOC_A]
    assert prepared.turn.context_version == 1 and prepared.turn.chat_context == {}
    assert any(block.type == "image" for block in confirmed.delivery["result"].content)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,expected", [("off", "ROUTING"), ("shadow", "ROUTING"), ("enabled", "DELIVER")]
)
async def test_single_strong_modes_preserve_full_delivery(mode, expected):
    repo = _RoutingRepository(
        turn=_turn(), candidates=[_candidate(DOC_A, 1), _candidate(DOC_B, 2, title=TITLE_B)]
    )
    service = _service(repo, _RecordingLlmProvider([]))
    service.settings = service.settings.model_copy(update={"m3_rag_single_strong_mode": mode})
    prepared = await service.prepare(
        knowledge_id=repo.turn.knowledge_id,
        question="LRU怎么升级版本",
        request_id="single",
        conversation_id=None,
    )
    prepared.rerank_candidates = scored([(DOC_A, 0.86), (DOC_B, 0.6)])
    plan = await service._plan_chat_turn(
        prepared=prepared, question="LRU怎么升级版本", product_name="test", scope_description="test"
    )
    assert plan.kind == expected
    assert repo.focus_updates == ([DOC_A] if mode == "enabled" else [])
    if mode == "shadow":
        assert plan.routing["thresholds"]["rag_single_strong"]["mode"] == "shadow"
    if mode == "enabled":
        assert any(block.type == "image" for block in plan.delivery["result"].content)
        assert plan.routing["resolved_from"] == "RAG_SINGLE_STRONG"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["history", "focus", "pending", "degraded"])
async def test_new_rules_do_not_override_followup_confirmation_or_degradation(phase, monkeypatch):
    repo = _RoutingRepository(
        turn=_turn(), candidates=[_candidate(DOC_A, 1), _candidate(DOC_B, 2, title=TITLE_B)]
    )
    service = _service(repo, _RecordingLlmProvider([]))
    service.settings = service.settings.model_copy(update={"m3_rag_single_strong_mode": "enabled"})
    prepared = await service.prepare(
        knowledge_id=repo.turn.knowledge_id,
        question="LRU怎么升级版本",
        request_id="phase-guard",
        conversation_id=None,
    )
    prepared.rerank_candidates = scored([(DOC_A, 0.86), (DOC_B, 0.6)])
    if phase == "history":
        prepared.turn = replace(prepared.turn, history=[{"role": "user", "content": "前一轮"}])
    elif phase == "focus":
        prepared.turn = replace(prepared.turn, focus_document_id=DOC_A)
    elif phase == "pending":
        prepared.turn = replace(
            prepared.turn,
            chat_context={"pending_options": [{"document_id": str(DOC_A), "title": TITLE_A}]},
        )
    else:
        prepared.rerank_status = "DEGRADED"

    def forbidden(*args, **kwargs):
        pytest.fail("First-turn-only rules ran in a protected phase")

    monkeypatch.setattr("app.services.rag.protect_aggregation", forbidden)
    monkeypatch.setattr("app.services.rag.single_strong_document", forbidden)
    await service._plan_chat_turn(
        prepared=prepared, question="LRU怎么升级版本", product_name="test", scope_description="test"
    )


@pytest.mark.asyncio
async def test_single_strong_context_conflict_does_not_mutate_prepared_turn():
    from app.common.errors import ApiError

    repo = _RoutingRepository(
        turn=_turn(), candidates=[_candidate(DOC_A, 1), _candidate(DOC_B, 2, title=TITLE_B)]
    )
    service = _service(repo, _RecordingLlmProvider([]))
    service.settings = service.settings.model_copy(update={"m3_rag_single_strong_mode": "enabled"})
    prepared = await service.prepare(
        knowledge_id=repo.turn.knowledge_id,
        question="LRU怎么升级版本",
        request_id="focus-conflict",
        conversation_id=None,
    )
    prepared.rerank_candidates = scored([(DOC_A, 0.86), (DOC_B, 0.6)])
    original_turn = prepared.turn
    repo.set_conversation_focus = AsyncMock(
        side_effect=ApiError("CONTEXT_CONFLICT", "会话已变化", 409)
    )
    with pytest.raises(ApiError, match="会话已变化"):
        await service._plan_chat_turn(
            prepared=prepared,
            question="LRU怎么升级版本",
            product_name="test",
            scope_description="test",
        )
    assert prepared.turn is original_turn
    assert repo.set_conversation_focus.call_args.kwargs["expected_context_version"] == 0
