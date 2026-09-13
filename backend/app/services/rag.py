from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import UUID

from minio import Minio

from app.common.errors import SERVER_BUSY_MESSAGE, ApiError, user_safe_chat_message
from app.common.text import (
    emphasize_document_title_mentions,
    estimate_tokens,
    markdown_document_title,
    strip_leading_document_heading,
)
from app.core.config import Settings
from app.ingestion.embedding import EmbeddingError, EmbeddingProvider, create_embedding_provider
from app.ingestion.models import EmbeddingInput
from app.rag.answer_policy import (
    DEFAULT_PRODUCT_SCOPE,
    POLICY_VERSION,
    AnswerStyle,
    classify_answer_style,
    normalize_scope_description,
    resume_issue_query,
)
from app.rag.doc_router import (
    ACTION_CLARIFY,
    ACTION_DELIVER,
    ACTION_NO_MATCH,
    ACTION_OFFER_SWITCH,
    ACTION_STAY,
    decide_doc_routing,
    normalize_for_match,
)
from app.rag.first_turn import protect_aggregation, single_strong_document, title_recall_terms
from app.rag.llm import LlmError, LlmProvider, create_llm_provider
from app.rag.models import (
    ModelSnapshot,
    PreparedRag,
    RerankDocument,
    RetrievalCandidate,
    Source,
    Turn,
)
from app.rag.presentation import DomainAssistTextStream, ModelTextStream, clean_model_text
from app.rag.prompt import (
    build_chunk_qa_prompt_template,
    build_context,
    build_doc_qa_prompt_template,
    build_document_context,
    build_domain_assist_prompt_template,
    build_routing_prompt_template,
    render_prompt,
)
from app.rag.query_rewrite import (
    document_followup_excerpt,
    is_elliptical_retry,
    rewrite_query,
    should_rewrite,
)
from app.rag.repository import RagRepository
from app.rag.rerank import RerankError, RerankProvider, create_rerank_provider
from app.rag.retrieval import apply_rerank, merge_expanded_candidates, reciprocal_rank_fusion
from app.rag.routing import (
    UNCONFIRMED_MESSAGE,
    ChatResult,
    ResponseType,
    build_assist_result,
    build_delivery_result,
    post_validate,
)
from app.rag.routing import (
    ContentBlock as RoutingContentBlock,
)
from app.rag.synonyms import bm25_expansion_terms
from app.rag.welcome import build_suggestions

EmbeddingFactory = Callable[[Settings], EmbeddingProvider]
LlmFactory = Callable[[Settings, Any], LlmProvider]
RerankFactory = Callable[[Settings, ModelSnapshot], RerankProvider]
_CITATION_PATTERN = re.compile(r"\[S([1-9][0-9]*)\]")
# Topic-change announcements carry no document intent: routing them through
# DOC_QA burns a full LLM call on the whole focus document (tens of seconds)
# only to produce a canned refusal that misreads the intent. The anchors and
# the length guard keep longer sentences containing these phrases (e.g.
# "帮我换一个问题：LRU怎么换") on the normal path.
_META_ACK_PATTERN = re.compile(r"^(?:换一?个(?:问题|话题)|算了|先?不问了)[呀啊吧。！!，,\s]*$")
_META_ACK_MESSAGE = "好的，请问您想了解什么？我将基于产品文档为您解答。"
_KEYBOARD_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")


def _is_fact_question(question: str) -> bool:
    """Conservative opt-in experiment after a document is already resolved."""
    if re.search(r"步骤|流程|全文|完整|如何|怎么|怎样", question):
        return False
    return len(question) <= 120 and bool(re.search(r"在哪里|在哪个|多少|是什么|为什么", question))


def _is_explanation_question(question: str) -> bool:
    if len(question) > 240 or re.search(r"全文|原文|完整|全部|所有|操作步骤|操作流程", question):
        return False
    return bool(
        re.search(
            r"为什么|为何|原因|原理|怎么理解|如何理解|意味着|说明什么|会怎样|会怎么样"
            r"|有什么影响|有何影响|有什么区别|分别解决什么|还差多少",
            question,
        )
    )


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))


def _unexpected_detail(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:500]


