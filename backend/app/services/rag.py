from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import UUID

from minio import Minio

from app.common.errors import ApiError
from app.common.text import estimate_tokens
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
    normalize_for_match,
)
from app.rag.llm import LlmError, LlmProvider, create_llm_provider
from app.rag.models import (
    ModelSnapshot,
    PreparedRag,
    RerankDocument,
    RetrievalCandidate,
    Source,
)
from app.rag.prompt import (
    build_chunk_qa_prompt_template,
    build_context,
    build_doc_qa_prompt_template,
    build_document_context,
    build_routing_prompt_template,
    render_prompt,
)
from app.rag.query_rewrite import rewrite_query, should_rewrite
from app.rag.repository import RagRepository
from app.rag.rerank import RerankError, RerankProvider, create_rerank_provider
from app.rag.retrieval import apply_rerank, reciprocal_rank_fusion
from app.rag.routing import (
    UNCONFIRMED_MESSAGE,
    ChatResult,
    build_assist_result,
    build_delivery_result,
    post_validate,
)
from app.rag.routing import ContentBlock as RoutingContentBlock

EmbeddingFactory = Callable[[Settings], EmbeddingProvider]
LlmFactory = Callable[[Settings, Any], LlmProvider]
RerankFactory = Callable[[Settings, ModelSnapshot], RerankProvider]
_CITATION_PATTERN = re.compile(r"\[S([1-9][0-9]*)\]")


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))


