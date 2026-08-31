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
from app.rag.doc_router import (
    ACTION_CLARIFY,
    ACTION_DELIVER,
    ACTION_NO_MATCH,
    ACTION_OFFER_SWITCH,
    ACTION_STAY,
    decide_doc_routing,
)
from app.rag.models import ModelSnapshot, RerankDocument, RetrievalCandidate, RetrievalConfig
from app.rag.repository import RagRepository
from app.rag.rerank import RerankError, RerankProvider, create_rerank_provider
from app.rag.retrieval import apply_rerank, reciprocal_rank_fusion
from app.support.models import ProblemProfile
from app.support.problem_resolver import normalize_for_match
from app.support.repository import SupportRepository

__all__ = [
    "ACTION_CLARIFY",
    "ACTION_DELIVER",
    "ACTION_OFFER_SWITCH",
    "ACTION_STAY",
    "SupportDocumentRouter",
]


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
        retrieval: RetrievalConfig | None = None,
        profiles: list[ProblemProfile] | None = None,
        focus_document_id: UUID | None = None,
    ) -> SupportDocumentRoute:
        retrieval = retrieval or RetrievalConfig(
            vector_top_k=self.settings.m3_vector_top_k,
            bm25_top_k=self.settings.m5_bm25_top_k,
            fusion_top_k=self.settings.m5_fusion_top_k,
            rerank_top_n=self.settings.m5_rerank_top_n,
            context_max_chunks=self.settings.m3_context_max_chunks,
        )
        provider: EmbeddingProvider | None = None
        trace: dict[str, Any] = {
            "source": "RAG_FALLBACK",
            "retrieval_query": question,
        }
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
                    limit=retrieval.bm25_top_k,
                ),
            )
            if len(embeddings) != 1 or len(embeddings[0]) != self.settings.embedding_dimension:
                raise EmbeddingError("Support RAG 查询 Embedding 维度异常")
            vector_candidates = await self.rag_repository.vector_search(
                knowledge_id=knowledge_id,
                index_id=index_id,
                embedding=embeddings[0],
                limit=retrieval.vector_top_k,
            )
            fused = reciprocal_rank_fusion(
                vector_candidates,
                bm25_candidates,
                rrf_k=self.settings.m5_rrf_k,
                limit=retrieval.fusion_top_k,
            )
            excluded_document_ids = self._excluded_document_ids(question, profiles or [])
            if excluded_document_ids:
                fused = [
                    candidate
                    for candidate in fused
                    if candidate.document_id not in excluded_document_ids
                ]
                trace["excluded_document_ids"] = [
                    str(document_id) for document_id in sorted(excluded_document_ids, key=str)
                ]
            trace["candidate_counts"] = {
                "bm25": len(bm25_candidates),
                "vector": len(vector_candidates),
                "fused": len(fused),
            }
            candidates, rerank_trace = await self._rerank(
                question, fused, retrieval, profiles or []
            )
            trace["rerank"] = rerank_trace
            decision = decide_doc_routing(
                question=question,
                candidates=candidates,
                focus_document_id=focus_document_id,
                chat_context={},
                t_high=self.settings.m3_doc_agg_t_high,
                t_low=self.settings.m3_doc_agg_t_low,
                ratio=self.settings.m3_doc_agg_ratio,
                min_hits=self.settings.m3_doc_agg_min_hits,
                max_hits=self.settings.m3_doc_agg_max_hits,
                stay_score=self.settings.m3_doc_stay_score,
                switch_gap=self.settings.m3_doc_switch_gap,
                lock_best_floor=self.settings.m3_doc_lock_best_floor,
                single_clarify_best_floor=self.settings.m3_doc_single_clarify_best_floor,
            )
            routing = decision.to_dict()
            if rerank_trace.get("status") == "DEGRADED" and decision.action == ACTION_DELIVER:
                options = [
                    {"document_id": str(item.document_id), "title": item.title}
                    for item in decision.doc_scores[:3]
                ]
                routing.update(
                    {
                        "decision": ACTION_CLARIFY,
                        "resolved_from": "RERANK_DEGRADED_GUARD",
                        "pending_options": options,
                    }
                )
                trace["document_routing"] = routing
                return SupportDocumentRoute(
                    action=ACTION_CLARIFY,
                    confidence=decision.doc_scores[0].best if decision.doc_scores else 0.0,
                    options=options,
                    trace=trace,
                )
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
            if decision.action == ACTION_OFFER_SWITCH:
                return SupportDocumentRoute(
                    action=ACTION_OFFER_SWITCH,
                    document_id=decision.document_id,
                    confidence=confidence,
                    options=list(decision.pending_options),
                    trace=trace,
                )
            if decision.action == ACTION_STAY:
                return SupportDocumentRoute(
                    action=ACTION_STAY,
                    document_id=decision.document_id,
                    confidence=confidence,
                    trace=trace,
                )
            return SupportDocumentRoute(action=ACTION_NO_MATCH, confidence=confidence, trace=trace)
        except EmbeddingError as exc:
            raise ApiError(
                "UPSTREAM_UNAVAILABLE",
                "文档检索服务暂时不可用",
                503,
                details={"component": "EMBEDDING", "detail": str(exc)[:500]},
            ) from exc
        except RerankError as exc:
            if not exc.degradable:
                raise ApiError(
                    exc.code,
                    exc.public_message,
                    502,
                    details={"component": "RERANK", "detail": exc.detail},
                ) from exc
            trace.update(
                {
                    "source": "RAG_FALLBACK_DEGRADED",
                    "error_code": exc.code,
                }
            )
            return SupportDocumentRoute(action=ACTION_NO_MATCH, trace=trace)
        except ApiError:
            raise
        except Exception as exc:
            raise ApiError(
                "RAG_DOCUMENT_ROUTING_FAILED",
                "文档检索处理失败",
                502,
                details={"error_type": type(exc).__name__},
            ) from exc
        finally:
            if provider is not None:
                await provider.aclose()

    async def _rerank(
        self,
        question: str,
        candidates: list[RetrievalCandidate],
        retrieval: RetrievalConfig,
        profiles: list[ProblemProfile],
    ) -> tuple[list[RetrievalCandidate], dict[str, Any]]:
        if not candidates:
            return [], {"status": "SKIPPED", "reason": "NO_CANDIDATES"}
        snapshot = await self.repository.get_rerank_model()
        if snapshot is None:
            raise ApiError("MODEL_NOT_CONFIGURED", "Rerank 模型未配置", 503)
        try:
            provider = self.rerank_factory(self.settings, snapshot)
            try:
                outcome = await provider.rerank(
                    question,
                    [
                        RerankDocument(self._rerank_text(candidate, profiles))
                        for candidate in candidates
                    ],
                    retrieval.rerank_top_n,
                )
            finally:
                await provider.aclose()
        except RerankError as exc:
            if not exc.degradable:
                raise
            return candidates[: retrieval.rerank_top_n], {
                "status": "DEGRADED",
                "reason": exc.code,
            }
        return apply_rerank(candidates, outcome.items), {
            "status": "PASSED",
            "provider": outcome.provider,
            "model": outcome.model_name,
            "usage": outcome.usage,
        }

    @staticmethod
    def _excluded_document_ids(question: str, profiles: list[ProblemProfile]) -> set[UUID]:
        normalized_question = normalize_for_match(question)
        return {
            profile.document_id
            for profile in profiles
            if any(
                len(term) >= 4 and term in normalized_question
                for value in profile.exclusions
                if (term := normalize_for_match(value))
            )
        }

    @staticmethod
    def _rerank_text(candidate: RetrievalCandidate, profiles: list[ProblemProfile]) -> str:
        profile = next(
            (item for item in profiles if item.document_id == candidate.document_id),
            None,
        )
        metadata: list[str] = [f"文档标题：{candidate.document}"]
        if profile is not None:
            metadata.extend(
                [
                    f"标准问题：{profile.canonical_question}",
                    f"别名：{'；'.join(profile.aliases[:5])}",
                    f"路由摘要：{profile.routing_summary[:500]}",
                ]
            )
        metadata.append(f"正文片段：{candidate.content}")
        return "\n".join(line for line in metadata if not line.endswith("："))[:4_000]
