"""Lightweight, document-only RAG fallback for formal Support.

This module deliberately reuses retrieval and document aggregation without
invoking the generic RAG answer path.  Support owns conversation persistence
and delivers a complete, frozen document only after this router makes a
high-confidence decision.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.common.errors import ApiError
from app.core.config import Settings
from app.ingestion.embedding import EmbeddingError, EmbeddingProvider, create_embedding_provider
from app.ingestion.models import EmbeddingInput
from app.rag.doc_router import ACTION_CLARIFY, ACTION_DELIVER, ACTION_NO_MATCH, decide_doc_routing
from app.rag.models import ModelSnapshot, RerankDocument, RetrievalCandidate
from app.rag.repository import RagRepository
from app.rag.rerank import RerankError, RerankProvider, create_rerank_provider
from app.rag.retrieval import apply_rerank, reciprocal_rank_fusion
from app.support.repository import SupportRepository


@dataclass(frozen=True, slots=True)
class SupportDocumentRoute:
    action: str
    document_id: UUID | None = None
    document_title: str | None = None
    confidence: float = 0.0
    options: list[dict[str, str]] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)


class SupportDocumentRouter:
    """Select a document for a Support request without generating an answer."""

    def __init__(
        self,
        repository: SupportRepository,
        settings: Settings,
        *,
        rag_repository: RagRepository | None = None,
        embedding_factory: Callable[[Settings], EmbeddingProvider] = create_embedding_provider,
        rerank_factory: Callable[[Settings, ModelSnapshot], RerankProvider] = (
            create_rerank_provider
        ),
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.rag_repository = rag_repository or RagRepository(repository.engine, settings)
        self.embedding_factory = embedding_factory
        self.rerank_factory = rerank_factory

    async def resolve(
        self,
        *,
        knowledge_id: UUID,
        index_id: UUID,
        question: str,
    ) -> SupportDocumentRoute:
        provider: EmbeddingProvider | None = None
        trace: dict[str, Any] = {"source": "RAG_FALLBACK"}
        try:
            provider = self.embedding_factory(self.settings)

            async def embed() -> list[list[float]]:
                return await provider.embed([EmbeddingInput(question)])

            embeddings, bm25_candidates = await asyncio.gather(
                embed(),
                self.rag_repository.bm25_search(
                    knowledge_id=knowledge_id,
                    index_id=index_id,
                    query=question,
                    limit=self.settings.m5_bm25_top_k,
                ),
            )
            if len(embeddings) != 1 or len(embeddings[0]) != self.settings.embedding_dimension:
                raise EmbeddingError("Support RAG 查询 Embedding 维度异常")
            vector_candidates = await self.rag_repository.vector_search(
                knowledge_id=knowledge_id,
                index_id=index_id,
                embedding=embeddings[0],
                limit=self.settings.m3_vector_top_k,
            )
            fused = reciprocal_rank_fusion(
                vector_candidates,
                bm25_candidates,
                rrf_k=self.settings.m5_rrf_k,
                limit=self.settings.m5_fusion_top_k,
            )
            trace["candidate_counts"] = {
                "bm25": len(bm25_candidates),
                "vector": len(vector_candidates),
                "fused": len(fused),
            }
            candidates, rerank_trace = await self._rerank(question, fused)
            trace["rerank"] = rerank_trace
            decision = decide_doc_routing(
                question=question,
                candidates=candidates,
                focus_document_id=None,
                chat_context={},
                t_high=self.settings.m3_doc_agg_t_high,
                t_low=self.settings.m3_doc_agg_t_low,
                ratio=self.settings.m3_doc_agg_ratio,
                min_hits=self.settings.m3_doc_agg_min_hits,
                max_hits=self.settings.m3_doc_agg_max_hits,
                stay_score=self.settings.m3_doc_stay_score,
                switch_gap=self.settings.m3_doc_switch_gap,
                lock_best_floor=self.settings.m3_doc_lock_best_floor,
            )
            routing = decision.to_dict()
            trace["document_routing"] = routing
            confidence = decision.doc_scores[0].best if decision.doc_scores else 0.0
            if decision.action == ACTION_DELIVER:
                return SupportDocumentRoute(
                    action=ACTION_DELIVER,
                    document_id=decision.document_id,
                    document_title=decision.document_title,
                    confidence=confidence,
                    trace=trace,
                )
            if decision.action == ACTION_CLARIFY:
                return SupportDocumentRoute(
                    action=ACTION_CLARIFY,
                    confidence=confidence,
                    options=list(decision.pending_options),
                    trace=trace,
                )
            return SupportDocumentRoute(action=ACTION_NO_MATCH, confidence=confidence, trace=trace)
        except (EmbeddingError, RerankError) as exc:
            trace.update(
                {
                    "source": "RAG_FALLBACK_DEGRADED",
                    "error_code": getattr(exc, "code", type(exc).__name__),
                }
            )
            return SupportDocumentRoute(action=ACTION_NO_MATCH, trace=trace)
        except Exception as exc:
            # A fallback must never turn a Profile miss into an accidental
            # document delivery.  Retain a bounded diagnostic in the trace.
            trace.update(
                {
                    "source": "RAG_FALLBACK_DEGRADED",
                    "error_code": type(exc).__name__,
                }
            )
            return SupportDocumentRoute(action=ACTION_NO_MATCH, trace=trace)
        finally:
            if provider is not None:
                await provider.aclose()

    async def _rerank(
        self, question: str, candidates: list[RetrievalCandidate]
    ) -> tuple[list[RetrievalCandidate], dict[str, Any]]:
        if not candidates:
            return [], {"status": "SKIPPED", "reason": "NO_CANDIDATES"}
        try:
            snapshot = await self.repository.get_rerank_model()
        except ApiError as exc:
            return candidates[: self.settings.m5_rerank_top_n], {
                "status": "DEGRADED",
                "reason": exc.code,
            }
        if snapshot is None:
            return candidates[: self.settings.m5_rerank_top_n], {
                "status": "DEGRADED",
                "reason": "MODEL_NOT_CONFIGURED",
            }
        try:
            provider = self.rerank_factory(self.settings, snapshot)
            try:
                outcome = await provider.rerank(
                    question,
                    [RerankDocument(candidate.content) for candidate in candidates],
                    self.settings.m5_rerank_top_n,
                )
            finally:
                await provider.aclose()
        except RerankError as exc:
            return candidates[: self.settings.m5_rerank_top_n], {
                "status": "DEGRADED",
                "reason": exc.code,
            }
        return apply_rerank(candidates, outcome.items), {
            "status": "PASSED",
            "provider": outcome.provider,
            "model": outcome.model_name,
            "usage": outcome.usage,
        }
