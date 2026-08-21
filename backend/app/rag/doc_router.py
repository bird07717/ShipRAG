"""Document-level routing for the chat pipeline.

Pure functions only: aggregation of reranked chunk candidates into document
scores, the ALIGNING/DOC_FOCUS decision policy, and rule-based resolution of
clarification replies. See docs/mvp1-chat-execution-plan.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

ACTION_DELIVER = "DELIVER"
ACTION_STAY = "STAY"
ACTION_CLARIFY = "CLARIFY"
ACTION_OFFER_SWITCH = "OFFER_SWITCH"
ACTION_NO_MATCH = "NO_MATCH"

_ORDINAL_MAP = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

_AFFIRMATIVE_PATTERN = re.compile(
    r"^(?:好(?:的|呀|啊)?|是(?:的)?|可以|要|行|对|嗯|确认|切换|换|ok|OK|Ok|好嘞)[呀啊。！!，,\s]*$"
)
_NEGATIVE_PATTERN = re.compile(r"^(?:不用|不要|不换|算了|取消|否|不|没有)[了呀啊。！!，,\s]*$")
_ORDINAL_PATTERN = re.compile(r"第\s*([一二三四五六七八九十]|\d+)\s*(?:个|份|篇|个文档)")


@dataclass(frozen=True, slots=True)
class DocScore:
    document_id: UUID
    title: str
    hits: int
    score: float
    best: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": str(self.document_id),
            "title": self.title,
            "hits": self.hits,
            "score": round(self.score, 4),
            "best": round(self.best, 4),
        }


@dataclass(frozen=True, slots=True)
class DocRoutingDecision:
    action: str
    document_id: UUID | None = None
    document_title: str | None = None
    resolved_from: str | None = None
    pending_options: list[dict[str, Any]] = field(default_factory=list)
    doc_scores: list[DocScore] = field(default_factory=list)
    phase_before: str = "ALIGNING"
    thresholds: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.action,
            "document_id": str(self.document_id) if self.document_id else None,
            "document_title": self.document_title,
            "resolved_from": self.resolved_from,
            "pending_options": self.pending_options,
            "doc_scores": [item.to_dict() for item in self.doc_scores],
            "phase_before": self.phase_before,
            "phase_after": "DOC_FOCUS" if self.action == ACTION_DELIVER else self.phase_before,
            "thresholds": self.thresholds,
        }


_SCORE_EPS = 1e-6


def _candidate_score(candidate: Any) -> float:
    rerank_score = getattr(candidate, "rerank_score", None)
    if rerank_score is not None:
        return float(rerank_score)
    rank = getattr(candidate, "rank", None) or 1
    return 1.0 / float(rank)


def _normalize_scores(raw_scores: list[float]) -> tuple[list[float], str]:
    """Map provider scores onto the [0, 1] scale the thresholds assume.

    Rerank providers disagree on scale: some emit relevance in [0, 1], others
    raw logits. Routing thresholds (t_high/t_low/stay_score) are meaningless
    against an uncalibrated scale, so any out-of-range batch is rescaled and
    the transform is recorded in the decision for observability.
    """
    max_score = max(raw_scores, default=0.0)
    min_score = min(raw_scores, default=0.0)
    if min_score < -_SCORE_EPS:
        span = max(max_score - min_score, _SCORE_EPS)
        return [min(1.0, max(0.0, (s - min_score) / span)) for s in raw_scores], "MINMAX"
    if max_score > 1.0 + _SCORE_EPS:
        return [min(1.0, max(0.0, s / max_score)) for s in raw_scores], "SCALED_BY_MAX"
    return [min(1.0, max(0.0, s)) for s in raw_scores], "NONE"


def aggregate_documents(
    candidates: list[Any], *, max_hits: int
) -> tuple[list[DocScore], dict[str, str]]:
    raw_scores: dict[UUID, list[float]] = {}
    rerank_present: set[UUID] = set()
    grouped: dict[UUID, dict[str, Any]] = {}
    for candidate in candidates:
        document_id = candidate.document_id
        entry = grouped.setdefault(
            document_id,
            {"title": candidate.document, "scores": []},
        )
        if getattr(candidate, "rerank_score", None) is not None:
            rerank_present.add(document_id)
        score = _candidate_score(candidate)
        raw_scores.setdefault(document_id, []).append(score)
        entry["scores"].append(score)

    flat_raw = [score for scores in raw_scores.values() for score in scores]
    normalized, normalization_mode = _normalize_scores(flat_raw)

    if len(rerank_present) == len(grouped) and grouped:
        score_source = "RERANK"
    elif not rerank_present:
        score_source = "RANK_FALLBACK"
    else:
        score_source = "MIXED"

    per_document: dict[UUID, list[float]] = {}
    cursor = 0
    for document_id, document_scores in raw_scores.items():
        per_document[document_id] = normalized[cursor : cursor + len(document_scores)]
        cursor += len(document_scores)

    doc_scores: list[DocScore] = []
    for document_id, entry in grouped.items():
        normalized_scores = per_document[document_id]
        ordered = sorted(normalized_scores, reverse=True)[: max(1, max_hits)]
        doc_scores.append(
            DocScore(
                document_id=document_id,
                title=entry["title"],
                hits=len(entry["scores"]),
                score=sum(ordered),
                best=max(normalized_scores),
            )
        )
    doc_scores.sort(key=lambda item: (-item.score, -item.hits, str(item.document_id)))
    meta = {"score_normalization": normalization_mode, "score_source": score_source}
    return doc_scores, meta


def normalize_for_match(text: str) -> str:
    """Whitespace-insensitive, case-insensitive form for title comparisons.

    Document titles often contain spaces the user will not type ("FFC 如何"
    vs "FFC如何"); raw substring matching silently fails on them.
    """
    return re.sub(r"\s+", "", text).casefold()


def resolve_pending_option(question: str, options: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Resolve a clarification reply against the offered options.

    Returns the chosen option dict, or None when the reply neither resolves
    nor rejects. Raises no exceptions; explicit rejection returns None.
    """
    normalized = question.strip()
    if not normalized or not options:
        return None
    if len(options) == 1 and _AFFIRMATIVE_PATTERN.match(normalized):
        return options[0]
    if _NEGATIVE_PATTERN.match(normalized):
        return None
    ordinal_match = _ORDINAL_PATTERN.search(normalized)
    if ordinal_match:
        token = ordinal_match.group(1)
        index = _ORDINAL_MAP.get(token, None) if not token.isdigit() else int(token)
        if index is not None and 1 <= index <= len(options):
            return options[index - 1]
    normalized_message = normalize_for_match(normalized)
    for option in options:
        title = normalize_for_match(str(option.get("title", "")))
        if len(normalized_message) >= 2 and (
            normalized_message in title or title in normalized_message
        ):
            return option
    return None