@dataclass(slots=True)
class _ChatPlan:
    kind: str  # "DELIVER" | "DOC_QA" | "CHUNK_RAG" | "ROUTING"
    prompt: str = ""
    sources: list[Source] = field(default_factory=list)
    delivery: dict[str, Any] | None = None
    routing: dict[str, Any] = field(default_factory=dict)
    require_citations: bool = True
    references: list[dict[str, Any]] = field(default_factory=list)
    fixed_answer: str | None = None
    pending_after: list[dict[str, Any]] | None = None
    clear_pending_after: bool = False


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
        retrieval_overrides: dict[str, int] | None = None,
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
        # Effective retrieval knobs: DB rag_config snapshot from begin_turn,
        # optionally overridden per-call (playground overrides win so the
        # playground keeps working independently of the stored config).
        retrieval = (
            replace(turn.retrieval, **retrieval_overrides)
            if retrieval_overrides
            else turn.retrieval
        )
        timings = {"prepare_turn_ms": _elapsed_ms(started_at)}
        provider: EmbeddingProvider | None = None
        rerank_provider: RerankProvider | None = None
        try:
            provider = self.embedding_factory(self.settings)
            if provider.provider != "fake" and provider.model_name != turn.embedding_model_name:
                raise EmbeddingError("查询模型与 Active Index 的 Embedding 模型快照不一致")

            retrieval_query = normalized_question
            query_rewrite_record: dict[str, str] | None = None
            if (
                mode == "CHAT"
                and self.settings.m3_query_rewrite_enabled
                and should_rewrite(
                    normalized_question,
                    turn.history,
                    max_chars=self.settings.m3_query_rewrite_max_chars,
                )
            ):
                rewrite_started = time.perf_counter()
                rewrite_provider = self.llm_factory(self.settings, turn.llm)
                try:
                    retrieval_query, query_rewrite_record = await rewrite_query(
                        rewrite_provider,
                        question=normalized_question,
                        history=turn.history,
                        max_tokens=self.settings.m3_query_rewrite_max_tokens,
                        timeout_seconds=self.settings.m3_query_rewrite_timeout_seconds,
                        max_chars=self.settings.m3_question_max_chars,
                    )
                finally:
                    await rewrite_provider.aclose()
                timings["query_rewrite_ms"] = _elapsed_ms(rewrite_started)

            async def embed_query() -> list[list[float]]:
                stage_started = time.perf_counter()
                result = await provider.embed([EmbeddingInput(retrieval_query)])
                timings["query_embedding_ms"] = _elapsed_ms(stage_started)
                return result

            async def retrieve_bm25() -> list[RetrievalCandidate]:
                stage_started = time.perf_counter()
                result = await self.repository.bm25_search(
                    knowledge_id=knowledge_id,
                    index_id=turn.index_id,
                    query=retrieval_query,
                    limit=retrieval.bm25_top_k,
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
                limit=retrieval.vector_top_k,
            )
            timings["vector_retrieval_ms"] = _elapsed_ms(vector_started)
            fusion_started = time.perf_counter()
            fusion_candidates = reciprocal_rank_fusion(
                vector_candidates,
                bm25_candidates,
                rrf_k=self.settings.m5_rrf_k,
                limit=retrieval.fusion_top_k,
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
                        retrieval_query, documents, retrieval.rerank_top_n
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
                    reranked_candidates = fusion_candidates[: retrieval.rerank_top_n]
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
            if query_rewrite_record is not None:
                # Persisted inside rag_trace.rerank_result for observability;
                # only attached when a rewrite was actually attempted.
                rerank_result["query_rewrite"] = query_rewrite_record

            expansion_started = time.perf_counter()
            expanded_candidates, expansion_result = await self._expand_context_neighbors(
                turn.index_id, reranked_candidates, max_chunks=retrieval.context_max_chunks
            )
            timings["context_expansion_ms"] = _elapsed_ms(expansion_started)
            rerank_result["context_expansion"] = expansion_result
            selected = self._select_context(
                expanded_candidates, max_chunks=retrieval.context_max_chunks
            )
            selected = self._order_context_by_parent(selected)

            prompt_started = time.perf_counter()
            retained = selected
            trimmed_count = 0
            while True:
                context, sources = build_context(retained)
                try:
                    prompt = render_prompt(
                        turn.prompt_template,
                        context=context,
                        question=normalized_question,
                        history=turn.history,
                        max_chars=self.settings.m3_prompt_max_chars,
                    )
                except ApiError as exc:
                    if exc.code != "PROMPT_TOO_LARGE" or not retained:
                        raise
                else:
                    if estimate_tokens(prompt) <= self.settings.m3_prompt_token_budget:
                        break
                    if not retained:
                        raise ApiError("PROMPT_TOO_LARGE", "Prompt 超出服务端预算", 422)
                # Degrade by dropping the lowest-ranked chunk instead of
                # failing the turn: chunks are ranked best-first, so the tail
                # is the least valuable context.
                retained = retained[:-1]
                trimmed_count += 1
            if trimmed_count:
                timings["prompt_trimmed_chunks"] = trimmed_count
                selected = retained
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
                rerank_candidates=reranked_candidates,
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

    def _select_context(
        self,
        candidates: list[RetrievalCandidate],
        *,
        max_chunks: int | None = None,
    ) -> list[RetrievalCandidate]:
        chunk_limit = max_chunks if max_chunks is not None else self.settings.m3_context_max_chunks
        selected: list[RetrievalCandidate] = []
        remaining_tokens = self.settings.m3_context_token_budget
        for candidate in candidates:
            if len(selected) >= chunk_limit:
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
        self,
        index_id: UUID,
        candidates: list[RetrievalCandidate],
        *,
        max_chunks: int | None = None,
    ) -> tuple[list[RetrievalCandidate], dict[str, Any]]:
        chunk_limit = max_chunks if max_chunks is not None else self.settings.m3_context_max_chunks
        expandable = [
            candidate
            for candidate in candidates[:chunk_limit]
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

    async def chat_stream(
        self,
        *,
        knowledge_id: UUID,
        conversation_id: UUID | None,
        question: str,
        request_id: str,
        product_name: str = "",
        scope_description: str = "",
    ) -> AsyncIterator[bytes]:
        prepared = await self.prepare(
            knowledge_id=knowledge_id,
            conversation_id=conversation_id,
            question=question,
            request_id=request_id,
            mode="CHAT",
        )
        turn = prepared.turn
        plan, plan_error = await self._safely_plan_chat_turn(
            prepared, question, product_name, scope_description
        )
        if plan_error is not None:
            yield sse_event(
                "error",
                {
                    "code": plan_error.code,
                    "message": plan_error.message,
                    "trace_id": str(turn.trace_id),
                    "retryable": False,
                },
            )
            return

        if plan.kind == "DELIVER" or plan.fixed_answer is not None:
            async for frame in self._emit_final_chat_turn(prepared, plan, usage={}):
                yield frame
            return

        yield sse_event(
            "trace",
            {
                "trace_id": str(turn.trace_id),
                "conversation_id": str(turn.conversation_id),
                "index_id": str(turn.index_id),
            },
        )
        yield sse_event("source", {"sources": [s.public_dict() for s in plan.sources]})

        provider: LlmProvider | None = None
        llm_started = time.perf_counter()
        mode_buffer = ""
        # DOC_QA and degraded CHUNK_RAG answers carry a leading [MODE:...] tag
        # to strip; routing answers stream verbatim.
        mode_detected = plan.kind == "ROUTING"
        try:
            provider = self.llm_factory(self.settings, turn.llm)
            prompt = plan.prompt
            async for delta in provider.stream(prompt):
                if not delta:
                    continue
                prepared.answer_parts.append(delta)
                if not mode_detected:
                    mode_buffer += delta
                    if "]" in mode_buffer:
                        mode_tag_end = mode_buffer.find("]") + 1
                        after_tag = mode_buffer[mode_tag_end:].lstrip("\n")
                        mode_detected = True
                        if after_tag:
                            yield sse_event("message", {"delta": after_tag})
                    elif len(mode_buffer) > 200:
                        mode_detected = True
                        yield sse_event("message", {"delta": mode_buffer})
                    continue
                yield sse_event("message", {"delta": delta})

            raw_answer = "".join(prepared.answer_parts)
            result = self._finalize_chat_result(plan, raw_answer)

            prepared.timings["llm_ms"] = _elapsed_ms(llm_started)
            prepared.timings["total_ms"] = _elapsed_ms(prepared.started_at)
            usage = provider.usage

            await self._apply_post_state(plan, turn, question)
            await self.repository.complete_turn(
                turn,
                answer=result.answer,
                sources=result.valid_sources,
                usage=usage,
                citation_result=result.citation_result,
                timings=prepared.timings,
                prompt=prompt,
                doc_routing=plan.routing,
            )

            yield sse_event(
                "done",
                {
                    "conversation_id": str(turn.conversation_id),
                    "message_id": str(turn.assistant_message_id),
                    "answer": result.answer,
                    "sources": result.valid_sources,
                    "usage": usage,
                    "latency_ms": prepared.timings["total_ms"],
                    "response_type": result.response_type.value,
                    "answer_mode": result.answer_mode.value if result.answer_mode else None,
                    "disclaimer": result.disclaimer,
                    "content": [block.to_dict() for block in result.content],
                    "references": plan.references,
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

    async def generate(
        self,
        *,
        knowledge_id: UUID,
        conversation_id: UUID | None,
        question: str,
        request_id: str,
        product_name: str = "",
        scope_description: str = "",
    ) -> dict[str, Any]:
        prepared = await self.prepare(
            knowledge_id=knowledge_id,
            conversation_id=conversation_id,
            question=question,
            request_id=request_id,
            mode="CHAT",
        )
        turn = prepared.turn
        plan, plan_error = await self._safely_plan_chat_turn(
            prepared, question, product_name, scope_description
        )
        if plan_error is not None:
            raise plan_error

        if plan.kind == "DELIVER" or plan.fixed_answer is not None:
            prepared.timings["total_ms"] = _elapsed_ms(prepared.started_at)
            if plan.kind == "DELIVER" and plan.delivery is not None:
                result = plan.delivery["result"]
            else:
                result = build_assist_result(plan.fixed_answer or "")
            await self._apply_post_state(plan, turn, question)
            await self.repository.complete_turn(
                turn,
                answer=result.answer,
                sources=result.valid_sources,
                usage={},
                citation_result=result.citation_result,
                timings=prepared.timings,
                prompt=None,
                doc_routing=plan.routing,
            )
            return self._chat_payload(
                turn, result, plan.references, {}, prepared.timings["total_ms"]
            )

        provider: LlmProvider | None = None
        llm_started = time.perf_counter()
        try:
            provider = self.llm_factory(self.settings, turn.llm)
            prompt = plan.prompt
            answer_parts: list[str] = []
            async for delta in provider.stream(prompt):
                if delta:
                    answer_parts.append(delta)
            raw_answer = "".join(answer_parts)
            result = self._finalize_chat_result(plan, raw_answer)
            prepared.timings["llm_ms"] = _elapsed_ms(llm_started)
            prepared.timings["total_ms"] = _elapsed_ms(prepared.started_at)
            usage = provider.usage
            await self._apply_post_state(plan, turn, question)
            await self.repository.complete_turn(
                turn,
                answer=result.answer,
                sources=result.valid_sources,
                usage=usage,
                citation_result=result.citation_result,
                timings=prepared.timings,
                prompt=prompt,
                doc_routing=plan.routing,
            )
            return self._chat_payload(
                turn, result, plan.references, usage, prepared.timings["total_ms"]
            )
        except LlmError as exc:
            prepared.timings["llm_ms"] = _elapsed_ms(llm_started)
            prepared.timings["total_ms"] = _elapsed_ms(prepared.started_at)
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code=exc.code,
                message=exc.public_message,
                partial_answer="",
                timings=prepared.timings,
            )
            raise ApiError(exc.code, exc.public_message, 504 if exc.retryable else 503) from exc
        except ApiError:
            raise
        except Exception:
            prepared.timings["total_ms"] = _elapsed_ms(prepared.started_at)
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code="INTERNAL_ERROR",
                message="回答生成失败",
                partial_answer="",
                timings=prepared.timings,
            )
            raise
        finally:
            if provider is not None:
                await provider.aclose()

    async def _safely_plan_chat_turn(
        self,
        prepared: PreparedRag,
        question: str,
        product_name: str,
        scope_description: str,
    ) -> tuple[_ChatPlan, ApiError | None]:
        turn = prepared.turn
        try:
            plan = await self._plan_chat_turn(
                prepared=prepared,
                question=question.strip(),
                product_name=product_name,
                scope_description=scope_description,
            )
            return plan, None
        except ApiError as exc:
            prepared.timings["total_ms"] = _elapsed_ms(prepared.started_at)
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code=exc.code,
                message=exc.message,
                partial_answer="",
                timings=prepared.timings,
            )
            return _ChatPlan(kind="ROUTING"), ApiError(exc.code, exc.message, exc.status_code)
        except Exception:
            prepared.timings["total_ms"] = _elapsed_ms(prepared.started_at)
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code="INTERNAL_ERROR",
                message="文档路由失败",
                partial_answer="",
                timings=prepared.timings,
            )
            return _ChatPlan(kind="ROUTING"), ApiError("INTERNAL_ERROR", "文档路由失败", 500)

    async def _plan_chat_turn(
        self,
        *,
        prepared: PreparedRag,
        question: str,
        product_name: str,
        scope_description: str,
    ) -> _ChatPlan:
        turn = prepared.turn
        candidates = prepared.rerank_candidates or prepared.candidates
        decision = decide_doc_routing(
            question=question,
            candidates=candidates,
            focus_document_id=turn.focus_document_id,
            chat_context=turn.chat_context,
            t_high=self.settings.m3_doc_agg_t_high,
            t_low=self.settings.m3_doc_agg_t_low,
            ratio=self.settings.m3_doc_agg_ratio,
            min_hits=self.settings.m3_doc_agg_min_hits,
            max_hits=self.settings.m3_doc_agg_max_hits,
            stay_score=self.settings.m3_doc_stay_score,
            switch_gap=self.settings.m3_doc_switch_gap,
            lock_best_floor=self.settings.m3_doc_lock_best_floor,
        )
        had_pending = bool(turn.chat_context.get("pending_options"))

        if decision.action == ACTION_DELIVER and decision.document_id is not None:
            document = await self.repository.get_document_blocks(
                knowledge_id=turn.knowledge_id,
                index_id=turn.index_id,
                document_id=decision.document_id,
            )
            await self.repository.set_conversation_focus(turn.conversation_id, decision.document_id)
            delivery = self._build_delivery_payload(document)
            return _ChatPlan(
                kind="DELIVER",
                delivery=delivery,
                routing=decision.to_dict(),
                references=delivery["references"],
            )

        clear_pending_after = had_pending
        catalog: list[dict[str, Any]] | None = None

        # Catalog-title resolution: the user explicitly names a document
        # (typically confirming a previous routing suggestion verbatim).
        # Delivering it directly closes the clarify loop instead of asking
        # the same question again. Never overrides STAY: a title-shaped
        # follow-up inside the focus document should be answered by DOC_QA.
        if decision.action in (ACTION_NO_MATCH, ACTION_CLARIFY, ACTION_OFFER_SWITCH):
            catalog = await self.repository.list_kb_documents(turn.knowledge_id)
            matched = self._match_catalog_title(question, catalog)
            if matched is not None:
                matched_id = UUID(str(matched["document_id"]))
                document = await self.repository.get_document_blocks(
                    knowledge_id=turn.knowledge_id,
                    index_id=turn.index_id,
                    document_id=matched_id,
                )
                await self.repository.set_conversation_focus(turn.conversation_id, matched_id)
                delivery = self._build_delivery_payload(document)
                routing = decision.to_dict()
                routing["resolved_from"] = "TITLE_MATCH"
                return _ChatPlan(
                    kind="DELIVER",
                    delivery=delivery,
                    routing=routing,
                    references=delivery["references"],
                    clear_pending_after=clear_pending_after,
                )

        if decision.action == ACTION_STAY and turn.focus_document_id is not None:
            document = await self.repository.get_document_blocks(
                knowledge_id=turn.knowledge_id,
                index_id=turn.index_id,
                document_id=turn.focus_document_id,
            )
            try:
                context, sources = build_document_context(document)
                prompt = render_prompt(
                    build_doc_qa_prompt_template(product_name, scope_description),
                    context=context,
                    question=question,
                    history=turn.history,
                    max_chars=self.settings.m3_prompt_max_chars,
                )
            except ApiError as exc:
                if exc.code != "PROMPT_TOO_LARGE":
                    raise
                # Degrade to chunk retrieval instead of failing: oversized
                # focus documents must not lock the conversation into
                # repeated 422s. The chunk variant of the QA prompt keeps the
                # mode-tag machinery (incl. PRODUCT_GENERAL general answers)
                # available on this path too.
                chunk_context, _chunk_sources = build_context(prepared.selected_candidates)
                try:
                    degraded_prompt = render_prompt(
                        build_chunk_qa_prompt_template(product_name, scope_description),
                        context=chunk_context,
                        question=question,
                        history=turn.history,
                        max_chars=self.settings.m3_prompt_max_chars,
                    )
                except ApiError:
                    degraded_prompt = prepared.prompt
                routing = decision.to_dict()
                routing["degraded_to"] = "CHUNK_RAG"
                routing["degradation_reason"] = "DOCUMENT_EXCEEDS_PROMPT_BUDGET"
                return _ChatPlan(
                    kind="CHUNK_RAG",
                    prompt=degraded_prompt,
                    sources=prepared.sources,
                    routing=routing,
                    require_citations=True,
                    references=[
                        self._document_reference(document["document_id"], document["title"])
                    ],
                    clear_pending_after=clear_pending_after,
                )
            return _ChatPlan(
                kind="DOC_QA",
                prompt=prompt,
                sources=sources,
                routing=decision.to_dict(),
                require_citations=False,
                references=[self._document_reference(document["document_id"], document["title"])],
                clear_pending_after=clear_pending_after,
            )

        if decision.action == ACTION_CLARIFY:
            context = "候选文档：\n" + self._render_doc_list(decision.pending_options)
            prompt = render_prompt(
                build_routing_prompt_template(product_name, scope_description),
                context=context,
                question=question,
                history=turn.history,
                max_chars=self.settings.m3_prompt_max_chars,
            )
            return _ChatPlan(
                kind="ROUTING",
                prompt=prompt,
                routing=decision.to_dict(),
                pending_after=decision.pending_options,
            )

        if decision.action == ACTION_OFFER_SWITCH and turn.focus_document_id is not None:
            current = await self.repository.get_document_source(turn.focus_document_id)
            target = decision.pending_options[0] if decision.pending_options else {}
            context = (
                "当前文档：《"
                + str(current.get("display_name") or current.get("filename", ""))
                + "》\n"
                "候选文档：\n" + self._render_doc_list([target] if target else [])
            )
            prompt = render_prompt(
                build_routing_prompt_template(product_name, scope_description),
                context=context,
                question=question,
                history=turn.history,
                max_chars=self.settings.m3_prompt_max_chars,
            )
            return _ChatPlan(
                kind="ROUTING",
                prompt=prompt,
                routing=decision.to_dict(),
                pending_after=decision.pending_options,
            )

        if catalog is None:
            catalog = await self.repository.list_kb_documents(turn.knowledge_id)
        routing = decision.to_dict()
        if not catalog:
            return _ChatPlan(
                kind="ROUTING",
                routing=routing,
                fixed_answer=UNCONFIRMED_MESSAGE,
                clear_pending_after=clear_pending_after,
            )
        context = "文档目录：\n" + self._render_doc_list(catalog)
        prompt = render_prompt(
            build_routing_prompt_template(product_name, scope_description),
            context=context,
            question=question,
            history=turn.history,
            max_chars=self.settings.m3_prompt_max_chars,
        )
        return _ChatPlan(
            kind="ROUTING",
            prompt=prompt,
            routing=routing,
            clear_pending_after=clear_pending_after,
        )

    def _build_delivery_payload(self, document: dict[str, Any]) -> dict[str, Any]:
        max_tokens = self.settings.m3_doc_delivery_max_tokens
        blocks: list[RoutingContentBlock] = []
        image_asset_ids: list[str] = []
        used_tokens = 0
        truncated = False
        for element in document.get("elements", []):
            if element.get("element_type") == "IMAGE":
                asset_id = element.get("image_asset_id")
                if asset_id is not None:
                    image_asset_ids.append(str(asset_id))
                    if used_tokens >= max_tokens:
                        truncated = True
                        break
                    blocks.append(
                        RoutingContentBlock(
                            type="image",
                            source_id="S1",
                            image_asset_id=str(asset_id),
                        )
                    )
                    used_tokens += 1
                elif element.get("content"):
                    tokens = estimate_tokens(str(element["content"]))
                    if used_tokens + tokens > max_tokens:
                        truncated = True
                        break
                    blocks.append(RoutingContentBlock(type="text", text=str(element["content"])))
                    used_tokens += tokens
                continue
            if not element.get("content"):
                continue
            tokens = estimate_tokens(str(element["content"]))
            if used_tokens + tokens > max_tokens:
                truncated = True
                break
            blocks.append(RoutingContentBlock(type="text", text=str(element["content"])))
            used_tokens += tokens
        if truncated:
            blocks.append(
                RoutingContentBlock(
                    type="text",
                    text="（文档较长，以上内容已截断，完整内容请下载原文档。）",
                )
            )
        summary = "已为你找到《" + document["title"] + "》，以下是文档完整内容："
        document_source = {
            "source_id": "S1",
            "document_id": document["document_id"],
            "document": document["title"],
            "section_path": [],
            "page": None,
            "element_ids": [],
            "chunk_id": None,
            "image_asset_ids": image_asset_ids,
        }
        result = build_delivery_result(summary=summary, blocks=blocks, document=document_source)
        return {
            "result": result,
            "document_source": document_source,
            "references": [self._document_reference(document["document_id"], document["title"])],
        }

    async def _emit_final_chat_turn(
        self, prepared: PreparedRag, plan: _ChatPlan, *, usage: dict[str, Any]
    ) -> AsyncIterator[bytes]:
        turn = prepared.turn
        if plan.kind == "DELIVER" and plan.delivery is not None:
            result = plan.delivery["result"]
        else:
            result = build_assist_result(plan.fixed_answer or "")
        prepared.timings["total_ms"] = _elapsed_ms(prepared.started_at)
        yield sse_event(
            "trace",
            {
                "trace_id": str(turn.trace_id),
                "conversation_id": str(turn.conversation_id),
                "index_id": str(turn.index_id),
            },
        )
        yield sse_event("source", {"sources": result.valid_sources})
        await self._apply_post_state(plan, turn, "")
        await self.repository.complete_turn(
            turn,
            answer=result.answer,
            sources=result.valid_sources,
            usage=usage,
            citation_result=result.citation_result,
            timings=prepared.timings,
            prompt=None,
            doc_routing=plan.routing,
        )
        yield sse_event(
            "done",
            {
                "conversation_id": str(turn.conversation_id),
                "message_id": str(turn.assistant_message_id),
                "answer": result.answer,
                "sources": result.valid_sources,
                "usage": usage,
                "latency_ms": prepared.timings["total_ms"],
                "response_type": result.response_type.value,
                "answer_mode": result.answer_mode.value if result.answer_mode else None,
                "disclaimer": result.disclaimer,
                "content": [block.to_dict() for block in result.content],
                "references": plan.references,
            },
        )

    async def _apply_post_state(self, plan: _ChatPlan, turn: Any, question: str) -> None:
        if plan.pending_after:
            await self.repository.set_conversation_pending(
                turn.conversation_id,
                pending_options=plan.pending_after,
                pending_query=question,
            )
        elif plan.clear_pending_after:
            await self.repository.clear_conversation_pending(turn.conversation_id)

    def _finalize_chat_result(self, plan: _ChatPlan, raw_answer: str) -> ChatResult:
        if plan.kind in ("DOC_QA", "CHUNK_RAG"):
            source_dicts = [s.public_dict() for s in plan.sources]
            return post_validate(
                raw_answer,
                source_dicts,
                True,
                require_citations=plan.require_citations,
            )
        return build_assist_result(raw_answer)

    @staticmethod
    def _render_doc_list(docs: list[dict[str, Any]]) -> str:
        return "\n".join(
            str(index) + ". 《" + str(doc.get("title", "")) + "》"
            for index, doc in enumerate(docs, start=1)
        )

    @staticmethod
    def _match_catalog_title(
        question: str, catalog: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Return the catalog document whose title contains the question.

        The containment direction (normalized question inside normalized
        title) plus a minimum length guard keeps this to explicit naming:
        short generic replies cannot match, and long questions cannot be
        contained in short titles. Trailing question marks are stripped so
        "FFC如何检查接线盒？" (an offered starter question) behaves the same
        as typing the title directly.
        """
        normalized_question = normalize_for_match(question).rstrip("？?。.!！;；,，")
        if len(normalized_question) < 4:
            return None
        for doc in catalog:
            document_id = doc.get("document_id")
            if not document_id:
                continue
            title = normalize_for_match(str(doc.get("title", "")))
            if title and normalized_question in title:
                return doc
        return None

    @staticmethod
    def _document_reference(document_id: Any, title: str) -> dict[str, Any]:
        doc_id = str(document_id)
        return {
            "document_id": doc_id,
            "title": title,
            "section_paths": [],
            "source_ids": [],
            "download_url": "/api/v1/documents/" + doc_id + "/content?download=true",
        }

    @staticmethod
    def _chat_payload(
        turn: Any,
        result: ChatResult,
        references: list[dict[str, Any]],
        usage: dict[str, Any],
        total_ms: int,
    ) -> dict[str, Any]:
        return {
            "message_id": str(turn.assistant_message_id),
            "conversation_id": str(turn.conversation_id),
            "trace_id": str(turn.trace_id),
            "response_type": result.response_type.value,
            "answer_mode": result.answer_mode.value if result.answer_mode else None,
            "answer": result.answer,
            "disclaimer": result.disclaimer,
            "content": [block.to_dict() for block in result.content],
            "references": references,
            "usage": usage,
            "latency_ms": total_ms,
        }

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
