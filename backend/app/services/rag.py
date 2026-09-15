from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from typing import Any
from uuid import UUID

from minio import Minio

from app.common.errors import SERVER_BUSY_MESSAGE, ApiError, user_safe_chat_message
from app.core.config import Settings
from app.ingestion.embedding import EmbeddingError, EmbeddingProvider, create_embedding_provider
from app.rag.llm import LlmError, LlmProvider, create_llm_provider
from app.rag.models import ModelSnapshot, PreparedRag, Turn
from app.rag.repository import RagRepository
from app.rag.rerank import RerankProvider, create_rerank_provider
from app.rag.response import (
    AnswerMode,
    ChatResult,
    ContentBlock,
    ResponseType,
    build_plain_answer,
)
from app.rag.scope import DEFAULT_PRODUCT_SCOPE
from app.rag.v2 import RagV2Pipeline

EmbeddingFactory = Callable[[Settings], EmbeddingProvider]
LlmFactory = Callable[[Settings, ModelSnapshot], LlmProvider]
RerankFactory = Callable[[Settings, ModelSnapshot], RerankProvider]

_META_ACK_PATTERN = re.compile(r"^(?:换一?个(?:问题|话题)|算了|先?不问了)[呀啊吧。！!，,\s]*$")
_META_ACK_MESSAGE = "好的，请问您想了解什么？我将基于产品文档为您解答。"
_LOW_INFORMATION_MESSAGE = "请补充具体的设备、故障现象或操作目标，我会继续协助排查。"
_KEYBOARD_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")


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
    if re.search(r"原样|逐字|打印|输出|复述", normalized) and re.search(
        r"系统提示|提示词|prompt|知识库范围|内部规则|token|内部地址", normalized
    ):
        return "PROMPT_EXTRACTION"
    return None


def sse_event(event: str, payload: dict[str, Any]) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n".encode()


