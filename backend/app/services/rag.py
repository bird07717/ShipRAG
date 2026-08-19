from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import UUID

from minio import Minio

from app.common.errors import ApiError
from app.core.config import Settings
from app.ingestion.embedding import EmbeddingError, EmbeddingProvider, create_embedding_provider
from app.ingestion.models import EmbeddingInput
from app.rag.llm import LlmError, LlmProvider, create_llm_provider
from app.rag.models import (
    ModelSnapshot,
    PreparedRag,
    RerankDocument,
    RetrievalCandidate,
    Source,
)
from app.rag.prompt import build_context, render_prompt
from app.rag.repository import RagRepository
from app.rag.rerank import RerankError, RerankProvider, create_rerank_provider
from app.rag.retrieval import apply_rerank, reciprocal_rank_fusion

EmbeddingFactory = Callable[[Settings], EmbeddingProvider]
LlmFactory = Callable[[Settings, Any], LlmProvider]
RerankFactory = Callable[[Settings, ModelSnapshot], RerankProvider]
_CITATION_PATTERN = re.compile(r"\[S([1-9][0-9]*)\]")


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))


def sse_event(event: str, payload: dict[str, Any]) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n".encode()


class RagService:
    def __init__(
        self,
        repository: RagRepository,
        settings: Settings,
        minio: Minio | None = None,
        *,
        embedding_factory: EmbeddingFactory = create_embedding_provider,
        llm_factory: LlmFactory = create_llm_provider,
        rerank_factory: RerankFactory = create_rerank_provider,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.minio = minio
        self.embedding_factory = embedding_factory
        self.llm_factory = llm_factory
        self.rerank_factory = rerank_factory

    async def prepare(
        self,
        *,
        knowledge_id: UUID,
        conversation_id: UUID | None,
        question: str,
        request_id: str,
        mode: str = "CHAT",
    ) -> PreparedRag:
        started_at = time.perf_counter()
        normalized_question = question.strip()
        if not normalized_question:
            raise ApiError("VALIDATION_ERROR", "问题不能为空", 422)
        if len(normalized_question) > self.settings.m3_question_max_chars:
            raise ApiError("VALIDATION_ERROR", "问题超过长度限制", 422)

        turn = await self.repository.begin_turn(
            knowledge_id=knowledge_id,
            conversation_id=conversation_id,
            question=normalized_question,
            request_id=request_id,
            mode=mode,
        )
        timings = {"prepare_turn_ms": _elapsed_ms(started_at)}
        provider: EmbeddingProvider | None = None
        rerank_provider: RerankProvider | None = None
        try:
            provider = self.embedding_factory(self.settings)
            if provider.provider != "fake" and provider.model_name != turn.embedding_model_name:
                raise EmbeddingError("查询模型与 Active Index 的 Embedding 模型快照不一致")

            async def embed_query() -> list[list[float]]:
                stage_started = time.perf_counter()
                result = await provider.embed([EmbeddingInput(normalized_question)])
                timings["query_embedding_ms"] = _elapsed_ms(stage_started)
                return result

            async def retrieve_bm25() -> list[RetrievalCandidate]:
                stage_started = time.perf_counter()
                result = await self.repository.bm25_search(
                    knowledge_id=knowledge_id,
                    index_id=turn.index_id,
                    query=normalized_question,
                    limit=self.settings.m5_bm25_top_k,
                )
                timings["bm25_retrieval_ms"] = _elapsed_ms(stage_started)
                return result

            embeddings, bm25_candidates = await asyncio.gather(
                embed_query(),
                retrieve_bm25(),
            )
            if len(embeddings) != 1 or len(embeddings[0]) != self.settings.embedding_dimension:
                raise EmbeddingError("查询 Embedding 维度异常")
            vector_started = time.perf_counter()
            vector_candidates = await self.repository.vector_search(
                knowledge_id=knowledge_id,
                index_id=turn.index_id,
                embedding=embeddings[0],
                limit=self.settings.m3_vector_top_k,
            )
            timings["vector_retrieval_ms"] = _elapsed_ms(vector_started)
            fusion_started = time.perf_counter()
            fusion_candidates = reciprocal_rank_fusion(
                vector_candidates,
                bm25_candidates,
                rrf_k=self.settings.m5_rrf_k,
                limit=self.settings.m5_fusion_top_k,
            )
            timings["fusion_ms"] = _elapsed_ms(fusion_started)

            rerank_started = time.perf_counter()
            rerank_result: dict[str, Any]
            if fusion_candidates:
                if turn.rerank is None:
                    raise RerankError("MODEL_NOT_CONFIGURED", "Rerank 模型未配置", degradable=False)
                rerank_provider = self.rerank_factory(self.settings, turn.rerank)
                documents = await self._build_rerank_documents(turn.index_id, fusion_candidates)
                try:
                    outcome = await rerank_provider.rerank(
                        normalized_question, documents, self.settings.m5_rerank_top_n
                    )
                    reranked_candidates = apply_rerank(fusion_candidates, outcome.items)
                    rerank_result = {
                        "status": "PASSED",
                        "degraded": False,
                        "provider": outcome.provider,
                        "model": outcome.model_name,
                        "usage": outcome.usage,
                    }
                except RerankError as exc:
                    if not exc.degradable:
                        raise
                    reranked_candidates = fusion_candidates[: self.settings.m5_rerank_top_n]
                    rerank_result = {
                        "status": "DEGRADED",
                        "degraded": True,
                        "provider": rerank_provider.provider,
                        "model": rerank_provider.model_name,
                        "error_code": exc.code,
                    }
            else:
                reranked_candidates = []
                rerank_result = {
                    "status": "SKIPPED",
                    "degraded": False,
                    "reason": "NO_CANDIDATES",
                }
            timings["rerank_ms"] = _elapsed_ms(rerank_started)

            expansion_started = time.perf_counter()
            expanded_candidates, expansion_result = await self._expand_context_neighbors(
                turn.index_id, reranked_candidates
            )
            timings["context_expansion_ms"] = _elapsed_ms(expansion_started)
            rerank_result["context_expansion"] = expansion_result
            selected = self._select_context(expanded_candidates)
            selected = self._order_context_by_parent(selected)
            context, sources = build_context(selected)

            prompt_started = time.perf_counter()
            prompt = render_prompt(
                turn.prompt_template,
                context=context,
                question=normalized_question,
                history=turn.history,
                max_chars=self.settings.m3_prompt_max_chars,
            )
            timings["prompt_ms"] = _elapsed_ms(prompt_started)
            await self.repository.save_prepared(
                turn,
                vector_candidates=vector_candidates,
                bm25_candidates=bm25_candidates,
                fusion_candidates=fusion_candidates,
                rerank_candidates=reranked_candidates,
                rerank_result=rerank_result,
                selected_context=[source.public_dict() for source in sources],
                prompt=prompt,
                timings=timings,
            )
            return PreparedRag(
                turn=turn,
                prompt=prompt,
                candidates=fusion_candidates,
                selected_candidates=selected,
                sources=sources,
                timings=timings,
                started_at=started_at,
            )
        except ApiError as exc:
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code=exc.code,
                message=exc.message,
                partial_answer="",
                timings=timings,
            )
            raise
        except EmbeddingError as exc:
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code="UPSTREAM_UNAVAILABLE",
                message="查询向量生成失败",
                partial_answer="",
                timings=timings,
            )
            raise ApiError("UPSTREAM_UNAVAILABLE", "查询向量生成失败", 503) from exc
        except RerankError as exc:
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code=exc.code,
                message=exc.public_message,
                partial_answer="",
                timings=timings,
            )
            raise ApiError(exc.code, exc.public_message, 502) from exc
        except Exception:
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code="INTERNAL_ERROR",
                message="RAG 请求准备失败",
                partial_answer="",
                timings=timings,
            )
            raise
        finally:
            if provider is not None:
                await provider.aclose()
            if rerank_provider is not None:
                await rerank_provider.aclose()

    async def _build_rerank_documents(
        self, index_id: UUID, candidates: list[RetrievalCandidate]
    ) -> list[RerankDocument]:
        if self.minio is None:
            return [RerankDocument(candidate.content) for candidate in candidates]
        all_asset_ids = [
            asset_id for candidate in candidates for asset_id in candidate.image_asset_ids
        ]
        assets = await self.repository.get_rerank_image_assets(
            index_id=index_id, image_asset_ids=all_asset_ids
        )
        remaining_bytes = self.settings.m5_rerank_image_byte_budget
        documents: list[RerankDocument] = []
        for candidate in candidates:
            image_bytes: bytes | None = None
            mime_type: str | None = None
            for asset_id in candidate.image_asset_ids[
                : self.settings.m5_rerank_max_images_per_document
            ]:
                asset = assets.get(asset_id)
                if asset is None or remaining_bytes <= 0:
                    continue
                loaded = await asyncio.to_thread(
                    self._download_rerank_image, asset, remaining_bytes
                )
                if loaded is None:
                    continue
                image_bytes = loaded
                mime_type = str(asset["mime_type"])
                remaining_bytes -= len(loaded)
                break
            documents.append(RerankDocument(candidate.content, image_bytes, mime_type))
        return documents

    def _download_rerank_image(self, asset: dict[str, Any], remaining_bytes: int) -> bytes | None:
        assert self.minio is not None
        response = self.minio.get_object(asset["minio_bucket"], asset["minio_object_key"])
        try:
            data = bytes(response.read(remaining_bytes + 1))
            return data if len(data) <= remaining_bytes else None
        finally:
            response.close()
            response.release_conn()

    def _select_context(self, candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]:
        selected: list[RetrievalCandidate] = []
        remaining_tokens = self.settings.m3_context_token_budget
        for candidate in candidates:
            if len(selected) >= self.settings.m3_context_max_chunks:
                break
            normalized_content = self._normalize_context_content(candidate.content)
            redundant_indexes = [
                index
                for index, existing in enumerate(selected)
                if existing.document_id == candidate.document_id
                and (
                    normalized_content in self._normalize_context_content(existing.content)
                    or self._normalize_context_content(existing.content) in normalized_content
                )
            ]
            if redundant_indexes:
                existing_index = redundant_indexes[0]
                existing = selected[existing_index]
                if len(normalized_content) <= len(
                    self._normalize_context_content(existing.content)
                ):
                    continue
                replacement_budget = remaining_tokens + existing.token_count
                if candidate.token_count <= replacement_budget:
                    selected[existing_index] = candidate
                    remaining_tokens = replacement_budget - candidate.token_count
                continue
            if candidate.token_count > remaining_tokens:
                continue
            selected.append(candidate)
            remaining_tokens -= candidate.token_count
        return selected

    async def _expand_context_neighbors(
        self, index_id: UUID, candidates: list[RetrievalCandidate]
    ) -> tuple[list[RetrievalCandidate], dict[str, Any]]:
        expandable = [
            candidate
            for candidate in candidates[: self.settings.m3_context_max_chunks]
            if candidate.parent_id is not None
            and (candidate.suspected_incomplete or candidate.is_procedural)
        ]
        if not expandable:
            return candidates, {
                "status": "SKIPPED",
                "seed_count": 0,
                "requested_neighbor_count": 0,
                "attached_neighbor_count": 0,
                "expansions": [],
            }
        seed_ids = {candidate.chunk_id for candidate in candidates}
        neighbor_ids: list[UUID] = []
        for candidate in expandable:
            for neighbor_id in (candidate.previous_chunk_id, candidate.next_chunk_id):
                if (
                    neighbor_id is not None
                    and neighbor_id not in seed_ids
                    and neighbor_id not in neighbor_ids
                ):
                    neighbor_ids.append(neighbor_id)
        neighbors = (
            await self.repository.get_chunks_by_ids(index_id=index_id, chunk_ids=neighbor_ids)
            if neighbor_ids
            else []
        )
        neighbor_registry = {candidate.chunk_id: candidate for candidate in neighbors}
        expanded: list[RetrievalCandidate] = []
        added: set[UUID] = set()
        expansion_records: list[dict[str, Any]] = []
        for candidate in candidates:
            if candidate.chunk_id not in added:
                expanded.append(candidate)
                added.add(candidate.chunk_id)
            if candidate not in expandable:
                continue
            attached: list[str] = []
            for neighbor_id, sequence_delta in (
                (candidate.previous_chunk_id, -1),
                (candidate.next_chunk_id, 1),
            ):
                neighbor = neighbor_registry.get(neighbor_id) if neighbor_id else None
                if neighbor is None or neighbor.chunk_id in added:
                    continue
                if (
                    neighbor.document_id != candidate.document_id
                    or neighbor.parent_id != candidate.parent_id
                    or candidate.sequence_no is None
                    or neighbor.sequence_no != candidate.sequence_no + sequence_delta
                ):
                    continue
                expanded.append(neighbor)
                added.add(neighbor.chunk_id)
                attached.append(str(neighbor.chunk_id))
            expansion_records.append(
                {
                    "seed_chunk_id": str(candidate.chunk_id),
                    "reasons": [
                        *candidate.incomplete_reasons,
                        *(["PROCEDURAL_CHUNK"] if candidate.is_procedural else []),
                    ],
                    "neighbor_chunk_ids": attached,
                }
            )
        return expanded, {
            "status": "EXPANDED" if neighbors else "UNCHANGED",
            "seed_count": len(expandable),
            "requested_neighbor_count": len(neighbor_ids),
            "attached_neighbor_count": sum(
                len(record["neighbor_chunk_ids"]) for record in expansion_records
            ),
            "expansions": expansion_records,
        }

    @staticmethod
    def _normalize_context_content(content: str) -> str:
        return re.sub(r"\s+", "", content).casefold()

    @staticmethod
    def _order_context_by_parent(
        candidates: list[RetrievalCandidate],
    ) -> list[RetrievalCandidate]:
        group_order: list[UUID] = []
        groups: dict[UUID, list[RetrievalCandidate]] = {}
        ungrouped: list[tuple[int, RetrievalCandidate]] = []
        positions: dict[UUID, int] = {}
        for position, candidate in enumerate(candidates):
            if candidate.parent_id is None:
                ungrouped.append((position, candidate))
                continue
            if candidate.parent_id not in groups:
                group_order.append(candidate.parent_id)
                groups[candidate.parent_id] = []
                positions[candidate.parent_id] = position
            groups[candidate.parent_id].append(candidate)
        blocks: list[tuple[int, list[RetrievalCandidate]]] = [
            (
                positions[parent_id],
                sorted(
                    groups[parent_id],
                    key=lambda item: (
                        item.sequence_no if item.sequence_no is not None else 10**9,
                        str(item.chunk_id),
                    ),
                ),
            )
            for parent_id in group_order
        ]
        blocks.extend((position, [candidate]) for position, candidate in ungrouped)
        return [candidate for _, block in sorted(blocks) for candidate in block]

    async def stream(self, prepared: PreparedRag) -> AsyncIterator[bytes]:
        turn = prepared.turn
        yield sse_event(
            "trace",
            {
                "trace_id": str(turn.trace_id),
                "conversation_id": str(turn.conversation_id),
                "index_id": str(turn.index_id),
            },
        )
        yield sse_event("source", {"sources": [item.public_dict() for item in prepared.sources]})

        provider: LlmProvider | None = None
        llm_started = time.perf_counter()
        try:
            provider = self.llm_factory(self.settings, turn.llm)
            async for delta in provider.stream(prepared.prompt):
                if not delta:
                    continue
                prepared.answer_parts.append(delta)
                yield sse_event("message", {"delta": delta})
            answer = "".join(prepared.answer_parts)
            valid_sources, citation_result = self._validate_citations(answer, prepared.sources)
            prepared.timings["llm_ms"] = _elapsed_ms(llm_started)
            prepared.timings["total_ms"] = _elapsed_ms(prepared.started_at)
            usage = provider.usage
            await self.repository.complete_turn(
                turn,
                answer=answer,
                sources=valid_sources,
                usage=usage,
                citation_result=citation_result,
                timings=prepared.timings,
            )
            yield sse_event(
                "done",
                {
                    "conversation_id": str(turn.conversation_id),
                    "message_id": str(turn.assistant_message_id),
                    "answer": answer,
                    "sources": valid_sources,
                    "usage": usage,
                    "latency_ms": prepared.timings["total_ms"],
                },
            )
        except asyncio.CancelledError:
            prepared.timings["total_ms"] = _elapsed_ms(prepared.started_at)
            task = asyncio.create_task(
                self.repository.fail_turn(
                    turn,
                    status="CANCELLED",
                    code="CLIENT_DISCONNECTED",
                    message="客户端已断开",
                    partial_answer="".join(prepared.answer_parts),
                    timings=prepared.timings,
                )
            )
            await asyncio.shield(task)
            raise
        except LlmError as exc:
            prepared.timings["llm_ms"] = _elapsed_ms(llm_started)
            prepared.timings["total_ms"] = _elapsed_ms(prepared.started_at)
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code=exc.code,
                message=exc.public_message,
                partial_answer="".join(prepared.answer_parts),
                timings=prepared.timings,
            )
            yield sse_event(
                "error",
                {
                    "code": exc.code,
                    "message": exc.public_message,
                    "trace_id": str(turn.trace_id),
                    "retryable": exc.retryable,
                },
            )
        except Exception:
            prepared.timings["total_ms"] = _elapsed_ms(prepared.started_at)
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code="INTERNAL_ERROR",
                message="回答生成失败",
                partial_answer="".join(prepared.answer_parts),
                timings=prepared.timings,
            )
            yield sse_event(
                "error",
                {
                    "code": "INTERNAL_ERROR",
                    "message": "回答生成失败",
                    "trace_id": str(turn.trace_id),
                    "retryable": False,
                },
            )
        finally:
            if provider is not None:
                await provider.aclose()

    @staticmethod
    def _validate_citations(
        answer: str, sources: list[Source]
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        registry = {source.source_id: source for source in sources}
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        invalid: list[str] = []
        for match in _CITATION_PATTERN.finditer(answer):
            source_id = f"S{match.group(1)}"
            if source_id not in registry:
                if source_id not in invalid:
                    invalid.append(source_id)
                continue
            if source_id in seen:
                continue
            seen.add(source_id)
            result.append(registry[source_id].public_dict())
        return result, {
            "citation_missing": len(result) == 0,
            "valid_source_ids": [str(item["source_id"]) for item in result],
            "invalid_source_ids": invalid,
            "registered_source_count": len(sources),
        }