def _input_guard_reason(question: str) -> str | None:
    """Reject only obvious low-information noise before model retrieval."""
    normalized = "".join(question.split()).casefold()
    if not normalized:
        return "EMPTY"
    if not any(character.isalnum() for character in normalized):
        return "SYMBOL_ONLY"
    if len(normalized) == 1:
        return "SINGLE_CHARACTER"
    alphanumeric = "".join(character for character in normalized if character.isalnum())
    if len(alphanumeric) >= 3 and len(set(alphanumeric)) == 1:
        return "REPEATED_CHARACTER"
    if len(alphanumeric) >= 5 and any(alphanumeric in row for row in _KEYBOARD_ROWS):
        return "KEYBOARD_SEQUENCE"
    return None


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
    # Clickable retry questions for NO_MATCH routing turns: shown by the
    # frontend after the answer instead of leaving the user at a dead end.
    fallback_suggestions: list[str] = field(default_factory=list)


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

    def _prompt_scope(
        self,
        prepared: PreparedRag,
        product_name: str,
        scope_description: str,
    ) -> tuple[str, str]:
        if self.settings.m3_rag_tech_assist_mode == "enabled":
            return (
                prepared.turn.knowledge_name or product_name,
                prepared.turn.scope_description or scope_description or DEFAULT_PRODUCT_SCOPE,
            )
        return product_name, scope_description or DEFAULT_PRODUCT_SCOPE

    def _select_query_embedding_provider(self, turn: Turn) -> EmbeddingProvider:
        """Pick the query embedder that matches the active index's model.

        Preference order: admin-managed DB snapshot (carries the DB-stored
        key/base_url), then env settings.  Neither matching raises later in
        prepare() with the frozen-snapshot mismatch error.  Custom injected
        factories only accept settings, so the snapshot path is limited to
        the production default factory.
        """
        if turn.embedding is not None and self.embedding_factory is create_embedding_provider:
            try:
                db_provider = create_embedding_provider(self.settings, turn.embedding)
            except EmbeddingError:
                # A snapshot without its own secret may still use the
                # provider-specific environment secret below.
                pass
            else:
                if db_provider.provider == "fake" or (
                    db_provider.model_name == turn.embedding_model_name
                ):
                    return db_provider
        return self.embedding_factory(self.settings)

    async def prepare(
        self,
        *,
        knowledge_id: UUID,
        conversation_id: UUID | None,
        question: str,
        request_id: str,
        mode: str = "CHAT",
        user_id: str | None = None,
        retrieval_overrides: dict[str, int] | None = None,
        reuse_user_message_id: UUID | None = None,
        reuse_assistant_message_id: UUID | None = None,
        parent_trace_id: UUID | None = None,
        fallback_reason: str | None = None,
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
            user_id=user_id,
            reuse_user_message_id=reuse_user_message_id,
            reuse_assistant_message_id=reuse_assistant_message_id,
            parent_trace_id=parent_trace_id,
            fallback_reason=fallback_reason,
        )
        policy_question = normalized_question
        issue_resumed = False
        if self.settings.m3_rag_tech_assist_mode == "enabled":
            policy_question, issue_resumed = resume_issue_query(
                normalized_question, turn.chat_context
            )
        input_guard_reason = (
            None
            if turn.chat_context.get("pending_options") or issue_resumed
            else _input_guard_reason(normalized_question)
        )
        if input_guard_reason is not None:
            return PreparedRag(
                turn=turn,
                prompt="",
                candidates=[],
                selected_candidates=[],
                sources=[],
                timings={"input_guard_ms": _elapsed_ms(started_at)},
                started_at=started_at,
                input_guard_reason=input_guard_reason,
                policy_question=policy_question,
                issue_resumed=issue_resumed,
            )
        # The timings dict is created here — not inside _prepare_retrieval —
        # so the turn-deadline handler below can merge the stage timings
        # accumulated before the hang into the failure record (which stage
        # stalled is the key post-mortem fact).
        timings: dict[str, Any] = {"prepare_turn_ms": _elapsed_ms(started_at)}
        # One overall interactive deadline per turn: a hung upstream
        # (embedding / rerank / LLM) must never hold a chat request for the
        # provider-level 90-120s timeouts. Bounded failure is better UX
        # than an unbounded wait, and fail_turn keeps the trace honest.
        try:
            async with asyncio.timeout(self.settings.m3_turn_deadline_seconds):
                prepared = await self._prepare_retrieval(
                    turn=turn,
                    started_at=started_at,
                    normalized_question=policy_question,
                    mode=mode,
                    retrieval_overrides=retrieval_overrides,
                    timings=timings,
                )
                prepared.policy_question = policy_question
                prepared.issue_resumed = issue_resumed
                return prepared
        except TimeoutError as exc:
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code="TURN_DEADLINE_EXCEEDED",
                message="本轮处理超过总时限",
                partial_answer="",
                timings={
                    "turn_deadline_seconds": int(self.settings.m3_turn_deadline_seconds * 1000),
                    **timings,
                },
                stage="turn_deadline",
                retryable=True,
            )
            raise ApiError("TURN_DEADLINE_EXCEEDED", "本次请求处理超时，请稍后重试", 504) from exc

    async def _prepare_retrieval(
        self,
        *,
        turn: Turn,
        started_at: float,
        normalized_question: str,
        mode: str,
        retrieval_overrides: dict[str, int] | None,
        timings: dict[str, Any],
    ) -> PreparedRag:
        # Effective retrieval knobs: DB rag_config snapshot from begin_turn,
        # optionally overridden per-call (playground overrides win so the
        # playground keeps working independently of the stored config).
        retrieval = (
            replace(turn.retrieval, **retrieval_overrides)
            if retrieval_overrides
            else turn.retrieval
        )
        turn = replace(turn, retrieval=retrieval)
        provider: EmbeddingProvider | None = None
        rerank_provider: RerankProvider | None = None
        try:
            # Query-side embedding must match the model the active index was
            # built with.  Prefer the admin-managed DB snapshot when it
            # matches; fall back to env settings (legacy default, and the
            # safety net while a post-config-change rebuild is in flight).
            provider = self._select_query_embedding_provider(turn)
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
                if (
                    query_rewrite_record.get("status") != "REWRITTEN"
                    and turn.focus_document_id is not None
                    and is_elliptical_retry(normalized_question)
                ):
                    try:
                        focus = await self.repository.get_document_source(turn.focus_document_id)
                    except ApiError as exc:
                        if exc.code != "DOCUMENT_NOT_FOUND":
                            raise
                    else:
                        title = str(focus.get("display_name") or focus.get("filename") or "")
                        if title and str(focus.get("knowledge_id")) == str(turn.knowledge_id):
                            retrieval_query = title + "：" + normalized_question
                            query_rewrite_record = {
                                **query_rewrite_record,
                                "fallback": "FOCUS_TITLE",
                                "rewritten": retrieval_query,
                            }
                timings["query_rewrite_ms"] = _elapsed_ms(rewrite_started)

            async def embed_query() -> list[list[float]]:
                # Fast-fail below the turn deadline, with one retry:
                # embedding is idempotent and cheap, so a transient upstream
                # stall (observed 2026-09-01) is masked by the retry instead
                # of eating the whole turn budget.
                attempts = 1 + max(0, self.settings.m3_query_embedding_retries)
                for attempt in range(1, attempts + 1):
                    stage_started = time.perf_counter()
                    try:
                        async with asyncio.timeout(
                            self.settings.m3_query_embedding_timeout_seconds
                        ):
                            result = await provider.embed([EmbeddingInput(retrieval_query)])
                    except TimeoutError as exc:
                        timings["query_embedding_ms"] = _elapsed_ms(stage_started)
                        if attempt >= attempts:
                            timings["query_embedding_timeout"] = True
                            limit = self.settings.m3_query_embedding_timeout_seconds
                            raise EmbeddingError(
                                f"查询向量生成超时（单次上限 {limit:.0f}s，共尝试 {attempts} 次）"
                            ) from exc
                        continue
                    timings["query_embedding_ms"] = _elapsed_ms(stage_started)
                    if attempt > 1:
                        timings["query_embedding_retries"] = attempt - 1
                    return result
                raise AssertionError("unreachable")

            async def retrieve_bm25() -> list[RetrievalCandidate]:
                stage_started = time.perf_counter()
                result = await self.repository.bm25_search(
                    knowledge_id=turn.knowledge_id,
                    index_id=turn.index_id,
                    query=retrieval_query,
                    limit=retrieval.bm25_top_k,
                )
                timings["bm25_retrieval_ms"] = _elapsed_ms(stage_started)
                return result

            async def retrieve_expansions() -> list[RetrievalCandidate]:
                # Domain synonyms widen recall for paraphrases ("如何检查"
                # never sees 《如何手动自检VDR》 through exact tokens, and the
                # vector channel ranks it too low). Expansion candidates do
                # NOT compete in RRF fusion (a single-channel BM25 rank can
                # never beat dual-channel originals); they ride directly to
                # the reranker, which scores them against the original
                # question and restores precision.
                stage_started = time.perf_counter()
                terms = bm25_expansion_terms(retrieval_query)
                batches: list[list[RetrievalCandidate]] = []
                if terms:
                    batches = list(
                        await asyncio.gather(
                            *(
                                self.repository.bm25_search(
                                    knowledge_id=turn.knowledge_id,
                                    index_id=turn.index_id,
                                    query=term,
                                    limit=3,
                                )
                                for term in terms
                            )
                        )
                    )
                timings["bm25_expansion_ms"] = _elapsed_ms(stage_started)
                return merge_expanded_candidates([], [c for b in batches for c in b])

            embeddings, bm25_candidates, expansion_candidates = await asyncio.gather(
                embed_query(),
                retrieve_bm25(),
                retrieve_expansions(),
            )
            if len(embeddings) != 1 or len(embeddings[0]) != self.settings.embedding_dimension:
                raise EmbeddingError("查询 Embedding 维度异常")
            vector_started = time.perf_counter()
            vector_candidates = await self.repository.vector_search(
                knowledge_id=turn.knowledge_id,
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
            # Expansion survivors ride along to the reranker (deduped against
            # the fused set, capped to bound rerank noise): fusion stays a
            # pure original-channel competition.
            fused_ids = {candidate.chunk_id for candidate in fusion_candidates}
            expansion_survivors = [
                candidate
                for candidate in expansion_candidates
                if candidate.chunk_id not in fused_ids
            ][:3]
            if expansion_survivors:
                fusion_candidates = [*fusion_candidates, *expansion_survivors]
            # RAG-only bounded title/model recall. These are candidates, not
            # direct matches: the original question must still pass rerank.
            title_recall_record: dict[str, Any] | None = None
            if (
                self.settings.m3_rag_title_recall
                and not turn.history
                and not turn.has_prior_messages
                and turn.focus_document_id is None
                and not turn.chat_context.get("pending_options")
            ):
                terms = title_recall_terms(retrieval_query)
                if terms:
                    title_candidates = await self.repository.title_recall_candidates(
                        knowledge_id=turn.knowledge_id,
                        index_id=turn.index_id,
                        question=retrieval_query,
                        terms=terms,
                    )
                    title_recall_record = {
                        "rule": "v1",
                        "terms": terms,
                        "chunk_ids": [str(item.chunk_id) for item in title_candidates],
                    }
                    fusion_candidates = merge_expanded_candidates(
                        fusion_candidates, title_candidates
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
                    async with asyncio.timeout(self.settings.m3_rerank_timeout_seconds):
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
                except TimeoutError:
                    # A hung rerank (TimeoutError) must not eat the turn
                    # budget into a 504 — it degrades to rank-fallback routing
                    # exactly like a degradable provider error does
                    # (thresholds operate on normalized rank scores).
                    reranked_candidates = fusion_candidates[: retrieval.rerank_top_n]
                    rerank_result = {
                        "status": "DEGRADED",
                        "degraded": True,
                        "provider": rerank_provider.provider,
                        "model": rerank_provider.model_name,
                        "error_code": "RERANK_UPSTREAM_TIMEOUT",
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
            if title_recall_record is not None:
                rerank_result["title_recall"] = title_recall_record
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
            prompt_started = time.perf_counter()
            selected = await self._generation_candidates(turn, selected)
            prompt, retained, sources = self._fit_context_prompt(
                selected,
                template=turn.prompt_template,
                question=normalized_question,
                history=turn.history,
            )
            trimmed_count = len(selected) - len(retained)
            if trimmed_count:
                timings["prompt_trimmed_chunks"] = trimmed_count
            selected = retained
            timings["prompt_ms"] = _elapsed_ms(prompt_started)
            await self.repository.save_prepared(
                turn,
                embedding_provider=provider.provider,
                embedding_model=provider.model_name,
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
                retrieval_query=retrieval_query,
                query_embedding=embeddings[0],
                rerank_candidates=reranked_candidates,
                rerank_status=str(rerank_result["status"]),
                rerank_error_code=(
                    str(rerank_result["error_code"]) if rerank_result.get("error_code") else None
                ),
            )
        except ApiError as exc:
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code=exc.code,
                message=exc.message,
                partial_answer="",
                timings=timings,
                stage="prepare",
            )
            raise
        except EmbeddingError as exc:
            # EmbeddingError messages already carry the upstream reason
            # (e.g. "Embedding 上游返回 HTTP 401") — keep it as detail.
            # retryable is intentionally omitted: the message distinguishes
            # transient network faults from permanent auth/config errors.
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code="UPSTREAM_UNAVAILABLE",
                message="查询向量生成失败",
                partial_answer="",
                timings=timings,
                stage="query_embedding",
                detail=str(exc),
            )
            raise ApiError("UPSTREAM_UNAVAILABLE", "查询向量生成失败", 503) from exc
        except RerankError as exc:
            # Only non-degradable rerank errors reach fail_turn (degradable
            # ones were already downgraded above) — those are config or
            # protocol errors, so retrying is pointless.
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code=exc.code,
                message=exc.public_message,
                partial_answer="",
                timings=timings,
                stage="rerank",
                retryable=False,
                detail=getattr(exc, "detail", None),
            )
            raise ApiError(exc.code, exc.public_message, 502) from exc
        except Exception as exc:
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code="INTERNAL_ERROR",
                message="RAG 请求准备失败",
                partial_answer="",
                timings=timings,
                stage="prepare",
                detail=_unexpected_detail(exc),
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
        # Prefix the document title so the cross-encoder can disambiguate
        # near-identical chunk bodies across documents (e.g. "FFC 如何检查
        # 接线盒" vs "PDC 如何检查接线盒" whose chunks differ only by the
        # device named in the title). Mirrors the Support router's rerank
        # text enrichment.
        def _text(candidate: RetrievalCandidate) -> str:
            title = str(candidate.document or "").strip()
            return f"文档标题：{title}\n{candidate.content}" if title else candidate.content

        if self.minio is None:
            return [RerankDocument(_text(candidate)) for candidate in candidates]
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
            documents.append(RerankDocument(_text(candidate), image_bytes, mime_type))
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

    def _render_bounded_prompt(
        self, template: str, *, context: str, question: str, history: list[dict[str, str]]
    ) -> str:
        retained = list(history)
        while True:
            try:
                prompt = render_prompt(
                    template,
                    context=context,
                    question=question,
                    history=retained,
                    max_chars=self.settings.m3_prompt_max_chars,
                )
                if estimate_tokens(prompt) > self.settings.m3_prompt_token_budget:
                    raise ApiError("PROMPT_TOO_LARGE", "Prompt 超出服务端预算", 422)
            except ApiError as exc:
                if (
                    exc.code != "PROMPT_TOO_LARGE"
                    or not retained
                    or self.settings.m3_history_strategy == "legacy"
                ):
                    raise
                # Drop an entire oldest round. Context evidence is reduced only
                # after no history remains; never cut individual message text.
                retained = retained[2:]
            else:
                history[:] = retained
                return prompt

    async def _generation_candidates(
        self,
        turn: Turn,
        candidates: list[RetrievalCandidate],
        *,
        document: dict[str, Any] | None = None,
    ) -> list[RetrievalCandidate]:
        headings: dict[UUID, dict[str, Any]] = {}
        if document is not None:
            for element in document.get("elements", []):
                if (
                    element.get("element_type") == "TEXT"
                    and str(element.get("content") or "").strip()
                ):
                    headings[UUID(str(document["document_id"]))] = {
                        "title": document["title"],
                        "element_id": element.get("element_id"),
                        "content": element["content"],
                    }
                    break
        elif candidates:
            loader = getattr(self.repository, "get_generation_headings", None)
            if loader is not None:
                headings = await loader(
                    index_id=turn.index_id,
                    knowledge_id=turn.knowledge_id,
                    document_ids=list({c.document_id for c in candidates}),
                )
        result = []
        for candidate in candidates:
            meta = headings.get(candidate.document_id)
            if not meta:
                result.append(candidate)
                continue
            content = candidate.content
            section_prefix = "章节：" + " > ".join(candidate.section_path) + "\n"
            prefix = section_prefix if content.startswith(section_prefix) else ""
            body = content[len(prefix) :]
            heading = next(
                (line.strip() for line in str(meta["content"]).splitlines() if line.strip()), ""
            )
            if (
                meta.get("element_id") in candidate.element_ids
                and heading
                and len(heading) <= 300
                and (body.lstrip() == heading or body.lstrip().startswith(heading + "\n"))
            ):
                content = prefix + strip_leading_document_heading(body)
            result.append(replace(candidate, content=content, document=str(meta["title"])))
        return result

    def _fit_context_prompt(
        self,
        candidates: list[RetrievalCandidate],
        *,
        template: str,
        question: str,
        history: list[dict[str, str]],
    ) -> tuple[str, list[RetrievalCandidate], list[Source]]:
        # Keep relevance order separate from reading order. Never slice the
        # rendered order: the last step can be the strongest evidence.
        retained = list(candidates)
        while True:
            ordered = self._order_context_by_parent(retained)
            context, sources = build_context(ordered)
            try:
                prompt = self._render_bounded_prompt(
                    template,
                    context=context,
                    question=question,
                    history=history,
                )
            except ApiError as exc:
                if exc.code != "PROMPT_TOO_LARGE" or not retained:
                    raise
                retained.pop()
            else:
                return prompt, ordered, sources

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
                expanded.append(
                    replace(
                        neighbor,
                        rank=candidate.rank,
                        rerank_rank=candidate.rerank_rank,
                        rerank_score=candidate.rerank_score,
                    )
                )
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
            answer = emphasize_document_title_mentions(
                answer, [source.document for source in prepared.sources]
            )
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
                    "trace_id": str(turn.trace_id),
                    "conversation_id": str(turn.conversation_id),
                    "message_id": str(turn.assistant_message_id),
                    "index_id": str(turn.index_id),
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
                    stage="llm",
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
                stage="llm",
                retryable=exc.retryable,
                detail=getattr(exc, "detail", None),
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
        except Exception as exc:
            prepared.timings["total_ms"] = _elapsed_ms(prepared.started_at)
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code="INTERNAL_ERROR",
                message="回答生成失败",
                partial_answer="".join(prepared.answer_parts),
                timings=prepared.timings,
                stage="llm",
                detail=_unexpected_detail(exc),
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
        user_id: str | None = None,
        retrieval_overrides: dict[str, int] | None = None,
        reuse_user_message_id: UUID | None = None,
        reuse_assistant_message_id: UUID | None = None,
        parent_trace_id: UUID | None = None,
        fallback_reason: str | None = None,
        include_images: bool = True,
    ) -> AsyncIterator[bytes]:
        prepared = await self.prepare(
            knowledge_id=knowledge_id,
            conversation_id=conversation_id,
            question=question,
            request_id=request_id,
            mode="CHAT",
            user_id=user_id,
            retrieval_overrides=retrieval_overrides,
            reuse_user_message_id=reuse_user_message_id,
            reuse_assistant_message_id=reuse_assistant_message_id,
            parent_trace_id=parent_trace_id,
            fallback_reason=fallback_reason,
        )
        product_name, scope_description = self._prompt_scope(
            prepared, product_name, scope_description
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
                    "message": user_safe_chat_message(plan_error),
                    "trace_id": str(turn.trace_id),
                    "retryable": plan_error.code == "TURN_DEADLINE_EXCEEDED",
                },
            )
            return

        turn = prepared.turn

        if plan.kind == "DELIVER" or plan.fixed_answer is not None:
            async for frame in self._emit_final_chat_turn(
                prepared,
                plan,
                usage={},
                include_images=include_images,
                question=question,
            ):
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
        visible_text: ModelTextStream | DomainAssistTextStream = (
            DomainAssistTextStream() if plan.kind == "DOMAIN_ASSIST" else ModelTextStream()
        )
        try:
            provider = self.llm_factory(self.settings, turn.llm)
            prompt = plan.prompt
            # A partial answer is not a successful terminal response.
            try:
                async with asyncio.timeout(
                    max(
                        0.001,
                        self.settings.m3_turn_deadline_seconds
                        - (time.perf_counter() - prepared.started_at),
                    )
                ):
                    async for delta in provider.stream(prompt):
                        if not delta:
                            continue
                        prepared.answer_parts.append(delta)
                        visible_delta = visible_text.feed(delta)
                        if visible_delta:
                            yield sse_event("message", {"delta": visible_delta})
            except TimeoutError as exc:
                prepared.timings["llm_deadline_exceeded"] = True
                raise LlmError(
                    "TURN_DEADLINE_EXCEEDED", SERVER_BUSY_MESSAGE, retryable=True
                ) from exc
            tail = visible_text.feed("", final=True)
            if tail:
                yield sse_event("message", {"delta": tail})

            raw_answer = "".join(prepared.answer_parts)
            result = self._finalize_chat_result(plan, raw_answer)

            if isinstance(visible_text, DomainAssistTextStream) and not visible_text.streams_body:
                if result.answer:
                    yield sse_event("message", {"delta": result.answer})

            result = self._apply_image_preference(result, include_images=include_images)

            prepared.timings["llm_ms"] = _elapsed_ms(llm_started)
            prepared.timings["total_ms"] = _elapsed_ms(prepared.started_at)
            usage = provider.usage

            context_patch, pending_after = self._completion_context_update(
                prepared, plan, result, question
            )
            if self.settings.m3_rag_tech_assist_mode != "enabled":
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
                response_type=result.response_type.value,
                answer_mode=result.answer_mode.value if result.answer_mode else None,
                context_patch=context_patch,
                pending_options_after=pending_after,
            )

            yield sse_event(
                "done",
                {
                    "trace_id": str(turn.trace_id),
                    "conversation_id": str(turn.conversation_id),
                    "message_id": str(turn.assistant_message_id),
                    "index_id": str(turn.index_id),
                    "answer": result.answer,
                    "sources": result.valid_sources,
                    "usage": usage,
                    "latency_ms": prepared.timings["total_ms"],
                    "response_type": result.response_type.value,
                    "answer_mode": result.answer_mode.value if result.answer_mode else None,
                    "disclaimer": result.disclaimer,
                    "content": [block.to_dict() for block in result.content],
                    "references": self._references_for_result(plan, result),
                    "next_suggestions": self._next_suggestions(plan, result),
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
                    partial_answer=clean_model_text("".join(prepared.answer_parts)),
                    timings=prepared.timings,
                    stage="llm",
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
                partial_answer=clean_model_text("".join(prepared.answer_parts)),
                timings=prepared.timings,
                stage="llm",
                retryable=exc.retryable,
                detail=getattr(exc, "detail", None),
            )
            yield sse_event(
                "error",
                {
                    "code": exc.code,
                    # LLM answer failures are end-user facing: the busy
                    # message replaces upstream detail (kept in the trace).
                    "message": SERVER_BUSY_MESSAGE,
                    "trace_id": str(turn.trace_id),
                    "retryable": exc.retryable,
                },
            )
        except Exception as exc:
            prepared.timings["total_ms"] = _elapsed_ms(prepared.started_at)
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code="INTERNAL_ERROR",
                message="回答生成失败",
                partial_answer=clean_model_text("".join(prepared.answer_parts)),
                timings=prepared.timings,
                stage="llm",
                detail=_unexpected_detail(exc),
            )
            yield sse_event(
                "error",
                {
                    "code": "INTERNAL_ERROR",
                    "message": SERVER_BUSY_MESSAGE,
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
        user_id: str | None = None,
        retrieval_overrides: dict[str, int] | None = None,
        reuse_user_message_id: UUID | None = None,
        reuse_assistant_message_id: UUID | None = None,
        parent_trace_id: UUID | None = None,
        fallback_reason: str | None = None,
        include_images: bool = True,
    ) -> dict[str, Any]:
        prepared = await self.prepare(
            knowledge_id=knowledge_id,
            conversation_id=conversation_id,
            question=question,
            request_id=request_id,
            mode="CHAT",
            user_id=user_id,
            retrieval_overrides=retrieval_overrides,
            reuse_user_message_id=reuse_user_message_id,
            reuse_assistant_message_id=reuse_assistant_message_id,
            parent_trace_id=parent_trace_id,
            fallback_reason=fallback_reason,
        )
        product_name, scope_description = self._prompt_scope(
            prepared, product_name, scope_description
        )
        turn = prepared.turn
        plan, plan_error = await self._safely_plan_chat_turn(
            prepared, question, product_name, scope_description
        )
        if plan_error is not None:
            raise plan_error
        turn = prepared.turn

        if plan.kind == "DELIVER" or plan.fixed_answer is not None:
            prepared.timings["total_ms"] = _elapsed_ms(prepared.started_at)
            if plan.kind == "DELIVER" and plan.delivery is not None:
                result = plan.delivery["result"]
            else:
                result = self._result_for_fixed_plan(plan)
            result = self._apply_image_preference(result, include_images=include_images)
            context_patch, pending_after = self._completion_context_update(
                prepared, plan, result, question
            )
            if self.settings.m3_rag_tech_assist_mode != "enabled":
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
                response_type=result.response_type.value,
                answer_mode=result.answer_mode.value if result.answer_mode else None,
                context_patch=context_patch,
                pending_options_after=pending_after,
            )
            return self._chat_payload(
                turn,
                result,
                self._references_for_result(plan, result),
                {},
                prepared.timings["total_ms"],
                self._next_suggestions(plan, result),
            )

        provider: LlmProvider | None = None
        llm_started = time.perf_counter()
        answer_parts: list[str] = []
        try:
            provider = self.llm_factory(self.settings, turn.llm)
            prompt = plan.prompt
            try:
                async with asyncio.timeout(
                    max(
                        0.001,
                        self.settings.m3_turn_deadline_seconds
                        - (time.perf_counter() - prepared.started_at),
                    )
                ):
                    async for delta in provider.stream(prompt):
                        if delta:
                            answer_parts.append(delta)
            except TimeoutError as exc:
                prepared.timings["llm_deadline_exceeded"] = True
                raise LlmError(
                    "TURN_DEADLINE_EXCEEDED", SERVER_BUSY_MESSAGE, retryable=True
                ) from exc
            raw_answer = "".join(answer_parts)
            result = self._finalize_chat_result(plan, raw_answer)
            result = self._apply_image_preference(result, include_images=include_images)
            prepared.timings["llm_ms"] = _elapsed_ms(llm_started)
            prepared.timings["total_ms"] = _elapsed_ms(prepared.started_at)
            usage = provider.usage
            context_patch, pending_after = self._completion_context_update(
                prepared, plan, result, question
            )
            if self.settings.m3_rag_tech_assist_mode != "enabled":
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
                response_type=result.response_type.value,
                answer_mode=result.answer_mode.value if result.answer_mode else None,
                context_patch=context_patch,
                pending_options_after=pending_after,
            )
            return self._chat_payload(
                turn,
                result,
                self._references_for_result(plan, result),
                usage,
                prepared.timings["total_ms"],
                self._next_suggestions(plan, result),
            )
        except LlmError as exc:
            prepared.timings["llm_ms"] = _elapsed_ms(llm_started)
            prepared.timings["total_ms"] = _elapsed_ms(prepared.started_at)
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code=exc.code,
                message=exc.public_message,
                partial_answer=clean_model_text("".join(answer_parts)),
                timings=prepared.timings,
                stage="llm",
                retryable=exc.retryable,
                detail=getattr(exc, "detail", None),
            )
            raise ApiError(exc.code, exc.public_message, 504 if exc.retryable else 503) from exc
        except ApiError:
            raise
        except Exception as exc:
            prepared.timings["total_ms"] = _elapsed_ms(prepared.started_at)
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code="INTERNAL_ERROR",
                message="回答生成失败",
                partial_answer="",
                timings=prepared.timings,
                stage="llm",
                detail=_unexpected_detail(exc),
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
            async with asyncio.timeout(
                max(
                    0.001,
                    self.settings.m3_turn_deadline_seconds
                    - (time.perf_counter() - prepared.started_at),
                )
            ):
                effective_question = prepared.policy_question or question.strip()
                plan = await self._plan_chat_turn(
                    prepared=prepared,
                    question=effective_question,
                    product_name=product_name,
                    scope_description=scope_description,
                )
            policy_question = effective_question
            if plan.routing.get("resolved_from") == "CLARIFICATION_REPLY":
                policy_question = str(prepared.turn.chat_context.get("pending_query") or "").strip()
                policy_question = policy_question or effective_question
            policy = classify_answer_style(policy_question)
            if plan.routing.get("resolved_from") == "TITLE_MATCH":
                policy = replace(
                    policy,
                    style=AnswerStyle.FULL_DOCUMENT,
                    reason="EXPLICIT_DOCUMENT_SELECTION",
                )
            plan.routing["answer_policy"] = {
                "version": POLICY_VERSION,
                "mode": self.settings.m3_rag_tech_assist_mode,
                "style": policy.style.value,
                "reason": policy.reason,
                "effective_query_source": (
                    "RAG_ISSUE"
                    if prepared.issue_resumed
                    else "PENDING_QUERY"
                    if policy_question != effective_question
                    else "CURRENT_QUESTION"
                ),
            }
            plan.routing["generation"] = {
                "kind": plan.kind,
                "prompt_sha256": hashlib.sha256(plan.prompt.encode()).hexdigest()
                if plan.prompt
                else None,
                "prompt_tokens": estimate_tokens(plan.prompt),
                "sources": [source.public_dict() for source in plan.sources],
                "history": {
                    **prepared.turn.history_diagnostics,
                    "used_message_ids": [h.get("message_id") for h in prepared.turn.history],
                    "used_message_count": len(prepared.turn.history),
                },
            }
            return plan, None
        except TimeoutError:
            prepared.timings["total_ms"] = _elapsed_ms(prepared.started_at)
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code="TURN_DEADLINE_EXCEEDED",
                message="本轮处理超过总时限",
                partial_answer="",
                timings=prepared.timings,
                stage="routing",
                retryable=True,
            )
            return _ChatPlan(kind="ROUTING"), ApiError(
                "TURN_DEADLINE_EXCEEDED",
                "本次请求处理超时，请稍后重试",
                504,
            )
        except ApiError as exc:
            prepared.timings["total_ms"] = _elapsed_ms(prepared.started_at)
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code=exc.code,
                message=exc.message,
                partial_answer="",
                timings=prepared.timings,
                stage="routing",
            )
            return _ChatPlan(kind="ROUTING"), ApiError(exc.code, exc.message, exc.status_code)
        except Exception as exc:
            prepared.timings["total_ms"] = _elapsed_ms(prepared.started_at)
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code="INTERNAL_ERROR",
                message="文档路由失败",
                partial_answer="",
                timings=prepared.timings,
                stage="routing",
                detail=_unexpected_detail(exc),
            )
            return _ChatPlan(kind="ROUTING"), ApiError("INTERNAL_ERROR", "文档路由失败", 500)

    async def _deliver_chat_document(
        self,
        prepared: PreparedRag,
        document_id: UUID,
        routing: dict[str, Any],
        *,
        clear_pending_after: bool = False,
    ) -> _ChatPlan:
        turn = prepared.turn
        document = await self.repository.get_document_blocks(
            knowledge_id=turn.knowledge_id,
            index_id=turn.index_id,
            document_id=document_id,
        )
        # Validate/build before committing focus; all direct paths use the
        # same optimistic context-version transition and full delivery.
        delivery = self._build_delivery_payload(document)
        await self._focus_document(prepared, document_id)
        return _ChatPlan(
            kind="DELIVER",
            delivery=delivery,
            routing=routing,
            references=delivery["references"],
            clear_pending_after=clear_pending_after,
        )

    async def _focus_document(self, prepared: PreparedRag, document_id: UUID) -> None:
        turn = prepared.turn
        focus_epoch = await self.repository.set_conversation_focus(
            turn.conversation_id,
            document_id,
            turn.index_id,
            expected_context_version=turn.context_version,
            user_message_id=turn.user_message_id,
            assistant_message_id=turn.assistant_message_id,
            origin_query=(
                str(turn.chat_context.get("pending_query") or "") or None
                if any(
                    str(option.get("document_id")) == str(document_id)
                    for option in turn.chat_context.get("pending_options", [])
                )
                else None
            ),
            preserve_rag_issue=self.settings.m3_rag_tech_assist_mode == "enabled",
        )
        preserved_context: dict[str, Any] = {}
        if self.settings.m3_rag_tech_assist_mode == "enabled":
            issue = turn.chat_context.get("rag_issue")
            if isinstance(issue, dict):
                preserved_context["rag_issue"] = issue
        prepared.turn = replace(
            turn,
            focus_document_id=document_id,
            focus_epoch=focus_epoch,
            context_version=turn.context_version + 1,
            chat_context=preserved_context,
        )

    async def _plan_chat_turn(
        self,
        *,
        prepared: PreparedRag,
        question: str,
        product_name: str,
        scope_description: str,
    ) -> _ChatPlan:
        turn = prepared.turn
        if prepared.input_guard_reason is not None:
            phase = "DOC_FOCUS" if turn.focus_document_id is not None else "ALIGNING"
            return _ChatPlan(
                kind="NO_MATCH",
                routing={
                    "decision": ACTION_NO_MATCH,
                    "document_id": None,
                    "document_title": None,
                    "resolved_from": "INPUT_GUARD",
                    "pending_options": [],
                    "doc_scores": [],
                    "phase_before": phase,
                    "phase_after": phase,
                    "thresholds": {"input_guard": prepared.input_guard_reason},
                },
                fixed_answer=UNCONFIRMED_MESSAGE,
            )
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
            single_clarify_best_floor=self.settings.m3_doc_single_clarify_best_floor,
            rerank_degraded=prepared.rerank_status == "DEGRADED",
        )
        had_pending = bool(turn.chat_context.get("pending_options"))
        first_turn = (
            not turn.history
            and not turn.has_prior_messages
            and turn.focus_document_id is None
            and not had_pending
            and prepared.rerank_status == "PASSED"
            and self._match_meta_ack(question) is None
        )
        if first_turn and self.settings.m3_rag_conflict_guard:
            decision = protect_aggregation(decision, candidates)

        excerpt = document_followup_excerpt(question)
        if (
            excerpt
            and turn.focus_document_id is not None
            and not had_pending
            and prepared.rerank_status == "PASSED"
            and not decision.thresholds.get("fallback_guard")
        ):
            document = await self.repository.get_document_blocks(
                knowledge_id=turn.knowledge_id,
                index_id=turn.index_id,
                document_id=turn.focus_document_id,
            )
            # An explicit, verbatim instruction is stronger evidence of the
            # user's intended document than cross-document similarity scores.
            if any(
                excerpt in str(element.get("content") or "")
                for element in document.get("elements", [])
                if element.get("element_type") in {"TEXT", "TABLE"}
            ):
                routing = decision.to_dict()
                routing.update(
                    decision=ACTION_STAY,
                    resolved_from="FOCUS_EXCERPT",
                    document_id=str(turn.focus_document_id),
                    document_title=str(document["title"]),
                    pending_options=[],
                    focus_excerpt={"previous_decision": decision.action},
                )
                return await self._document_qa_plan(
                    prepared=prepared,
                    document_id=turn.focus_document_id,
                    question=question,
                    product_name=product_name,
                    scope_description=scope_description,
                    routing=routing,
                    document=document,
                )

        async def selected_document(document_id: UUID, routing: dict[str, Any]) -> _ChatPlan:
            policy_question = question
            if routing.get("resolved_from") == "CLARIFICATION_REPLY":
                policy_question = (
                    str(turn.chat_context.get("pending_query") or "").strip() or question
                )
            policy = classify_answer_style(policy_question)
            if self.settings.m3_rag_tech_assist_mode == "enabled":
                force_delivery = routing.get("resolved_from") == "TITLE_MATCH"
                if policy.style != AnswerStyle.FULL_DOCUMENT and not force_delivery:
                    plan = await self._document_qa_plan(
                        prepared=prepared,
                        document_id=document_id,
                        question=policy_question,
                        product_name=product_name,
                        scope_description=scope_description,
                        routing=routing,
                        clear_pending_after=had_pending,
                    )
                    if plan.kind in {"DOC_QA", "CHUNK_RAG"}:
                        await self._focus_document(prepared, document_id)
                    return plan
                return await self._deliver_chat_document(
                    prepared,
                    document_id,
                    routing,
                    clear_pending_after=had_pending,
                )
            fact_mode = self.settings.m3_rag_fact_qa_mode
            explanation_mode = self.settings.m3_rag_explanation_mode
            explanation_qa = False
            if first_turn and explanation_mode != "off" and _is_explanation_question(question):
                routing["explanation_qa"] = {"mode": explanation_mode, "rule": "explanation_v1"}
                explanation_qa = explanation_mode == "enabled"
            if first_turn and fact_mode != "off" and _is_fact_question(question):
                routing["fact_qa"] = {"mode": fact_mode, "rule": "explicit_fact_v1"}
                explanation_qa |= fact_mode == "enabled"
            if explanation_qa:
                plan = await self._document_qa_plan(
                    prepared=prepared,
                    document_id=document_id,
                    question=question,
                    product_name=product_name,
                    scope_description=scope_description,
                    routing=routing,
                    clear_pending_after=had_pending,
                )
                if plan.kind in {"DOC_QA", "CHUNK_RAG"}:
                    await self._focus_document(prepared, document_id)
                return plan
            return await self._deliver_chat_document(
                prepared,
                document_id,
                routing,
                clear_pending_after=had_pending,
            )

        if decision.action == ACTION_DELIVER and decision.document_id is not None:
            return await selected_document(decision.document_id, decision.to_dict())

        clear_pending_after = had_pending
        catalog: list[dict[str, Any]] | None = None
        rank_fallback = bool(decision.thresholds.get("fallback_guard"))

        meta_ack = self._match_meta_ack(question)
        if meta_ack is not None:
            routing = decision.to_dict()
            routing["resolved_from"] = "META_ACK"
            return _ChatPlan(
                kind="ROUTING",
                routing=routing,
                fixed_answer=meta_ack,
                clear_pending_after=had_pending,
            )

        # Catalog-title resolution: the user explicitly names a document
        # (typically confirming a previous routing suggestion verbatim).
        # Delivering it directly closes the clarify loop instead of asking
        # the same question again.  A normal STAY remains authoritative, but
        # a conservative rank-fallback STAY may be overridden by an explicit
        # catalog title because title matching does not depend on reranking.
        title_resolution_allowed = decision.action in (
            ACTION_NO_MATCH,
            ACTION_CLARIFY,
            ACTION_OFFER_SWITCH,
        ) or (decision.action == ACTION_STAY and rank_fallback)
        if title_resolution_allowed:
            catalog = await self.repository.list_kb_documents(turn.knowledge_id)
            matched = self._match_catalog_title(question, catalog)
            if matched is not None:
                matched_id = UUID(str(matched["document_id"]))
                routing = decision.to_dict()
                routing["resolved_from"] = "TITLE_MATCH"
                return await selected_document(matched_id, routing)

        # Uncalibrated rank-only candidates must not reach document QA or
        # become public references, including when a focus already exists. The
        # user gets a deterministic, non-error answer and can retry when the
        # reranker recovers or provide an explicit document title. Existing
        # focus state is retained for a later, well-formed follow-up.
        if rank_fallback:
            routing = decision.to_dict()
            routing["resolved_from"] = "RANK_FALLBACK_GUARD"
            if self.settings.m3_rag_tech_assist_mode == "enabled" and normalize_scope_description(
                turn.knowledge_description
            ):
                return self._domain_assist_plan(
                    turn=turn,
                    question=question,
                    product_name=product_name,
                    scope_description=scope_description,
                    routing=routing,
                    clear_pending_after=clear_pending_after,
                )
            return _ChatPlan(
                kind="NO_MATCH",
                routing=routing,
                fixed_answer=UNCONFIRMED_MESSAGE,
                clear_pending_after=clear_pending_after,
            )

        if (
            first_turn
            and self.settings.m3_rag_single_strong_mode != "off"
            and decision.action in (ACTION_NO_MATCH, ACTION_CLARIFY)
            and decision.resolved_from != "RAG_AGGREGATION_CONFLICT"
        ):
            strong = single_strong_document(
                candidates,
                floor=self.settings.m3_rag_single_strong_floor,
                gap=self.settings.m3_rag_single_strong_gap,
            )
            if strong is not None:
                document_id, best, runner = strong
                title = next(
                    item.document for item in candidates if item.document_id == document_id
                )
                decision = replace(
                    decision,
                    thresholds={
                        **decision.thresholds,
                        "rag_single_strong": {
                            "rule": "v1",
                            "mode": self.settings.m3_rag_single_strong_mode,
                            "document_id": str(document_id),
                            "best": best,
                            "runner_best": runner,
                            "floor": self.settings.m3_rag_single_strong_floor,
                            "gap": self.settings.m3_rag_single_strong_gap,
                        },
                    },
                )
                if self.settings.m3_rag_single_strong_mode == "enabled":
                    decision = replace(
                        decision,
                        action=ACTION_DELIVER,
                        document_id=document_id,
                        document_title=title,
                        pending_options=[],
                        resolved_from="RAG_SINGLE_STRONG",
                    )
                    return await selected_document(document_id, decision.to_dict())

        if decision.action == ACTION_STAY and turn.focus_document_id is not None:
            if (
                self.settings.m3_rag_tech_assist_mode == "enabled"
                and classify_answer_style(question).style == AnswerStyle.FULL_DOCUMENT
            ):
                return await self._deliver_chat_document(
                    prepared,
                    turn.focus_document_id,
                    decision.to_dict(),
                    clear_pending_after=clear_pending_after,
                )
            return await self._document_qa_plan(
                prepared=prepared,
                document_id=turn.focus_document_id,
                question=question,
                product_name=product_name,
                scope_description=scope_description,
                routing=decision.to_dict(),
                clear_pending_after=clear_pending_after,
            )

        if decision.action == ACTION_CLARIFY:
            # The candidates are already deterministic here: a templated
            # clarify answer is instant, always enumerates titles (the
            # pending-resolution loop matches the user's next reply against
            # exactly these titles), and removes a slow freeform LLM answer
            # from the interactive critical path.
            titles = self._render_doc_list(decision.pending_options)
            if len(decision.pending_options) == 1:
                fixed = "可能与您的问题相关的文档：\n" + titles + "\n请确认是否需要查看。"
            else:
                fixed = "找到多个可能相关的文档，请确认要查看哪一份：\n" + titles
            return _ChatPlan(
                kind="ROUTING",
                routing=decision.to_dict(),
                fixed_answer=fixed,
                pending_after=decision.pending_options,
            )

        if decision.action == ACTION_OFFER_SWITCH and turn.focus_document_id is not None:
            current = await self.repository.get_document_source(turn.focus_document_id)
            target = decision.pending_options[0] if decision.pending_options else {}
            target_title = str(target.get("title", "")) or "另一份文档"
            current_title = str(current.get("display_name") or current.get("filename") or "")
            fixed = (
                "当前问题可能属于《"
                + target_title
                + "》，是否切换焦点文档？\n（回复“确认”或文档名切换；继续处理《"
                + current_title
                + "》请直接追问）"
            )
            return _ChatPlan(
                kind="ROUTING",
                routing=decision.to_dict(),
                fixed_answer=fixed,
                pending_after=decision.pending_options,
            )

        if catalog is None:
            catalog = await self.repository.list_kb_documents(turn.knowledge_id)
        routing = decision.to_dict()
        if (
            decision.action == ACTION_NO_MATCH
            and self.settings.m3_rag_tech_assist_mode == "enabled"
            and normalize_scope_description(turn.knowledge_description)
        ):
            return self._domain_assist_plan(
                turn=turn,
                question=question,
                product_name=product_name,
                scope_description=scope_description,
                routing=routing,
                clear_pending_after=clear_pending_after,
            )
        if self.settings.m3_rag_tech_assist_mode == "shadow":
            routing["domain_assist"] = {
                "mode": "shadow",
                "eligible": bool(normalize_scope_description(turn.knowledge_description)),
            }
        if not catalog:
            return _ChatPlan(
                kind="NO_MATCH",
                routing=routing,
                fixed_answer=UNCONFIRMED_MESSAGE,
                clear_pending_after=clear_pending_after,
            )
        context = "文档目录：\n" + self._render_doc_list(catalog)
        fallback_suggestions: list[str] = []
        if decision.action == ACTION_NO_MATCH:
            # Retrieval weak candidates: documents with real hits that did
            # not reach the delivery thresholds. They are prompt hints only —
            # deliberately NOT pending_options, so they never enter the
            # auto-delivery resolution loop (low mis-delivery rate first).
            weak_candidates = [item.to_dict() for item in decision.doc_scores if item.hits >= 1][:3]
            if weak_candidates:
                context += (
                    "\n检索器弱候选（与问题有一定相关度但未达交付置信度，"
                    "仅供澄清参考，不作为交付依据）：\n"
                    + self._render_weak_candidates(weak_candidates)
                )
            fallback_suggestions = build_suggestions(weak_candidates) or build_suggestions(catalog)
        prompt = self._render_bounded_prompt(
            build_routing_prompt_template(product_name, scope_description),
            context=context,
            question=question,
            history=turn.history,
        )
        return _ChatPlan(
            kind="ROUTING",
            prompt=prompt,
            routing=routing,
            clear_pending_after=clear_pending_after,
            fallback_suggestions=fallback_suggestions,
        )

    def _domain_assist_plan(
        self,
        *,
        turn: Turn,
        question: str,
        product_name: str,
        scope_description: str,
        routing: dict[str, Any],
        clear_pending_after: bool,
    ) -> _ChatPlan:
        routing["domain_assist"] = {
            "mode": "enabled",
            "scope_sha256": turn.scope_sha256,
            "scope_updated_at": (
                turn.knowledge_updated_at.isoformat() if turn.knowledge_updated_at else None
            ),
        }
        prompt = self._render_bounded_prompt(
            build_domain_assist_prompt_template(product_name, scope_description),
            context="本轮未取得足以支持产品专有事实的可靠正文证据。",
            question=question,
            history=turn.history,
        )
        return _ChatPlan(
            kind="DOMAIN_ASSIST",
            prompt=prompt,
            routing=routing,
            clear_pending_after=clear_pending_after,
        )

    async def _scoped_document_candidates(
        self,
        prepared: PreparedRag,
        document_id: UUID,
        question: str,
    ) -> tuple[list[RetrievalCandidate], dict[str, Any]]:
        turn = prepared.turn
        # Reuse the query vector and frozen model/index; only long documents
        # pay for this bounded second retrieval within the confirmed document.
        kwargs = {
            "knowledge_id": turn.knowledge_id,
            "index_id": turn.index_id,
            "document_id": document_id,
        }
        query = prepared.retrieval_query or question
        bm25 = await self.repository.bm25_search(
            **kwargs,
            query=query,
            limit=turn.retrieval.bm25_top_k,
        )
        vector = (
            await self.repository.vector_search(
                **kwargs,
                embedding=prepared.query_embedding,
                limit=turn.retrieval.vector_top_k,
            )
            if prepared.query_embedding
            else []
        )
        candidates = reciprocal_rank_fusion(
            vector,
            bm25,
            rrf_k=self.settings.m5_rrf_k,
            limit=turn.retrieval.fusion_top_k,
        )
        candidates = [c for c in candidates if c.document_id == document_id]
        diagnostics: dict[str, Any] = {
            "document_id": str(document_id),
            "index_id": str(turn.index_id),
            "candidate_count": len(candidates),
            "status": "NO_CANDIDATES",
        }
        if not candidates or turn.rerank is None:
            return [], diagnostics
        provider = self.rerank_factory(self.settings, turn.rerank)
        try:
            async with asyncio.timeout(self.settings.m3_rerank_timeout_seconds):
                documents = await self._build_rerank_documents(turn.index_id, candidates)
                outcome = await provider.rerank(query, documents, turn.retrieval.rerank_top_n)
            candidates = apply_rerank(candidates, outcome.items)
            diagnostics.update(status="PASSED", candidates=[c.trace_dict() for c in candidates])
            return candidates, diagnostics
        except (RerankError, TimeoutError) as exc:
            diagnostics.update(status="DEGRADED", error_code=getattr(exc, "code", "TIMEOUT"))
            return [], diagnostics
        finally:
            await provider.aclose()

    async def _document_qa_plan(
        self,
        *,
        prepared: PreparedRag,
        document_id: UUID,
        question: str,
        product_name: str,
        scope_description: str,
        routing: dict[str, Any],
        clear_pending_after: bool = False,
        document: dict[str, Any] | None = None,
    ) -> _ChatPlan:
        turn = prepared.turn
        if document is None:
            document = await self.repository.get_document_blocks(
                knowledge_id=turn.knowledge_id,
                index_id=turn.index_id,
                document_id=document_id,
            )
        context, sources = build_document_context(document)
        try:
            prompt = self._render_bounded_prompt(
                build_doc_qa_prompt_template(product_name, scope_description),
                context=context,
                question=question,
                history=turn.history,
            )
        except ApiError as exc:
            if exc.code != "PROMPT_TOO_LARGE":
                raise
            started = time.perf_counter()
            candidates, diagnostics = await self._scoped_document_candidates(
                prepared,
                document_id,
                question,
            )
            expanded, expansion = await self._expand_context_neighbors(
                turn.index_id,
                candidates,
                max_chunks=turn.retrieval.context_max_chunks,
            )
            selected = self._select_context(
                [c for c in expanded if c.document_id == document_id],
                max_chunks=turn.retrieval.context_max_chunks,
            )
            selected = await self._generation_candidates(turn, selected, document=document)
            prompt, selected, sources = self._fit_context_prompt(
                selected,
                template=build_chunk_qa_prompt_template(product_name, scope_description),
                question=question,
                history=turn.history,
            )
            prepared.timings["document_retrieval_ms"] = _elapsed_ms(started)
            routing.update(
                degraded_to="CHUNK_RAG",
                degradation_reason="DOCUMENT_EXCEEDS_PROMPT_BUDGET",
                document_retrieval=diagnostics,
                document_context_expansion=expansion,
            )
            if not selected:
                return _ChatPlan(
                    kind="NO_MATCH",
                    routing=routing,
                    fixed_answer="当前文档中没有足够的相关内容，无法确认该问题。请补充具体章节或操作对象。",
                    clear_pending_after=clear_pending_after,
                )
            return _ChatPlan(
                kind="CHUNK_RAG",
                prompt=prompt,
                sources=sources,
                routing=routing,
                require_citations=True,
                references=[self._document_reference(document["document_id"], document["title"])],
                clear_pending_after=clear_pending_after,
            )
        return _ChatPlan(
            kind="DOC_QA",
            prompt=prompt,
            sources=sources,
            routing=routing,
            require_citations=False,
            references=[self._document_reference(document["document_id"], document["title"])],
            clear_pending_after=clear_pending_after,
        )

    def _build_delivery_payload(self, document: dict[str, Any]) -> dict[str, Any]:
        # Delivery copies parsed document elements, not an LLM prompt. Token
        # budgets here used to silently omit entire steps and trailing images.
        document_title = str(document["title"])
        blocks: list[RoutingContentBlock] = []
        image_asset_ids: list[str] = []
        heading_pending = True
        for element in document.get("elements", []):
            if element.get("element_type") == "IMAGE":
                asset_id = element.get("image_asset_id")
                if asset_id is not None:
                    image_asset_ids.append(str(asset_id))
                    blocks.append(
                        RoutingContentBlock(
                            type="image",
                            source_id="S1",
                            image_asset_id=str(asset_id),
                        )
                    )
                elif element.get("content"):
                    blocks.append(RoutingContentBlock(type="text", text=str(element["content"])))
                continue
            if not element.get("content"):
                continue
            content = str(element["content"])
            if element.get("element_type") == "TEXT":
                if not content.strip():
                    continue
                if heading_pending:
                    heading_pending = False
                    content = strip_leading_document_heading(content)
                    if not content.strip():
                        continue
            blocks.append(
                RoutingContentBlock(
                    type="table" if element.get("element_type") == "TABLE" else "text",
                    text=content,
                )
            )
        summary = markdown_document_title(document_title)
        document_source = {
            "source_id": "S1",
            "document_id": document["document_id"],
            "document": document_title,
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
            "references": [self._document_reference(document["document_id"], document_title)],
        }

    @staticmethod
    def _apply_image_preference(result: ChatResult, *, include_images: bool) -> ChatResult:
        """Filter delivery, not retrieval evidence; apply before saving the answer."""
        if include_images:
            return result
        return replace(
            result,
            content=[block for block in result.content if block.type != "image"],
            valid_sources=[{**source, "image_asset_ids": []} for source in result.valid_sources],
        )

    async def _emit_final_chat_turn(
        self,
        prepared: PreparedRag,
        plan: _ChatPlan,
        *,
        usage: dict[str, Any],
        include_images: bool = True,
        question: str = "",
    ) -> AsyncIterator[bytes]:
        turn = prepared.turn
        if plan.kind == "DELIVER" and plan.delivery is not None:
            result = plan.delivery["result"]
        else:
            result = self._result_for_fixed_plan(plan)
        result = self._apply_image_preference(result, include_images=include_images)
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
        # Templated routing answers (clarify / switch offers) are short
        # text: stream them in small deltas so the chat UI renders
        # progressively like LLM answers. Document delivery keeps its
        # trace/source/done contract — its body renders from content
        # blocks, not text deltas.
        if plan.kind != "DELIVER":
            answer_text = str(result.answer or "")
            for offset in range(0, len(answer_text), 24):
                yield sse_event("message", {"delta": answer_text[offset : offset + 24]})
                await asyncio.sleep(0)
        context_patch, pending_after = self._completion_context_update(
            prepared, plan, result, question
        )
        if self.settings.m3_rag_tech_assist_mode != "enabled":
            await self._apply_post_state(plan, turn, question)
        await self.repository.complete_turn(
            turn,
            answer=result.answer,
            sources=result.valid_sources,
            usage=usage,
            citation_result=result.citation_result,
            timings=prepared.timings,
            prompt=None,
            doc_routing=plan.routing,
            response_type=result.response_type.value,
            answer_mode=result.answer_mode.value if result.answer_mode else None,
            context_patch=context_patch,
            pending_options_after=pending_after,
        )
        yield sse_event(
            "done",
            {
                "trace_id": str(turn.trace_id),
                "conversation_id": str(turn.conversation_id),
                "message_id": str(turn.assistant_message_id),
                "index_id": str(turn.index_id),
                "answer": result.answer,
                "sources": result.valid_sources,
                "usage": usage,
                "latency_ms": prepared.timings["total_ms"],
                "response_type": result.response_type.value,
                "answer_mode": result.answer_mode.value if result.answer_mode else None,
                "disclaimer": result.disclaimer,
                "content": [block.to_dict() for block in result.content],
                "references": self._references_for_result(plan, result),
                "next_suggestions": self._next_suggestions(plan, result),
            },
        )

    def _completion_context_update(
        self,
        prepared: PreparedRag,
        plan: _ChatPlan,
        result: ChatResult,
        question: str,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
        if self.settings.m3_rag_tech_assist_mode != "enabled":
            return None, None
        turn = prepared.turn
        if plan.pending_after:
            pending_options = [
                {
                    **option,
                    "origin_focus_epoch": turn.focus_epoch,
                    "origin_document_id": (
                        str(turn.focus_document_id) if turn.focus_document_id is not None else None
                    ),
                }
                for option in plan.pending_after
            ]
            plan.routing["rag_issue_state"] = {"action": "CLEAR", "reason": "DOC_PENDING"}
            return (
                {
                    "pending_options": pending_options,
                    "pending_query": question.strip(),
                    "rag_issue": None,
                },
                pending_options,
            )

        if plan.kind == "DOMAIN_ASSIST" and result.response_type == ResponseType.UNCONFIRMED:
            existing = turn.chat_context.get("rag_issue")
            origin_id = turn.user_message_id
            if prepared.issue_resumed and isinstance(existing, dict):
                try:
                    origin_id = UUID(str(existing["origin_user_message_id"]))
                except (KeyError, TypeError, ValueError):
                    origin_id = turn.user_message_id
            origin_query = (prepared.policy_question or question.strip())[:1_200]
            style = classify_answer_style(origin_query)
            issue = {
                "version": 1,
                "strategy_version": POLICY_VERSION,
                "origin_query": origin_query,
                "origin_user_message_id": str(origin_id),
                "answer_style": style.style.value,
                "pending_condition": {
                    "type": "TEXT",
                    "question": result.answer[:500],
                    "source_user_message_id": str(turn.user_message_id),
                    "source_assistant_message_id": str(turn.assistant_message_id),
                },
            }
            plan.routing["rag_issue_state"] = {
                "action": "SET",
                "origin_user_message_id": str(origin_id),
                "source_assistant_message_id": str(turn.assistant_message_id),
            }
            return (
                {"pending_options": None, "pending_query": None, "rag_issue": issue},
                [],
            )

        if prepared.input_guard_reason is not None and turn.chat_context.get("rag_issue"):
            return None, None
        if (
            turn.chat_context.get("rag_issue") is not None
            or turn.chat_context.get("pending_options")
            or plan.clear_pending_after
        ):
            plan.routing["rag_issue_state"] = {"action": "CLEAR", "reason": "TURN_COMPLETED"}
            return (
                {"pending_options": None, "pending_query": None, "rag_issue": None},
                [],
            )
        return None, None

    async def _apply_post_state(self, plan: _ChatPlan, turn: Any, question: str) -> None:
        if plan.pending_after:
            pending_options = [
                {
                    **option,
                    "origin_focus_epoch": turn.focus_epoch,
                    "origin_document_id": (
                        str(turn.focus_document_id) if turn.focus_document_id is not None else None
                    ),
                }
                for option in plan.pending_after
            ]
            await self.repository.set_conversation_pending(
                turn.conversation_id,
                pending_options=pending_options,
                pending_query=question,
                expected_context_version=turn.context_version,
            )
        elif plan.clear_pending_after:
            await self.repository.clear_conversation_pending(
                turn.conversation_id,
                expected_context_version=turn.context_version,
            )

    def _finalize_chat_result(self, plan: _ChatPlan, raw_answer: str) -> ChatResult:
        if plan.kind in ("DOC_QA", "CHUNK_RAG"):
            source_dicts = [s.public_dict() for s in plan.sources]
            result = post_validate(
                raw_answer,
                source_dicts,
                True,
                require_citations=plan.require_citations,
            )
        elif plan.kind == "DOMAIN_ASSIST":
            result = post_validate(
                raw_answer,
                [],
                False,
                preserve_unconfirmed_without_evidence=True,
            )
        else:
            result = build_assist_result(raw_answer)
        if not result.answer.strip() and not any(block.type == "image" for block in result.content):
            raise LlmError("LLM_EMPTY_RESPONSE", SERVER_BUSY_MESSAGE, retryable=True)
        titles = [
            str(reference.get("title") or reference.get("document") or "")
            for reference in (plan.references or result.valid_sources)
        ]
        if not titles:
            return result
        answer = emphasize_document_title_mentions(result.answer, titles)
        content = [
            replace(
                block,
                text=(
                    emphasize_document_title_mentions(block.text, titles)
                    if block.text is not None
                    else None
                ),
            )
            for block in result.content
        ]
        return replace(result, answer=answer, content=content)

    @staticmethod
    def _result_for_fixed_plan(plan: _ChatPlan) -> ChatResult:
        if plan.kind == "NO_MATCH":
            return post_validate(
                "[MODE:UNCONFIRMED]\n" + (plan.fixed_answer or UNCONFIRMED_MESSAGE),
                [],
                False,
            )
        return build_assist_result(plan.fixed_answer or "")

    @staticmethod
    def _references_for_result(plan: _ChatPlan, result: ChatResult) -> list[dict[str, Any]]:
        if result.response_type.value in ("DOC_DELIVERED", "DOCUMENT_DELIVERED"):
            return plan.references
        if result.response_type.value == "ANSWERED" and result.answer_mode is not None:
            if result.answer_mode.value in ("PRODUCT_KNOWLEDGE", "PRODUCT_EXPLAINED"):
                if plan.kind == "CHUNK_RAG":
                    documents = {str(s.get("document_id")) for s in result.valid_sources}
                    return [r for r in plan.references if str(r.get("document_id")) in documents]
                return plan.references
        return []

    @staticmethod
    def _match_meta_ack(question: str) -> str | None:
        normalized = re.sub(r"\s+", "", question)
        if not normalized or len(normalized) > 12:
            return None
        return _META_ACK_MESSAGE if _META_ACK_PATTERN.match(normalized) else None

    @staticmethod
    def _render_doc_list(docs: list[dict[str, Any]]) -> str:
        return "\n".join(
            str(index) + ". " + markdown_document_title(str(doc.get("title", "")))
            for index, doc in enumerate(docs, start=1)
        )

    @staticmethod
    def _render_weak_candidates(weak: list[dict[str, Any]]) -> str:
        return "\n".join(
            f"{index}. {markdown_document_title(str(item.get('title', '')))}"
            f"（相关度 {float(item.get('score', 0.0)):.2f}，命中 {item.get('hits', 0)} 个片段）"
            for index, item in enumerate(weak, start=1)
        )

    @staticmethod
    def _next_suggestions(plan: _ChatPlan, result: ChatResult) -> list[str]:
        """Expose plan retry suggestions unless the turn delivered a document."""
        if result.response_type.value in ("DOC_DELIVERED", "DOCUMENT_DELIVERED"):
            return []
        return plan.fallback_suggestions

    @staticmethod
    def _match_catalog_title(question: str, catalog: list[dict[str, Any]]) -> dict[str, Any] | None:
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
        next_suggestions: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "message_id": str(turn.assistant_message_id),
            "conversation_id": str(turn.conversation_id),
            "trace_id": str(turn.trace_id),
            "index_id": str(turn.index_id),
            "response_type": result.response_type.value,
            "answer_mode": result.answer_mode.value if result.answer_mode else None,
            "answer": result.answer,
            "disclaimer": result.disclaimer,
            "content": [block.to_dict() for block in result.content],
            "references": references,
            "usage": usage,
            "latency_ms": total_ms,
            "next_suggestions": next_suggestions or [],
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