class RagService:
    """Single active RAG implementation: hidden full-document, dual-LLM V2."""

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
        # Keep the constructor contract used by application dependency factories.
        self.minio = minio
        self.embedding_factory = embedding_factory
        self.llm_factory = llm_factory
        self.rerank_factory = rerank_factory

    def _v2_pipeline(self) -> RagV2Pipeline:
        return RagV2Pipeline(
            self.repository,
            self.settings,
            llm_factory=self.llm_factory,
            embedding_selector=self._select_query_embedding_provider,
            rerank_factory=self.rerank_factory,
        )

    @staticmethod
    def _prompt_scope(
        prepared: PreparedRag,
        product_name: str,
        scope_description: str,
    ) -> tuple[str, str]:
        return (
            prepared.turn.knowledge_name or product_name,
            prepared.turn.scope_description or scope_description or DEFAULT_PRODUCT_SCOPE,
        )

    def _select_query_embedding_provider(self, turn: Turn) -> EmbeddingProvider:
        """Select the query embedder matching the conversation's frozen index."""

        if turn.embedding is not None and self.embedding_factory is create_embedding_provider:
            try:
                db_provider = create_embedding_provider(self.settings, turn.embedding)
            except EmbeddingError:
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
        input_guard_reason = (
            "META_ACK"
            if self._match_meta_ack(normalized_question) is not None
            else _input_guard_reason(normalized_question)
        )
        retrieval = (
            replace(turn.retrieval, **retrieval_overrides)
            if retrieval_overrides
            else turn.retrieval
        )
        return PreparedRag(
            turn=replace(turn, retrieval=retrieval),
            timings={"prepare_turn_ms": _elapsed_ms(started_at), "v2_prepare_only": True},
            started_at=started_at,
            input_guard_reason=input_guard_reason,
            policy_question=normalized_question,
        )

    async def stream(self, prepared: PreparedRag) -> AsyncIterator[bytes]:
        product_name, scope_description = self._prompt_scope(
            prepared,
            prepared.turn.knowledge_name,
            prepared.turn.scope_description,
        )
        async for frame in self._chat_stream_v2(
            prepared,
            question=prepared.policy_question,
            product_name=product_name,
            scope_description=scope_description,
            include_images=True,
        ):
            yield frame

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
        async for frame in self._chat_stream_v2(
            prepared,
            question=question,
            product_name=product_name,
            scope_description=scope_description,
            include_images=include_images,
        ):
            yield frame

    async def _generate_v2(
        self,
        prepared: PreparedRag,
        *,
        question: str,
        product_name: str,
        scope_description: str,
        include_images: bool,
    ) -> dict[str, Any]:
        turn = prepared.turn
        try:
            if prepared.input_guard_reason is not None:
                if prepared.input_guard_reason == "META_ACK":
                    result = build_plain_answer(self._match_meta_ack(question) or _META_ACK_MESSAGE)
                else:
                    result = ChatResult(
                        response_type=ResponseType.UNCONFIRMED,
                        answer_mode=AnswerMode.UNCONFIRMED,
                        answer=_LOW_INFORMATION_MESSAGE,
                        content=[ContentBlock(type="text", text=_LOW_INFORMATION_MESSAGE)],
                        disclaimer=None,
                        valid_sources=[],
                        citation_result={"status": "NO_EVIDENCE", "pipeline": "V2"},
                    )
                usage: dict[str, Any] = {"pipeline": "V2", "llm_calls": 0}
                next_suggestions: list[str] = []
                v2_trace: dict[str, Any] = {
                    "pipeline": "V2",
                    "input_guard": prepared.input_guard_reason,
                }
                timings = dict(prepared.timings)
            else:
                try:
                    async with asyncio.timeout(
                        max(
                            0.001,
                            self.settings.m3_turn_deadline_seconds
                            - (time.perf_counter() - prepared.started_at),
                        )
                    ):
                        v2 = await self._v2_pipeline().run(
                            prepared,
                            question=question,
                            product_name=product_name,
                            scope_description=scope_description,
                            include_images=include_images,
                        )
                except TimeoutError as exc:
                    raise LlmError(
                        "TURN_DEADLINE_EXCEEDED", SERVER_BUSY_MESSAGE, retryable=True
                    ) from exc
                result = v2.result
                usage = v2.usage
                next_suggestions = v2.next_suggestions
                v2_trace = v2.trace
                timings = v2.timings

            timings["total_ms"] = _elapsed_ms(prepared.started_at)
            await self.repository.complete_turn(
                turn,
                answer=result.answer,
                sources=[],
                usage=usage,
                citation_result=result.citation_result,
                timings=timings,
                doc_routing={
                    "resolved_from": "RAG_V2",
                    "pipeline": "V2",
                    "generation": v2_trace,
                },
                response_type=result.response_type.value,
                answer_mode=result.answer_mode.value if result.answer_mode else None,
            )
            return self._chat_payload(
                turn, result, [], usage, timings["total_ms"], next_suggestions
            )
        except asyncio.CancelledError:
            timings = dict(prepared.timings)
            timings["total_ms"] = _elapsed_ms(prepared.started_at)
            task = asyncio.create_task(
                self.repository.fail_turn(
                    turn,
                    status="CANCELLED",
                    code="CLIENT_DISCONNECTED",
                    message="客户端已断开",
                    partial_answer="",
                    timings=timings,
                    stage="v2",
                )
            )
            await asyncio.shield(task)
            raise
        except LlmError as exc:
            timings = dict(prepared.timings)
            timings["total_ms"] = _elapsed_ms(prepared.started_at)
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code=exc.code,
                message=exc.public_message,
                partial_answer="",
                timings=timings,
                stage="v2",
                retryable=exc.retryable,
                detail=getattr(exc, "detail", None),
            )
            raise ApiError(exc.code, SERVER_BUSY_MESSAGE, 504 if exc.retryable else 502) from exc
        except ApiError as exc:
            timings = dict(prepared.timings)
            timings["total_ms"] = _elapsed_ms(prepared.started_at)
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code=exc.code,
                message=exc.message,
                partial_answer="",
                timings=timings,
                stage="v2",
                retryable=exc.status_code >= 500,
            )
            raise
        except Exception as exc:
            timings = dict(prepared.timings)
            timings["total_ms"] = _elapsed_ms(prepared.started_at)
            await self.repository.fail_turn(
                turn,
                status="FAILED",
                code="INTERNAL_ERROR",
                message="回答生成失败",
                partial_answer="",
                timings=timings,
                stage="v2",
                detail=_unexpected_detail(exc),
            )
            raise ApiError("INTERNAL_ERROR", SERVER_BUSY_MESSAGE, 500) from exc

    async def _chat_stream_v2(
        self,
        prepared: PreparedRag,
        *,
        question: str,
        product_name: str,
        scope_description: str,
        include_images: bool,
    ) -> AsyncIterator[bytes]:
        turn = prepared.turn
        yield sse_event(
            "trace",
            {
                "trace_id": str(turn.trace_id),
                "conversation_id": str(turn.conversation_id),
                "index_id": str(turn.index_id),
            },
        )
        yield sse_event("source", {"sources": []})
        try:
            payload = await self._generate_v2(
                prepared,
                question=question,
                product_name=product_name,
                scope_description=scope_description,
                include_images=include_images,
            )
        except ApiError as exc:
            yield sse_event(
                "error",
                {
                    "code": exc.code,
                    "message": user_safe_chat_message(exc),
                    "trace_id": str(turn.trace_id),
                    "retryable": exc.status_code >= 500,
                },
            )
            return
        answer = str(payload.get("answer") or "")
        for offset in range(0, len(answer), 24):
            yield sse_event("message", {"delta": answer[offset : offset + 24]})
            await asyncio.sleep(0)
        yield sse_event("done", payload)

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
        return await self._generate_v2(
            prepared,
            question=question,
            product_name=product_name,
            scope_description=scope_description,
            include_images=include_images,
        )

    @staticmethod
    def _match_meta_ack(question: str) -> str | None:
        if len(question) > 24 or not _META_ACK_PATTERN.fullmatch(question.strip()):
            return None
        return _META_ACK_MESSAGE

    @staticmethod
    def _chat_payload(
        turn: Turn,
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
