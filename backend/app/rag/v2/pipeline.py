from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.rag.llm import LlmError, LlmProvider
from app.rag.models import ModelSnapshot, PreparedRag
from app.rag.response import (
    OUT_OF_SCOPE_MESSAGE,
    AnswerMode,
    ChatResult,
    ContentBlock,
    ResponseType,
    TraceDiagnosticReason,
)
from app.rag.v2.answer_generator import (
    apply_scope_authority,
    build_answer_prompt,
    generate_answer,
    repair_generated_answer,
)
from app.rag.v2.conversation_answer import (
    CONVERSATION_FALLBACK_MESSAGE,
    build_conversation_prompt,
    build_conversation_result,
    generate_conversation_answer,
)
from app.rag.v2.document_context import build_document_context
from app.rag.v2.document_retrieval import EmbeddingSelector, RerankFactory, retrieve_documents
from app.rag.v2.query_interpreter import interpret_query
from app.rag.v2.validation import (
    evidence_violations,
    normalize_evidence_bindings,
    parameter_observations,
    public_leaks,
    scrub_document_keys,
    validate_generated_answer,
)

LlmFactory = Callable[[Settings, ModelSnapshot], LlmProvider]


@dataclass(frozen=True, slots=True)
class RagV2Result:
    result: ChatResult
    usage: dict[str, Any]
    trace: dict[str, Any]
    timings: dict[str, int]
    next_suggestions: list[str]


