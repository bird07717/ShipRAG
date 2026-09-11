"""RAG-only first-turn safeguards. Never used by the shared Support router."""

from __future__ import annotations

import math
import re
from dataclasses import replace
from uuid import UUID

from app.rag.doc_router import DocRoutingDecision
from app.rag.models import RetrievalCandidate


def raw_document_scores(candidates: list[RetrievalCandidate]) -> dict[UUID, float]:
    scores: dict[UUID, float] = {}
    for candidate in candidates:
        score = candidate.rerank_score
        if (
            score is None
            or isinstance(score, bool)
            or not math.isfinite(score)
            or not 0 <= score <= 1
        ):
            return {}
        scores[candidate.document_id] = max(scores.get(candidate.document_id, 0), score)
    return scores


def protect_aggregation(
    decision: DocRoutingDecision, candidates: list[RetrievalCandidate]
) -> DocRoutingDecision:
    scores = raw_document_scores(candidates)
    chosen = decision.document_id
    if decision.resolved_from != "AGGREGATION" or chosen not in scores:
        return decision
    strongest = max(scores, key=lambda key: scores[key])
    if scores[strongest] <= scores[chosen] + 1e-6:
        return decision
    titles = {item.document_id: item.title for item in decision.doc_scores}
    return replace(
        decision,
        action="CLARIFY",
        document_id=None,
        document_title=None,
        resolved_from="RAG_AGGREGATION_CONFLICT",
        pending_options=[
            {"document_id": str(key), "title": titles[key]} for key in (strongest, chosen)
        ],
        thresholds={
            **decision.thresholds,
            "rag_first_turn_rule": "v1",
            "aggregate_document_id": str(chosen),
            "strongest_document_id": str(strongest),
            "aggregate_best": scores[chosen],
            "strongest_best": scores[strongest],
        },
    )


def single_strong_document(
    candidates: list[RetrievalCandidate], *, floor: float, gap: float
) -> tuple[UUID, float, float] | None:
    scores = raw_document_scores(candidates)
    ranked = sorted(scores, key=lambda key: (-scores[key], str(key)))
    if len(ranked) < 2:
        return None
    best, runner = scores[ranked[0]], scores[ranked[1]]
    if best >= floor and best - runner >= gap:
        return ranked[0], best, runner
    return None


def title_recall_terms(question: str) -> list[str]:
    """Exact alphanumeric model/code tokens; not generic action expansion."""
    tokens = re.findall(
        r"(?<![A-Za-z0-9])[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*(?![A-Za-z0-9])", question
    )
    return list(
        dict.fromkeys(
            token.casefold()
            for token in tokens
            if 4 <= len(token) <= 32 and re.search(r"[A-Za-z]", token) and re.search(r"\d", token)
        )
    )[:3]
