"""Unit tests for app.rag.doc_router aggregation and decision policy."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.rag.doc_router import (
    ACTION_CLARIFY,
    ACTION_DELIVER,
    ACTION_NO_MATCH,
    ACTION_OFFER_SWITCH,
    ACTION_STAY,
    aggregate_documents,
    decide_doc_routing,
    resolve_pending_option,
)
from app.rag.models import RetrievalCandidate

DOC_A = uuid4()
DOC_B = uuid4()
DOC_C = uuid4()


def _candidate(
    document_id,
    rank,
    rerank_score=None,
    title="doc",
) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=uuid4(),
        document_id=document_id,
        document=title,
        chunk_type="TEXT",
        content="content",
        token_count=10,
        section_path=[],
        element_ids=[],
        distance=None,
        similarity=None,
        rank=rank,
        rerank_score=rerank_score,
    )


def _settings_kwargs(**overrides):
    base = dict(
        t_high=0.9,
        t_low=0.5,
        ratio=1.8,
        min_hits=2,
        max_hits=3,
        stay_score=0.35,
        switch_gap=0.25,
        lock_best_floor=0.75,
    )
    base.update(overrides)
    return base


def test_aggregate_groups_caps_and_sorts():
    candidates = [
        _candidate(DOC_A, 1, 0.9, "A"),
        _candidate(DOC_A, 2, 0.8),
        _candidate(DOC_A, 3, 0.7),
        _candidate(DOC_A, 4, 0.6),
        _candidate(DOC_B, 5, 0.5, "B"),
    ]
    scores, meta = aggregate_documents(candidates, max_hits=3)
    assert [item.document_id for item in scores] == [DOC_A, DOC_B]
    top = scores[0]
    assert top.hits == 4
    assert top.score == pytest.approx(0.9 + 0.8 + 0.7)  # capped at 3 chunks
    assert top.best == 0.9
    assert meta == {"score_normalization": "NONE", "score_source": "RERANK"}


def test_aggregate_falls_back_to_rank_score_without_rerank():
    candidates = [
        _candidate(DOC_A, 1, None),
        _candidate(DOC_A, 2, None),
        _candidate(DOC_B, 3, None),
    ]
    scores, meta = aggregate_documents(candidates, max_hits=3)
    assert scores[0].document_id == DOC_A
    assert scores[0].score == 1.0 + 0.5
    assert meta == {"score_normalization": "NONE", "score_source": "RANK_FALLBACK"}


def test_aggregate_rescales_logits_above_unit_range():
    # a rerank provider emitting raw (non-[0,1]) scores must not break the
    # threshold semantics: the batch is divided by its max
    candidates = [
        _candidate(DOC_A, 1, 8.0),
        _candidate(DOC_A, 2, 6.0),
        _candidate(DOC_B, 3, 3.0),
    ]
    scores, meta = aggregate_documents(candidates, max_hits=3)
    assert meta == {"score_normalization": "SCALED_BY_MAX", "score_source": "RERANK"}
    assert scores[0].best == pytest.approx(1.0)
    assert scores[0].score == pytest.approx(1.0 + 0.75)
    assert scores[1].best == pytest.approx(0.375)


def test_aggregate_rescales_signed_scores_via_minmax():
    candidates = [
        _candidate(DOC_A, 1, -2.0),
        _candidate(DOC_B, 2, 2.0),
    ]
    scores, meta = aggregate_documents(candidates, max_hits=3)
    assert meta == {"score_normalization": "MINMAX", "score_source": "RERANK"}
    best_by_doc = {item.document_id: item.best for item in scores}
    assert best_by_doc[DOC_B] == pytest.approx(1.0)
    assert best_by_doc[DOC_A] == pytest.approx(0.0)


def test_aggregate_flags_mixed_score_sources():
    candidates = [
        _candidate(DOC_A, 1, 0.8),
        _candidate(DOC_B, 2, None),
    ]
    _, meta = aggregate_documents(candidates, max_hits=3)
    assert meta == {"score_normalization": "NONE", "score_source": "MIXED"}


def test_decision_records_score_normalization():
    candidates = [_candidate(DOC_A, 1, 5.0), _candidate(DOC_A, 2, 4.0)]
    decision = decide_doc_routing(
        question="如何升级",
        candidates=candidates,
        focus_document_id=None,
        chat_context={},
        **_settings_kwargs(),
    )
    assert decision.thresholds["score_normalization"] == "SCALED_BY_MAX"
    assert decision.thresholds["score_source"] == "RERANK"


def test_aligning_locks_clear_winner():
    candidates = [
        _candidate(DOC_A, 1, 0.9),
        _candidate(DOC_A, 2, 0.8),
        _candidate(DOC_B, 3, 0.2),
    ]
    decision = decide_doc_routing(
        question="如何升级",
        candidates=candidates,
        focus_document_id=None,
        chat_context={},
        **_settings_kwargs(),
    )
    assert decision.action == ACTION_DELIVER
    assert decision.document_id == DOC_A
    assert decision.resolved_from == "AGGREGATION"


def test_aligning_requires_margin_over_runner_up():
    candidates = [
        _candidate(DOC_A, 1, 0.9),
        _candidate(DOC_A, 2, 0.8),
        _candidate(DOC_B, 3, 0.95),
    ]
    decision = decide_doc_routing(
        question="如何升级",
        candidates=candidates,
        focus_document_id=None,
        chat_context={},
        **_settings_kwargs(),
    )
    assert decision.action == ACTION_CLARIFY
    assert len(decision.pending_options) == 2


def test_aligning_single_hit_below_min_hits_asks_once_with_pending():
    # one strong chunk is not lock-grade (min_hits), but it is eligible: ask
    # once with the candidate recorded as pending so the reply converges
    # instead of falling into an open-ended catalog loop
    candidates = [_candidate(DOC_A, 1, 1.0)]
    decision = decide_doc_routing(
        question="如何升级",
        candidates=candidates,
        focus_document_id=None,
        chat_context={},
        **_settings_kwargs(),
    )
    assert decision.action == ACTION_CLARIFY
    assert len(decision.pending_options) == 1
    assert decision.pending_options[0]["document_id"] == str(DOC_A)


def test_aligning_no_match_when_nothing_scores():
    decision = decide_doc_routing(
        question="如何升级",
        candidates=[_candidate(DOC_A, 1, 0.1)],
        focus_document_id=None,
        chat_context={},
        **_settings_kwargs(),
    )
    assert decision.action == ACTION_NO_MATCH


def test_doc_focus_offers_switch_when_other_doc_decisively_better():
    # P2-8 regression: keyword overlap keeps the focus doc above the stay
    # floor (0.4 >= 0.35), but a single strong hit on another document
    # (0.9, gap 0.5 > switch_gap 0.25) cannot reach the lock band (min_hits).
    # The topic has clearly moved on: offer a switch instead of staying.
    candidates = [
        _candidate(DOC_A, 1, 0.4),
        _candidate(DOC_B, 2, 0.9),
    ]
    decision = decide_doc_routing(
        question="第二步怎么做",
        candidates=candidates,
        focus_document_id=DOC_A,
        chat_context={},
        **_settings_kwargs(),
    )
    assert decision.action == ACTION_OFFER_SWITCH
    assert decision.document_id == DOC_A
    assert decision.pending_options[0]["document_id"] == str(DOC_B)


def test_doc_focus_stays_within_ambiguity_gap():
    # focus 0.7 vs other 0.8: the gap (0.1) is inside switch_gap (0.25), so
    # continuity wins and the conversation stays on the focus document
    candidates = [
        _candidate(DOC_A, 1, 0.7),
        _candidate(DOC_B, 2, 0.8),
    ]
    decision = decide_doc_routing(
        question="第二步怎么做",
        candidates=candidates,
        focus_document_id=DOC_A,
        chat_context={},
        **_settings_kwargs(),
    )
    assert decision.action == ACTION_STAY
    assert decision.document_id == DOC_A


def test_doc_focus_gap_requires_other_best_above_t_low():
    # aggregated noise must not trigger a switch: B's summed score (0.6)
    # clears t_low but its best chunk (0.3) does not, so the gap is not
    # decisive and the focus document (0.4 >= stay floor) is kept
    candidates = [
        _candidate(DOC_A, 1, 0.4),
        _candidate(DOC_B, 2, 0.3),
        _candidate(DOC_B, 3, 0.3),
    ]
    decision = decide_doc_routing(
        question="第二步怎么做",
        candidates=candidates,
        focus_document_id=DOC_A,
        chat_context={},
        **_settings_kwargs(),
    )
    assert decision.action == ACTION_STAY


def test_doc_focus_large_switch_gap_keeps_old_behavior():
    # with switch_gap 0.8 the 0.5 gap is not decisive: falls back to the
    # stay floor (0.4 >= 0.35) like before the P2-8 fix
    candidates = [
        _candidate(DOC_A, 1, 0.4),
        _candidate(DOC_B, 2, 0.9),
    ]
    decision = decide_doc_routing(
        question="第二步怎么做",
        candidates=candidates,
        focus_document_id=DOC_A,
        chat_context={},
        **_settings_kwargs(switch_gap=0.8),
    )
    assert decision.action == ACTION_STAY


def test_lock_requires_strong_single_chunk():
    # follow-up regression: two mediocre keyword-overlap hits (0.55 + 0.5 =
    # 1.05 >= t_high, hits=2) must NOT lock without a strong best chunk;
    # in ALIGNING the user is asked to clarify instead of a wrong dump
    candidates = [
        _candidate(DOC_B, 1, 0.55),
        _candidate(DOC_B, 2, 0.5),
        _candidate(DOC_C, 3, 0.6),
    ]
    decision = decide_doc_routing(
        question="怎么配置重启",
        candidates=candidates,
        focus_document_id=None,
        chat_context={},
        **_settings_kwargs(),
    )
    assert decision.action == ACTION_CLARIFY


def test_doc_focus_mediocre_hits_do_not_dump_other_document():
    # follow-up regression: focused on A, the user asks a follow-up; doc B
    # aggregates two mediocre hits (1.05 >= t_high) but its best chunk (0.55)
    # is below the lock floor -> stay on A and answer via DOC_QA
    candidates = [
        _candidate(DOC_A, 1, 0.4),
        _candidate(DOC_B, 2, 0.55),
        _candidate(DOC_B, 3, 0.5),
    ]
    decision = decide_doc_routing(
        question="第二步怎么做",
        candidates=candidates,
        focus_document_id=DOC_A,
        chat_context={},
        **_settings_kwargs(),
    )
    assert decision.action == ACTION_STAY
    assert decision.document_id == DOC_A


def test_doc_focus_competitive_focus_doc_blocks_auto_switch():
    # continuity: doc B passes the lock bar (score 1.1, best 0.8) but the
    # focus document is still strongly competitive (single 0.9 chunk), so
    # the ratio test against the focus score blocks the auto-switch
    candidates = [
        _candidate(DOC_A, 1, 0.9),
        _candidate(DOC_B, 2, 0.8),
        _candidate(DOC_B, 3, 0.3),
    ]
    decision = decide_doc_routing(
        question="升级前的检查项",
        candidates=candidates,
        focus_document_id=DOC_A,
        chat_context={},
        **_settings_kwargs(),
    )
    assert decision.action == ACTION_STAY
    assert decision.document_id == DOC_A


def test_doc_focus_dominant_other_doc_still_auto_switches():
    # even with a competitive focus doc, a dominant lock (best 0.9+, huge
    # aggregate margin) still switches without asking
    candidates = [
        _candidate(DOC_A, 1, 0.7),
        _candidate(DOC_A, 2, 0.65),
        _candidate(DOC_B, 3, 0.95),
        _candidate(DOC_B, 4, 0.9),
        _candidate(DOC_B, 5, 0.85),
    ]
    decision = decide_doc_routing(
        question="怎么更换故障PDC",
        candidates=candidates,
        focus_document_id=DOC_A,
        chat_context={},
        **_settings_kwargs(),
    )
    assert decision.action == ACTION_DELIVER
    assert decision.document_id == DOC_B


def test_doc_focus_switches_even_when_current_doc_above_stay_floor():
    # Live regression (2026-08-20): focused on the upgrade doc, the user asks
    # about replacing hardware. Keyword overlap keeps the focus doc at 0.67
    # (above stay_score) while the replacement doc locks at 2.5 with 7 hits.
    # Strong switch evidence must outrank the stay floor.
    candidates = [
        _candidate(DOC_B, 1, 0.88),
        _candidate(DOC_B, 2, 0.86),
        _candidate(DOC_B, 3, 0.84),
        _candidate(DOC_A, 4, 0.67),
    ]
    decision = decide_doc_routing(
        question="怎么更换故障PDC",
        candidates=candidates,
        focus_document_id=DOC_A,
        chat_context={},
        **_settings_kwargs(),
    )
    assert decision.action == ACTION_DELIVER
    assert decision.document_id == DOC_B


def test_doc_focus_switches_on_strong_other_doc():
    candidates = [
        _candidate(DOC_B, 1, 0.9),
        _candidate(DOC_B, 2, 0.8),
        _candidate(DOC_A, 3, 0.1),
    ]
    decision = decide_doc_routing(
        question="如何更换硬件",
        candidates=candidates,
        focus_document_id=DOC_A,
        chat_context={},
        **_settings_kwargs(),
    )
    assert decision.action == ACTION_DELIVER
    assert decision.document_id == DOC_B


def test_doc_focus_offers_switch_on_moderate_other_doc():
    candidates = [
        _candidate(DOC_B, 1, 0.9),
        _candidate(DOC_A, 2, 0.2),
    ]
    decision = decide_doc_routing(
        question="如何更换硬件",
        candidates=candidates,
        focus_document_id=DOC_A,
        chat_context={},
        **_settings_kwargs(),
    )
    assert decision.action == ACTION_OFFER_SWITCH
    assert decision.pending_options[0]["document_id"] == str(DOC_B)


def test_doc_focus_stays_when_no_alternative():
    decision = decide_doc_routing(
        question="随机问题",
        candidates=[_candidate(DOC_A, 1, 0.1)],
        focus_document_id=DOC_A,
        chat_context={},
        **_settings_kwargs(),
    )
    assert decision.action == ACTION_STAY


def test_pending_reply_ordinal_resolves_to_deliver():
    candidates = [_candidate(DOC_C, 1, 0.9)]
    decision = decide_doc_routing(
        question="第二个",
        candidates=candidates,
        focus_document_id=None,
        chat_context={
            "pending_options": [
                {"document_id": str(DOC_A), "title": "如何U盘升级PDC"},
                {"document_id": str(DOC_B), "title": "如何更换故障PDC"},
            ]
        },
        **_settings_kwargs(),
    )
    assert decision.action == ACTION_DELIVER
    assert decision.document_id == DOC_B
    assert decision.resolved_from == "CLARIFICATION_REPLY"


def test_pending_reply_affirmative_single_option():
    decision = decide_doc_routing(
        question="好的",
        candidates=[],
        focus_document_id=DOC_A,
        chat_context={"pending_options": [{"document_id": str(DOC_B), "title": "B文档"}]},
        **_settings_kwargs(),
    )
    assert decision.action == ACTION_DELIVER
    assert decision.document_id == DOC_B


def test_pending_reply_title_containment_resolves():
    decision = decide_doc_routing(
        question="U盘升级",
        candidates=[],
        focus_document_id=None,
        chat_context={
            "pending_options": [
                {"document_id": str(DOC_A), "title": "如何U盘升级PDC"},
                {"document_id": str(DOC_B), "title": "如何更换故障PDC"},
            ]
        },
        **_settings_kwargs(),
    )
    assert decision.action == ACTION_DELIVER
    assert decision.document_id == DOC_A


def test_pending_reply_negative_falls_through():
    decision = decide_doc_routing(
        question="不用了",
        candidates=[_candidate(DOC_A, 1, 1.0), _candidate(DOC_A, 2, 0.9)],
        focus_document_id=None,
        chat_context={"pending_options": [{"document_id": str(DOC_B), "title": "B文档"}]},
        **_settings_kwargs(),
    )
    assert decision.action == ACTION_DELIVER
    assert decision.document_id == DOC_A
    assert decision.resolved_from == "AGGREGATION"


def test_pending_reply_unresolvable_falls_through():
    decision = decide_doc_routing(
        question="我在想别的事情",
        candidates=[],
        focus_document_id=None,
        chat_context={"pending_options": [{"document_id": str(DOC_B), "title": "B文档"}]},
        **_settings_kwargs(),
    )
    assert decision.action == ACTION_NO_MATCH


def test_resolve_pending_option_rules():
    options = [
        {"document_id": "a", "title": "如何U盘升级PDC"},
        {"document_id": "b", "title": "如何更换故障PDC"},
    ]
    assert resolve_pending_option("第二个", options) == options[1]
    assert resolve_pending_option("第1个", options) == options[0]
    assert resolve_pending_option("好", [options[0]]) == options[0]
    assert resolve_pending_option("不用", [options[0]]) is None
    assert resolve_pending_option("如何更换故障PDC", options) == options[1]
    assert resolve_pending_option("完全无关的回答", options) is None
    assert resolve_pending_option("", options) is None


def test_resolve_pending_option_ignores_whitespace_in_title():
    # live regression: the user echoes the offered title without the space
    # ("FFC如何检查接线盒" vs "FFC 如何检查接线盒"); raw substring matching
    # failed and the system asked the same question again
    options = [{"document_id": "a", "title": "故障解决文档13—FFC 如何检查接线盒"}]
    assert resolve_pending_option("FFC如何检查接线盒", options) == options[0]
    assert resolve_pending_option("ffc 如何检查接线盒", options) == options[0]


def test_decision_trace_dict_shape():
    candidates = [_candidate(DOC_A, 1, 0.9), _candidate(DOC_A, 2, 0.8)]
    decision = decide_doc_routing(
        question="如何升级",
        candidates=candidates,
        focus_document_id=None,
        chat_context={},
        **_settings_kwargs(),
    )
    payload = decision.to_dict()
    assert payload["decision"] == ACTION_DELIVER
    assert payload["phase_before"] == "ALIGNING"
    assert payload["phase_after"] == "DOC_FOCUS"
    assert payload["doc_scores"][0]["hits"] == 2
    assert payload["thresholds"]["t_high"] == 0.9
