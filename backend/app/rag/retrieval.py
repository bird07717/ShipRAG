from __future__ import annotations

from dataclasses import replace

from app.rag.models import RerankItem, RetrievalCandidate


def reciprocal_rank_fusion(
    vector_candidates: list[RetrievalCandidate],
    bm25_candidates: list[RetrievalCandidate],
    *,
    rrf_k: int,
    limit: int,
) -> list[RetrievalCandidate]:
    registry: dict[object, RetrievalCandidate] = {}
    scores: dict[object, float] = {}
    vector_ranks: dict[object, int] = {}
    bm25_ranks: dict[object, int] = {}

    for candidate in vector_candidates:
        registry[candidate.chunk_id] = candidate
        vector_ranks[candidate.chunk_id] = candidate.rank
        scores[candidate.chunk_id] = scores.get(candidate.chunk_id, 0.0) + 1.0 / (
            rrf_k + candidate.rank
        )
    for candidate in bm25_candidates:
        registry.setdefault(candidate.chunk_id, candidate)
        bm25_ranks[candidate.chunk_id] = candidate.rank
        scores[candidate.chunk_id] = scores.get(candidate.chunk_id, 0.0) + 1.0 / (
            rrf_k + candidate.rank
        )

    ordered_ids = sorted(
        registry,
        key=lambda chunk_id: (
            -scores[chunk_id],
            min(vector_ranks.get(chunk_id, 10**9), bm25_ranks.get(chunk_id, 10**9)),
            str(chunk_id),
        ),
    )[:limit]
    return [
        replace(
            registry[chunk_id],
            rank=rank,
            vector_rank=vector_ranks.get(chunk_id),
            bm25_rank=bm25_ranks.get(chunk_id),
            bm25_score=next(
                (
                    candidate.bm25_score
                    for candidate in bm25_candidates
                    if candidate.chunk_id == chunk_id
                ),
                None,
            ),
            rrf_score=scores[chunk_id],
        )
        for rank, chunk_id in enumerate(ordered_ids, start=1)
    ]


def apply_rerank(
    candidates: list[RetrievalCandidate], items: list[RerankItem]
) -> list[RetrievalCandidate]:
    return [
        replace(
            candidates[item.index],
            rank=rank,
            rerank_rank=rank,
            rerank_score=item.score,
        )
        for rank, item in enumerate(items, start=1)
    ]
