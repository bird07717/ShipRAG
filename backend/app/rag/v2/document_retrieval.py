from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from app.core.config import Settings
from app.ingestion.embedding import EmbeddingError, EmbeddingProvider
from app.ingestion.models import EmbeddingInput
from app.rag.models import (
    DocumentCardCandidate,
    ModelSnapshot,
    PreparedRag,
    RerankDocument,
    RetrievalCandidate,
    Turn,
)
from app.rag.orchestration import (
    QueryRetrieval,
    QuerySource,
    SemanticQuery,
    build_shadow_candidates,
)
from app.rag.repository import SearchQueryUnsupportedError
from app.rag.rerank import RerankError, RerankProvider
from app.rag.v2.query_interpreter import QueryInterpretation

EmbeddingSelector = Callable[[Turn], EmbeddingProvider]
RerankFactory = Callable[[Settings, ModelSnapshot], RerankProvider]


@dataclass(frozen=True, slots=True)
class DocumentCandidate:
    document_id: UUID
    score: float
    chunk_ids: tuple[UUID, ...]
    rerank_score: float | None = None


def _document_rerank_text(
    item: dict[str, object],
    chunks_by_id: dict[str, RetrievalCandidate],
) -> str:
    lines = [f"文档标题：{str(item.get('document') or '').strip()}"]
    card = item.get("card")
    if isinstance(card, dict):
        document_type = str(card.get("document_type") or "").strip()
        summary = str(card.get("summary") or "").strip()
        tasks = [str(value).strip() for value in card.get("tasks") or [] if str(value).strip()]
        if document_type:
            lines.append(f"文档类型：{document_type}")
        if summary:
            lines.append(f"摘要：{summary}")
        if tasks:
            lines.append("任务：" + "；".join(tasks))
    chunk_ids = item.get("chunk_ids")
    if isinstance(chunk_ids, list):
        for raw_chunk_id in chunk_ids[:3]:
            chunk = chunks_by_id.get(str(raw_chunk_id))
            if chunk is not None and chunk.content.strip():
                lines.append("相关片段：" + chunk.content.strip())
    return "\n".join(lines)


def _rerank_query(question: str, interpretation: QueryInterpretation) -> str:
    resolved = interpretation.resolved_question.strip()
    original = question.strip()
    if not resolved or _query_key(resolved) == _query_key(original):
        return original
    return f"{resolved}\n用户原始问题：{original}"


def _query_key(value: str) -> str:
    return "".join(value.casefold().split()).rstrip("？?。.!！")


def _queries(question: str, interpretation: QueryInterpretation) -> list[SemanticQuery]:
    result: list[SemanticQuery] = []
    seen: set[str] = set()
    entries: list[tuple[str, QuerySource]] = [
        (question, "ORIGINAL"),
        (interpretation.resolved_question, "STANDALONE"),
        *((item, "PLANNER") for item in interpretation.search_queries),
    ]
    for text, source in entries:
        cleaned = " ".join(text.split())
        key = _query_key(cleaned)
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(SemanticQuery(cleaned, (source,)))
    return result