def _passes_lock(
    top: DocScore,
    runner_up_score: float | None,
    *,
    t_high: float,
    ratio: float,
    min_hits: int,
    best_floor: float,
) -> bool:
    # A lock needs aggregate strength (score >= t_high with enough hits) AND
    # at least one genuinely strong chunk (best >= best_floor). Without the
    # per-chunk floor, two mediocre keyword-overlap hits (0.5 + 0.5) could
    # "lock" a document on generic follow-up questions and dump it wholesale.
    if top.hits < min_hits or top.score < t_high or top.best < best_floor:
        return False
    if runner_up_score is not None and top.score < ratio * runner_up_score:
        return False
    return True


def decide_doc_routing(
    *,
    question: str,
    candidates: list[Any],
    focus_document_id: UUID | None,
    chat_context: dict[str, Any],
    t_high: float,
    t_low: float,
    ratio: float,
    min_hits: int,
    max_hits: int,
    stay_score: float,
    switch_gap: float = 0.25,
    lock_best_floor: float = 0.75,
) -> DocRoutingDecision:
    thresholds: dict[str, Any] = {
        "t_high": t_high,
        "t_low": t_low,
        "ratio": ratio,
        "min_hits": min_hits,
        "max_hits": max_hits,
        "stay_score": stay_score,
        "switch_gap": switch_gap,
        "lock_best_floor": lock_best_floor,
    }
    phase = "DOC_FOCUS" if focus_document_id is not None else "ALIGNING"

    pending_options = [
        option
        for option in (chat_context.get("pending_options") or [])
        if option.get("document_id")
    ]
    if pending_options:
        resolved = resolve_pending_option(question, pending_options)
        if resolved is not None:
            doc_scores, score_meta = aggregate_documents(candidates, max_hits=max_hits)
            thresholds.update(score_meta)
            return DocRoutingDecision(
                action=ACTION_DELIVER,
                document_id=UUID(str(resolved["document_id"])),
                document_title=str(resolved.get("title", "")) or None,
                resolved_from="CLARIFICATION_REPLY",
                doc_scores=doc_scores,
                phase_before=phase,
                thresholds=thresholds,
            )

    doc_scores, score_meta = aggregate_documents(candidates, max_hits=max_hits)
    thresholds.update(score_meta)

    if focus_document_id is None:
        if doc_scores:
            top = doc_scores[0]
            runner_up_score = doc_scores[1].score if len(doc_scores) > 1 else None
            if _passes_lock(
                top,
                runner_up_score,
                t_high=t_high,
                ratio=ratio,
                min_hits=min_hits,
                best_floor=lock_best_floor,
            ):
                return DocRoutingDecision(
                    action=ACTION_DELIVER,
                    document_id=top.document_id,
                    document_title=top.title,
                    resolved_from="AGGREGATION",
                    doc_scores=doc_scores,
                    phase_before=phase,
                    thresholds=thresholds,
                )
            eligible = [item for item in doc_scores if item.hits >= 1 and item.score >= t_low][:3]
            if eligible:
                # Ask once with the candidates recorded as pending options so
                # the loop converges: the user's next reply (affirmative,
                # ordinal, or the document title itself) resolves to DELIVER
                # instead of triggering another open-ended question.
                return DocRoutingDecision(
                    action=ACTION_CLARIFY,
                    pending_options=[
                        {"document_id": str(item.document_id), "title": item.title}
                        for item in eligible
                    ],
                    doc_scores=doc_scores,
                    phase_before=phase,
                    thresholds=thresholds,
                )
        return DocRoutingDecision(
            action=ACTION_NO_MATCH,
            doc_scores=doc_scores,
            phase_before=phase,
            thresholds=thresholds,
        )

    focus_scores = [item for item in doc_scores if item.document_id == focus_document_id]
    other_scores = [item for item in doc_scores if item.document_id != focus_document_id]
    focus_best = focus_scores[0].best if focus_scores else 0.0

    # A lock-grade hit on another document outranks staying: keyword overlap
    # can keep the focus document above the absolute stay floor even when the
    # question has clearly moved elsewhere. The focus document's own score
    # joins the ratio test as an implicit runner-up, so a competitive focus
    # document blocks an auto-switch: after delivering a document, follow-up
    # questions must not dump another document just because it aggregates
    # slightly higher.
    if other_scores:
        top = other_scores[0]
        other_runner_score = other_scores[1].score if len(other_scores) > 1 else 0.0
        focus_score_sum = focus_scores[0].score if focus_scores else 0.0
        competitor_score = max(other_runner_score, focus_score_sum)
        if _passes_lock(
            top,
            competitor_score if competitor_score > 0 else None,
            t_high=t_high,
            ratio=ratio,
            min_hits=min_hits,
            best_floor=lock_best_floor,
        ):
            return DocRoutingDecision(
                action=ACTION_DELIVER,
                document_id=top.document_id,
                document_title=top.title,
                resolved_from="AGGREGATION",
                doc_scores=doc_scores,
                phase_before=phase,
                thresholds=thresholds,
            )

    if other_scores:
        top = other_scores[0]
        # Offer a switch when the focus document is weak (below the stay
        # floor) OR when another document is decisively better: a single
        # strong hit that cannot reach the lock band (t_low < best < t_high
        # with hits < min_hits) would otherwise trap the conversation on the
        # old document despite the topic having moved on.
        decisive_gap = top.best >= t_low and top.best > focus_best + switch_gap
        if top.hits >= 1 and top.score >= t_low and (focus_best < stay_score or decisive_gap):
            return DocRoutingDecision(
                action=ACTION_OFFER_SWITCH,
                document_id=focus_document_id,
                pending_options=[{"document_id": str(top.document_id), "title": top.title}],
                doc_scores=doc_scores,
                phase_before=phase,
                thresholds=thresholds,
            )

    if focus_best >= stay_score:
        return DocRoutingDecision(
            action=ACTION_STAY,
            document_id=focus_document_id,
            doc_scores=doc_scores,
            phase_before=phase,
            thresholds=thresholds,
        )

    return DocRoutingDecision(
        action=ACTION_STAY,
        document_id=focus_document_id,
        doc_scores=doc_scores,
        phase_before=phase,
        thresholds=thresholds,
    )