class RagV2Pipeline:
    def __init__(
        self,
        repository: Any,
        settings: Settings,
        *,
        llm_factory: LlmFactory,
        embedding_selector: EmbeddingSelector,
        rerank_factory: RerankFactory,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.llm_factory = llm_factory
        self.embedding_selector = embedding_selector
        self.rerank_factory = rerank_factory

    async def _run_conversation_path(
        self,
        prepared: PreparedRag,
        *,
        question: str,
        resolved_question: str,
        interpreted_scope: str,
        product_name: str,
        scope_description: str,
        query_usage: dict[str, Any],
        query_trace: dict[str, Any],
        timings: dict[str, int],
    ) -> RagV2Result:
        turn = prepared.turn
        retrieval_trace = {"status": "SKIPPED", "reason": "CONVERSATION_ROUTE"}
        context_trace = {
            "document_ids": [],
            "document_count": 0,
            "image_count": 0,
            "estimated_tokens": 0,
            "mode": "NONE",
            "content_sha256": hashlib.sha256(b"").hexdigest(),
        }
        await self.repository.save_v2_progress(
            turn,
            generation={
                "pipeline": "V2",
                "status": "ANSWER_PENDING",
                "response_path": "CONVERSATION",
                "query_interpreter": query_trace,
                "retrieval": retrieval_trace,
                "context": context_trace,
            },
            selected_context=[],
            timings=timings,
        )

        if interpreted_scope == "OUT_OF_SCOPE":
            result = build_conversation_result(OUT_OF_SCOPE_MESSAGE, out_of_scope=True)
            timings["v2_answer_generator_ms"] = 0
            return RagV2Result(
                result=result,
                usage={
                    "pipeline": "V2",
                    "response_path": "CONVERSATION",
                    "query_interpreter": query_usage,
                },
                trace={
                    "pipeline": "V2",
                    "response_path": "CONVERSATION",
                    "query_interpreter": query_trace,
                    "retrieval": retrieval_trace,
                    "context": context_trace,
                    "answer": {
                        "status": "PASSED",
                        "response_kind": "OUT_OF_SCOPE",
                        "basis": "CONVERSATION",
                        "used_document_count": 0,
                        "used_image_count": 0,
                        "answer_sha256": hashlib.sha256(result.answer.encode("utf-8")).hexdigest(),
                        "fallback_applied": False,
                    },
                },
                timings=timings,
                next_suggestions=[],
            )

        prompt = build_conversation_prompt(
            question=question,
            resolved_question=resolved_question,
            history=turn.history,
            product_name=turn.knowledge_name or product_name,
            scope_description=turn.scope_description or scope_description,
        )
        answer_started = time.perf_counter()
        answer_provider: LlmProvider | None = None
        answer_usage: dict[str, Any] = {}
        fallback_reason: str | None = None
        try:
            answer_provider = self.llm_factory(self.settings, turn.llm)
            generated = await generate_conversation_answer(
                answer_provider,
                prompt=prompt,
                timeout_seconds=min(20.0, self.settings.m3_rag_answer_timeout_seconds),
            )
            answer_usage = dict(answer_provider.usage)
            visible_input = "\n".join(
                [
                    question,
                    turn.knowledge_name or product_name,
                    turn.scope_description or scope_description,
                    *(item.get("content", "") for item in turn.history[-2:]),
                ]
            )
            violations = public_leaks(generated.answer, (), allowed_text=visible_input)
            if (
                "[IMG:" in generated.answer.upper()
                or "HTTP://" in generated.answer.upper()
                or "HTTPS://" in generated.answer.upper()
            ):
                violations.append("conversation_output_contract")
            if violations:
                raise LlmError(
                    "V2_CONVERSATION_OUTPUT_INVALID",
                    "对话回答不符合公开输出约束",
                    retryable=False,
                    detail=",".join(violations)[:500],
                )
            result = build_conversation_result(generated.answer)
        except LlmError as exc:
            fallback_reason = str(getattr(exc, "code", type(exc).__name__))[:100]
            result = build_conversation_result(CONVERSATION_FALLBACK_MESSAGE)
            if answer_provider is not None:
                answer_usage = dict(answer_provider.usage)
        finally:
            if answer_provider is not None:
                await answer_provider.aclose()
        timings["v2_answer_generator_ms"] = round((time.perf_counter() - answer_started) * 1000)

        answer_trace: dict[str, Any] = {
            "status": "DEGRADED" if fallback_reason else "PASSED",
            "response_kind": "ANSWER",
            "basis": "CONVERSATION",
            "used_document_count": 0,
            "used_image_count": 0,
            "answer_sha256": hashlib.sha256(result.answer.encode("utf-8")).hexdigest(),
            "fallback_applied": fallback_reason is not None,
        }
        if fallback_reason is not None:
            answer_trace["fallback_reason"] = fallback_reason

        return RagV2Result(
            result=result,
            usage={
                "pipeline": "V2",
                "response_path": "CONVERSATION",
                "query_interpreter": query_usage,
                "conversation_answer": answer_usage,
            },
            trace={
                "pipeline": "V2",
                "response_path": "CONVERSATION",
                "query_interpreter": query_trace,
                "retrieval": retrieval_trace,
                "context": context_trace,
                "answer": answer_trace,
            },
            timings=timings,
            next_suggestions=[],
        )

    @staticmethod
    def _run_out_of_scope_path(
        *,
        query_usage: dict[str, Any],
        query_trace: dict[str, Any],
        timings: dict[str, int],
    ) -> RagV2Result:
        """Finish a KNOWLEDGE-routed turn that LLM 1 already placed out of scope.

        The interpreter owns the scope decision (see ``apply_scope_authority``), so
        retrieval, document loading and LLM 2 could only be thrown away. The turn
        keeps ``response_path=KNOWLEDGE`` so it still counts as an out-of-scope
        request; ``shortcut`` keeps its near-zero latency out of the technical
        latency samples.
        """

        retrieval_trace = {"status": "SKIPPED", "reason": "OUT_OF_SCOPE"}
        context_trace = {
            "document_ids": [],
            "document_count": 0,
            "image_count": 0,
            "estimated_tokens": 0,
            "mode": "NONE",
            "content_sha256": hashlib.sha256(b"").hexdigest(),
        }
        result = ChatResult(
            response_type=ResponseType.OUT_OF_SCOPE,
            answer_mode=AnswerMode.OUT_OF_SCOPE,
            answer=OUT_OF_SCOPE_MESSAGE,
            content=[ContentBlock(type="text", text=OUT_OF_SCOPE_MESSAGE)],
            disclaimer=None,
            valid_sources=[],
            citation_result={
                "status": "NOT_REQUIRED",
                "pipeline": "V2",
                "response_path": "KNOWLEDGE",
                "shortcut": "OUT_OF_SCOPE",
            },
        )
        timings["v2_answer_generator_ms"] = 0
        return RagV2Result(
            result=result,
            usage={
                "pipeline": "V2",
                "response_path": "KNOWLEDGE",
                "query_interpreter": query_usage,
            },
            trace={
                "pipeline": "V2",
                "response_path": "KNOWLEDGE",
                "query_interpreter": query_trace,
                "retrieval": retrieval_trace,
                "context": context_trace,
                "answer": {
                    "status": "PASSED",
                    "response_kind": "OUT_OF_SCOPE",
                    "basis": "NONE",
                    "scope_authority": {
                        "interpreted_scope": "OUT_OF_SCOPE",
                        "model_response_kind": None,
                        "final_response_kind": "OUT_OF_SCOPE",
                        "conflict_resolved": False,
                    },
                    "used_document_count": 0,
                    "used_document_ids": [],
                    "used_image_count": 0,
                    "answer_sha256": hashlib.sha256(result.answer.encode("utf-8")).hexdigest(),
                    "fallback_applied": False,
                },
            },
            timings=timings,
            next_suggestions=[],
        )

    async def run(
        self,
        prepared: PreparedRag,
        *,
        question: str,
        product_name: str,
        scope_description: str,
        include_images: bool,
    ) -> RagV2Result:
        turn = prepared.turn
        timings = dict(prepared.timings)

        query_started = time.perf_counter()
        # A factory failure (e.g. no usable LLM credential) is deterministic and
        # would fail the answer stage identically, so it must surface here rather
        # than being masked as an LLM 1 degradation that still spends retrieval.
        # `interpret_query` itself converts upstream failures into the degraded
        # history-fallback interpretation.
        query_provider = self.llm_factory(self.settings, turn.llm)
        try:
            query_outcome = await interpret_query(
                query_provider,
                question=question,
                history=turn.history,
                knowledge_name=turn.knowledge_name or product_name,
                scope_description=turn.scope_description or scope_description,
                max_rounds=self.settings.m3_rag_history_max_rounds,
                max_tokens=self.settings.m3_rag_query_interpreter_max_tokens,
                timeout_seconds=self.settings.m3_rag_query_interpreter_timeout_seconds,
            )
            query_usage = dict(query_provider.usage)
        finally:
            await query_provider.aclose()
        timings["v2_query_interpreter_ms"] = round((time.perf_counter() - query_started) * 1000)

        response_path = query_outcome.interpretation.response_path
        query_trace = query_outcome.trace_dict()
        query_trace["response_path"] = response_path

        if response_path == "CONVERSATION":
            return await self._run_conversation_path(
                prepared,
                question=question,
                resolved_question=query_outcome.interpretation.resolved_question,
                interpreted_scope=query_outcome.interpretation.scope,
                product_name=product_name,
                scope_description=scope_description,
                query_usage=query_usage,
                query_trace=query_trace,
                timings=timings,
            )

        if query_outcome.interpretation.scope == "OUT_OF_SCOPE":
            return self._run_out_of_scope_path(
                query_usage=query_usage,
                query_trace=query_trace,
                timings=timings,
            )

        retrieval_started = time.perf_counter()
        candidates, retrieval_trace = await retrieve_documents(
            self.repository,
            settings=self.settings,
            turn=turn,
            question=question,
            interpretation=query_outcome.interpretation,
            previous_document_ids=turn.previous_document_ids,
            embedding_selector=self.embedding_selector,
            rerank_factory=self.rerank_factory,
        )
        timings["v2_document_retrieval_ms"] = round(
            (time.perf_counter() - retrieval_started) * 1000
        )

        context_started = time.perf_counter()
        context = await build_document_context(
            self.repository,
            knowledge_id=turn.knowledge_id,
            index_id=turn.index_id,
            candidates=candidates,
            token_budget=min(
                self.settings.m3_rag_full_context_token_budget,
                self.settings.m3_prompt_token_budget,
            ),
        )
        timings["v2_document_context_ms"] = round((time.perf_counter() - context_started) * 1000)
        query_trace["fallback_retrieval_succeeded"] = bool(
            query_outcome.history_fallback == "APPLIED" and candidates
        )
        context_trace = {
            "document_ids": list(context.document_ids),
            "document_count": len(context.document_ids),
            "image_count": len(context.image_bindings),
            "estimated_tokens": context.estimated_tokens,
            "mode": context.context_mode,
            "content_sha256": hashlib.sha256(context.prompt_context.encode("utf-8")).hexdigest(),
        }
        selected_context_trace = [
            {
                "document_key": document_key,
                "document_id": document_id,
                "content_sha256": hashlib.sha256(document_text.encode("utf-8")).hexdigest(),
            }
            for (document_key, document_text), document_id in zip(
                context.document_evidence,
                context.document_ids,
                strict=True,
            )
        ]
        await self.repository.save_v2_progress(
            turn,
            generation={
                "pipeline": "V2",
                "status": "ANSWER_PENDING",
                "response_path": "KNOWLEDGE",
                "query_interpreter": query_trace,
                "retrieval": retrieval_trace,
                "context": context_trace,
            },
            selected_context=selected_context_trace,
            timings=timings,
        )
        prompt = build_answer_prompt(
            history=turn.history,
            interpretation=query_outcome.interpretation,
            context=context,
            product_name=turn.knowledge_name or product_name,
            scope_description=turn.scope_description or scope_description,
            max_rounds=self.settings.m3_rag_history_max_rounds,
        )

        answer_started = time.perf_counter()
        answer_provider = self.llm_factory(self.settings, turn.llm)
        repair_count = 0
        key_scrub_count = 0
        allowed_public_text = question + "\n" + context.prompt_context
        try:
            generated = await generate_answer(
                answer_provider,
                prompt=prompt,
                max_tokens=self.settings.m3_rag_answer_max_tokens,
                timeout_seconds=self.settings.m3_rag_answer_timeout_seconds,
                thinking_enabled=self.settings.m3_rag_answer_thinking_enabled,
                thinking_budget=self.settings.m3_rag_answer_thinking_budget,
            )
            model_response_kind = generated.response_kind
            generated, scope_authority_applied = apply_scope_authority(
                generated,
                interpretation=query_outcome.interpretation,
                product_name=turn.knowledge_name or product_name,
            )
            generated = normalize_evidence_bindings(generated, context)
            generated, scrubbed = scrub_document_keys(
                generated, context, allowed_text=allowed_public_text
            )
            key_scrub_count += scrubbed
            violations = [
                *public_leaks(
                    generated.answer,
                    context.title_aliases,
                    allowed_text=allowed_public_text,
                ),
                *evidence_violations(generated, context),
            ]
            while violations and repair_count < self.settings.m3_rag_answer_repair_attempts:
                repair_count += 1
                generated = await repair_generated_answer(
                    answer_provider,
                    source_prompt=prompt,
                    violations=violations,
                    document_keys=context.document_keys,
                    image_document_keys={
                        binding.key: binding.document_key for binding in context.image_bindings
                    },
                    max_tokens=self.settings.m3_rag_answer_max_tokens,
                    timeout_seconds=self.settings.m3_rag_answer_timeout_seconds,
                    thinking_enabled=self.settings.m3_rag_answer_thinking_enabled,
                    thinking_budget=self.settings.m3_rag_answer_thinking_budget,
                )
                model_response_kind = generated.response_kind
                generated, repair_scope_authority_applied = apply_scope_authority(
                    generated,
                    interpretation=query_outcome.interpretation,
                    product_name=turn.knowledge_name or product_name,
                )
                scope_authority_applied = scope_authority_applied or repair_scope_authority_applied
                generated = normalize_evidence_bindings(generated, context)
                generated, scrubbed = scrub_document_keys(
                    generated, context, allowed_text=allowed_public_text
                )
                key_scrub_count += scrubbed
                violations = [
                    *public_leaks(
                        generated.answer,
                        context.title_aliases,
                        allowed_text=allowed_public_text,
                    ),
                    *evidence_violations(generated, context),
                ]
            if violations:
                raise LlmError(
                    (
                        "V2_PUBLIC_LEAK"
                        if any(
                            item.startswith(
                                (
                                    "internal_url:",
                                    "internal_resource:",
                                    "management_path:",
                                    "prompt_marker:",
                                )
                            )
                            for item in violations
                        )
                        else "V2_EVIDENCE_INVALID"
                    ),
                    "回答生成失败，请稍后重试",
                    retryable=True,
                    detail=",".join(violations)[:500],
                )
            result = validate_generated_answer(
                generated,
                context=context,
                include_images=include_images,
                allowed_text=allowed_public_text,
            )
            answer_usage = dict(answer_provider.usage)
        finally:
            await answer_provider.aclose()
        timings["v2_answer_generator_ms"] = round((time.perf_counter() - answer_started) * 1000)
        result.citation_result["response_path"] = "KNOWLEDGE"

        document_id_by_key = dict(zip(context.document_keys, context.document_ids, strict=True))
        used_document_ids = [
            document_id_by_key[key]
            for key in generated.used_document_keys
            if key in document_id_by_key
        ]
        observed_parameters = parameter_observations(
            result.answer,
            question=question,
            context=context,
        )
        diagnostic_reason: str | None = None
        if scope_authority_applied:
            diagnostic_reason = TraceDiagnosticReason.SCOPE_CONFLICT.value
        elif generated.response_kind == "ASK_ONE":
            diagnostic_reason = TraceDiagnosticReason.NEEDS_KEY_CONDITION.value

        generation_trace: dict[str, Any] = {
            "pipeline": "V2",
            "response_path": "KNOWLEDGE",
            "query_interpreter": query_trace,
            "retrieval": retrieval_trace,
            "context": context_trace,
            "answer": {
                "thinking_requested": self.settings.m3_rag_answer_thinking_enabled,
                "thinking_budget": self.settings.m3_rag_answer_thinking_budget,
                "response_kind": generated.response_kind,
                "scope_authority": {
                    "interpreted_scope": query_outcome.interpretation.scope,
                    "model_response_kind": model_response_kind,
                    "final_response_kind": generated.response_kind,
                    "conflict_resolved": scope_authority_applied,
                },
                "basis": generated.basis,
                "used_document_count": len(generated.used_document_keys),
                "used_document_ids": used_document_ids,
                "used_image_count": len(generated.used_image_keys),
                "public_leak_repair_count": repair_count,
                "document_key_scrub_count": key_scrub_count,
                "next_suggestions_discarded": len(generated.next_suggestions),
                "parameter_observations": observed_parameters,
                "unsupported_parameter_count": sum(
                    item["status"] == "UNSUPPORTED" for item in observed_parameters
                ),
                "answer_sha256": hashlib.sha256(result.answer.encode("utf-8")).hexdigest(),
            },
        }
        if diagnostic_reason is not None:
            generation_trace["diagnostic_reason"] = diagnostic_reason

        return RagV2Result(
            result=result,
            usage={
                "pipeline": "V2",
                "response_path": "KNOWLEDGE",
                "query_interpreter": query_usage,
                "answer": answer_usage,
            },
            trace=generation_trace,
            timings=timings,
            # The field stays in the public contract but is always empty (ADR-0040): the
            # model-written suggestions were unreliable (see suggestions-review.md), so
            # they are dropped here instead of changing the generation prompt.
            next_suggestions=[],
        )
