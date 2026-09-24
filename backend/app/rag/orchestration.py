"""Query provenance and candidate fusion shared by the active RAG V2 retriever."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from app.rag.models import DocumentCardCandidate, RetrievalCandidate

QuerySource = Literal["ORIGINAL", "STANDALONE", "CONTEXT_ANCHOR"]


@dataclass(frozen=True, slots=True)
class SemanticQuery:
    text: str
    sources: tuple[QuerySource, ...]

    def trace_dict(self) -> dict[str, object]:
        return {
            "query_sha256": hashlib.sha256(self.text.encode("utf-8")).hexdigest(),
            "query_chars": len(self.text),
            "sources": list(self.sources),
        }


@dataclass(frozen=True, slots=True)
class QueryRetrieval:
    query: SemanticQuery
    chunk_vector: list[RetrievalCandidate]
    chunk_bm25: list[RetrievalCandidate]
    card_vector: list[DocumentCardCandidate]
    card_bm25: list[DocumentCardCandidate]


def _rrf_score(rank: int, rrf_k: int) -> float:
    return 1.0 / (rrf_k + max(1, rank))


@dataclass(slots=True)
class _ChunkAggregate:
    candidate: RetrievalCandidate
    score: float
    query_sources: set[str]
    hits: set[tuple[int, str]]


@dataclass(slots=True)
class _CardAggregate:
    candidate: DocumentCardCandidate
    score: float
    query_sources: set[str]
    hits: set[tuple[int, str]]


@dataclass(slots=True)
class _DocumentAggregate:
    document_id: str
    document: str
    card: dict[str, object] | None
    card_score: float
    chunk_score: float
    chunk_ids: list[str]
    query_sources: set[str]


def build_shadow_candidates(
    batches: list[QueryRetrieval],
    *,
    rrf_k: int,
    chunk_limit: int,
    document_limit: int,
) -> dict[str, object]:
    """Fuse by unique query/channel hit, never by repeated object occurrence."""
    chunk_registry: dict[str, _ChunkAggregate] = {}
    card_registry: dict[str, _CardAggregate] = {}

    for batch_index, batch in enumerate(batches):
        query_sources = set(batch.query.sources)
        for channel, chunk_candidates in (
            ("VECTOR", batch.chunk_vector),
            ("BM25", batch.chunk_bm25),
        ):
            for candidate in chunk_candidates:
                key = str(candidate.chunk_id)
                chunk_item = chunk_registry.setdefault(
                    key,
                    _ChunkAggregate(candidate, 0.0, set(), set()),
                )
                hit = (batch_index, channel)
                if hit in chunk_item.hits:
                    continue
                chunk_item.hits.add(hit)
                chunk_item.score += _rrf_score(candidate.rank, rrf_k)
                chunk_item.query_sources.update(query_sources)

        for channel, card_candidates in (
            ("VECTOR", batch.card_vector),
            ("BM25", batch.card_bm25),
        ):
            for card_candidate in card_candidates:
                key = str(card_candidate.document_id)
                card_item = card_registry.setdefault(
                    key,
                    _CardAggregate(card_candidate, 0.0, set(), set()),
                )
                hit = (batch_index, channel)
                if hit in card_item.hits:
                    continue
                card_item.hits.add(hit)
                card_item.score += _rrf_score(card_candidate.rank, rrf_k)
                card_item.query_sources.update(query_sources)

    ranked_chunks = sorted(
        chunk_registry.values(),
        key=lambda item: (-item.score, str(item.candidate.chunk_id)),
    )[:chunk_limit]
    chunks: list[dict[str, object]] = [
        {
            "chunk_id": str(item.candidate.chunk_id),
            "document_id": str(item.candidate.document_id),
            "document": item.candidate.document,
            "score": round(item.score, 8),
            "query_sources": sorted(item.query_sources),
        }
        for item in ranked_chunks
    ]

    documents: dict[str, _DocumentAggregate] = {}
    for chunk in ranked_chunks:
        document_id = str(chunk.candidate.document_id)
        entry = documents.setdefault(
            document_id,
            _DocumentAggregate(document_id, chunk.candidate.document, None, 0.0, 0.0, [], set()),
        )
        if len(entry.chunk_ids) < 3:
            entry.chunk_ids.append(str(chunk.candidate.chunk_id))
            entry.chunk_score += chunk.score
        entry.query_sources.update(chunk.query_sources)

    for document_id, card in card_registry.items():
        card_trace: dict[str, object] = {
            **card.candidate.trace_dict(),
            "score": round(card.score, 8),
            "query_sources": sorted(card.query_sources),
        }
        entry = documents.setdefault(
            document_id,
            _DocumentAggregate(document_id, card.candidate.document, None, 0.0, 0.0, [], set()),
        )
        entry.card = card_trace
        entry.card_score = card.score
        entry.query_sources.update(card.query_sources)

    ranked = sorted(
        documents.values(),
        key=lambda item: (-(item.card_score + item.chunk_score), item.document_id),
    )[:document_limit]
    ranked_documents: list[dict[str, object]] = [
        {
            "document_id": item.document_id,
            "document": item.document,
            "card": item.card,
            "card_score": round(item.card_score, 8),
            "chunk_score": round(item.chunk_score, 8),
            "score": round(item.card_score + item.chunk_score, 8),
            "chunk_ids": item.chunk_ids,
            "query_sources": sorted(item.query_sources),
        }
        for item in ranked
    ]

    return {
        "fusion_method": "MULTI_QUERY_RRF_DOCUMENT_PLUS_CHUNK_V1",
        "chunk_candidates": chunks,
        "document_candidates": ranked_documents[:document_limit],
    }