async def retrieve_documents(
    repository: Any,
    *,
    settings: Settings,
    turn: Turn,
    prepared: PreparedRag,
    question: str,
    interpretation: QueryInterpretation,
    embedding_selector: EmbeddingSelector,
    rerank_factory: RerankFactory,
) -> tuple[list[DocumentCandidate], dict[str, Any]]:
    queries = _queries(question, interpretation)
    embeddings: dict[int, list[float]] = {}
    degradations: list[str] = []
    provider: EmbeddingProvider | None = None
    try:
        provider = embedding_selector(turn)
        async with asyncio.timeout(settings.m3_query_embedding_timeout_seconds):
            vectors = await provider.embed([EmbeddingInput(item.text) for item in queries])
        if len(vectors) == len(queries) and all(
            len(vector) == settings.embedding_dimension for vector in vectors
        ):
            embeddings = dict(enumerate(vectors))
        else:
            degradations.append("QUERY_EMBEDDING_INVALID")
    except (EmbeddingError, TimeoutError) as exc:
        degradations.append(str(getattr(exc, "code", type(exc).__name__))[:100])
    finally:
        if provider is not None:
            try:
                await provider.aclose()
            except Exception:
                pass

    async def retrieve(index: int) -> QueryRetrieval:
        query = queries[index]
        lexical_results = await asyncio.gather(
            repository.bm25_search(
                knowledge_id=turn.knowledge_id,
                index_id=turn.index_id,
                query=query.text,
                limit=turn.retrieval.bm25_top_k,
            ),
            repository.document_card_bm25_search(
                knowledge_id=turn.knowledge_id,
                index_id=turn.index_id,
                query=query.text,
                limit=settings.m3_rag_document_card_top_k,
            ),
            return_exceptions=True,
        )
        lexical: list[list[Any]] = []
        for result in lexical_results:
            if isinstance(result, SearchQueryUnsupportedError):
                degradations.append("PARADEDB_UNSUPPORTED_QUERY_SHAPE")
                lexical.append([])
            elif isinstance(result, BaseException):
                raise result
            else:
                lexical.append(result)
        chunk_bm25 = cast(list[RetrievalCandidate], lexical[0])
        card_bm25 = cast(list[DocumentCardCandidate], lexical[1])
        vector = embeddings.get(index)
        if vector is None:
            chunk_vector: list[RetrievalCandidate] = []
            card_vector: list[DocumentCardCandidate] = []
        else:
            chunk_vector, card_vector = await asyncio.gather(
                repository.vector_search(
                    knowledge_id=turn.knowledge_id,
                    index_id=turn.index_id,
                    embedding=vector,
                    limit=turn.retrieval.vector_top_k,
                ),
                repository.document_card_vector_search(
                    knowledge_id=turn.knowledge_id,
                    index_id=turn.index_id,
                    embedding=vector,
                    limit=settings.m3_rag_document_card_top_k,
                ),
            )
        return QueryRetrieval(query, chunk_vector, chunk_bm25, card_vector, card_bm25)

    batches: list[QueryRetrieval] = []
    retrieval_results = await asyncio.gather(
        *(retrieve(index) for index in range(len(queries))), return_exceptions=True
    )
    for result in retrieval_results:
        if isinstance(result, BaseException):
            degradations.append(str(getattr(result, "code", type(result).__name__))[:100])
        else:
            batches.append(result)

    if batches:
        document_pool_limit = max(
            settings.m3_rag_full_document_limit,
            settings.m3_rag_document_card_top_k,
        )
        fused = build_shadow_candidates(
            batches,
            rrf_k=settings.m5_rrf_k,
            chunk_limit=turn.retrieval.fusion_top_k,
            document_limit=document_pool_limit,
        )
        raw_documents = cast(list[dict[str, object]], fused.get("document_candidates") or [])
    else:
        raw_documents = []

    chunks_by_id: dict[str, RetrievalCandidate] = {}
    for batch in batches:
        for chunk in (*batch.chunk_vector, *batch.chunk_bm25):
            chunks_by_id.setdefault(str(chunk.chunk_id), chunk)

    document_rerank: dict[str, Any] = {
        "status": "SKIPPED",
        "reason": "NOT_ENOUGH_CANDIDATES" if len(raw_documents) < 2 else "MODEL_NOT_CONFIGURED",
        "candidate_count": len(raw_documents),
    }
    rerank_scores: dict[str, float] = {}
    if len(raw_documents) >= 2 and turn.rerank is not None:
        rerank_provider: RerankProvider | None = None
        before_ids = [str(item.get("document_id") or "") for item in raw_documents]
        try:
            rerank_provider = rerank_factory(settings, turn.rerank)
            documents = [
                RerankDocument(_document_rerank_text(item, chunks_by_id)) for item in raw_documents
            ]
            async with asyncio.timeout(settings.m3_rerank_timeout_seconds):
                outcome = await rerank_provider.rerank(
                    _rerank_query(question, interpretation),
                    documents,
                    len(documents),
                )
            reordered = [raw_documents[item.index] for item in outcome.items]
            if len(reordered) != len(raw_documents):
                raise RerankError(
                    "UPSTREAM_PROTOCOL_ERROR",
                    "文档级 Rerank 未返回全部候选",
                    degradable=True,
                )
            raw_documents = reordered
            rerank_scores = {
                str(raw_documents[index].get("document_id") or ""): item.score
                for index, item in enumerate(outcome.items)
            }
            document_rerank = {
                "status": "PASSED",
                "provider": outcome.provider,
                "model": outcome.model_name,
                "candidate_count": len(raw_documents),
                "before_document_ids": before_ids,
                "after_document_ids": [
                    str(item.get("document_id") or "") for item in raw_documents
                ],
                "scores": [round(item.score, 8) for item in outcome.items],
            }
        except (RerankError, TimeoutError) as exc:
            document_rerank = {
                "status": "DEGRADED",
                "reason": str(getattr(exc, "code", "TIMEOUT"))[:100],
                "candidate_count": len(raw_documents),
                "before_document_ids": before_ids,
                "after_document_ids": before_ids,
            }
        finally:
            if rerank_provider is not None:
                await rerank_provider.aclose()

    candidates: list[DocumentCandidate] = []
    seen_documents: set[UUID] = set()
    for item in raw_documents:
        try:
            document_id = UUID(str(item["document_id"]))
            raw_chunk_ids = cast(list[object], item.get("chunk_ids") or [])
            chunk_ids = tuple(UUID(str(value)) for value in raw_chunk_ids)
            score = float(cast(Any, item.get("score") or 0))
        except (KeyError, TypeError, ValueError):
            continue
        seen_documents.add(document_id)
        candidates.append(
            DocumentCandidate(
                document_id,
                score,
                chunk_ids,
                rerank_scores.get(str(document_id)),
            )
        )

    # The original retrieval is an independent, already-reranked safety net.
    # It is appended, never allowed to replace the original-query candidates.
    for candidate in prepared.rerank_candidates or prepared.selected_candidates:
        if candidate.document_id in seen_documents:
            continue
        seen_documents.add(candidate.document_id)
        candidates.append(DocumentCandidate(candidate.document_id, 0.0, (candidate.chunk_id,)))
        if len(candidates) >= settings.m3_rag_full_document_limit:
            break

    candidates = candidates[: settings.m3_rag_full_document_limit]
    return candidates, {
        "query_count": len(queries),
        "query_hashes": [hashlib.sha256(item.text.encode()).hexdigest() for item in queries],
        "candidate_document_ids": [str(item.document_id) for item in candidates],
        "candidate_count": len(candidates),
        "degradations": list(dict.fromkeys(degradations)),
        "document_rerank": document_rerank,
    }
