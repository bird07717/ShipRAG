from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.rag.llm import LlmError, LlmProvider
from app.rag.models import ModelSnapshot, PreparedRag
from app.rag.routing import ChatResult
from app.rag.v2.answer_generator import (
    build_answer_prompt,
    generate_answer,
    repair_generated_answer,
)
from app.rag.v2.document_context import build_document_context
from app.rag.v2.document_retrieval import EmbeddingSelector, RerankFactory, retrieve_documents
from app.rag.v2.query_interpreter import (
    QueryInterpretationOutcome,
    fallback_interpretation,
    interpret_query,
)
from app.rag.v2.validation import (
    evidence_violations,
    normalize_evidence_bindings,
    parameter_observations,
    public_leaks,
    validate_generated_answer,
)

LlmFactory = Callable[[Settings, ModelSnapshot], LlmProvider]


@dataclass(frozen=True, slots=True)
class RagV2Result:
    result: ChatResult
    prompt: str
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
        query_provider: LlmProvider | None = None
        try:
            query_provider = self.llm_factory(self.settings, turn.llm)
            query_outcome = await interpret_query(
                query_provider,
                question=question,
                history=turn.history,
                knowledge_name=turn.knowledge_name or product_name,
                scope_description=turn.scope_description or scope_description,
                max_rounds=self.settings.m3_rag_history_max_rounds,
                variant_limit=self.settings.m3_rag_query_variant_limit,
                max_tokens=self.settings.m3_rag_query_planner_max_tokens,
                timeout_seconds=self.settings.m3_rag_query_planner_timeout_seconds,
            )
            query_usage = dict(query_provider.usage)
        except Exception as exc:
            query_outcome = QueryInterpretationOutcome(
                "DEGRADED",
                fallback_interpretation(question),
                round((time.perf_counter() - query_started) * 1000),
                reason=str(getattr(exc, "code", type(exc).__name__))[:100],
            )
            query_usage = {}
        finally:
            if query_provider is not None:
                await query_provider.aclose()
        timings["v2_query_interpreter_ms"] = round((time.perf_counter() - query_started) * 1000)

        retrieval_started = time.perf_counter()
        candidates, retrieval_trace = await retrieve_documents(
            self.repository,
            settings=self.settings,
            turn=turn,
            question=question,
            interpretation=query_outcome.interpretation,
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
        query_trace = query_outcome.trace_dict()
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
                "query_interpreter": query_trace,
                "retrieval": retrieval_trace,
                "context": context_trace,
            },
            selected_context=selected_context_trace,
            timings=timings,
        )
        prompt = build_answer_prompt(
            question=question,
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
        allowed_public_text = question + "\n" + context.prompt_context
        try:
            generated = await generate_answer(
                answer_provider,
                prompt=prompt,
                max_tokens=self.settings.m3_rag_answer_max_tokens,
                timeout_seconds=self.settings.m3_rag_answer_timeout_seconds,
            )
            generated = normalize_evidence_bindings(generated, context)
            violations = [
                *public_leaks(
                    generated.answer,
                    context.title_aliases,
                    allowed_text=allowed_public_text,
                ),
                *evidence_violations(generated, context),
            ]
            while violations and repair_count < self.settings.m3_rag_title_leak_repair_attempts:
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
                )
                generated = normalize_evidence_bindings(generated, context)
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

        return RagV2Result(
            result=result,
            prompt=prompt,
            usage={"pipeline": "V2", "query_interpreter": query_usage, "answer": answer_usage},
            trace={
                "pipeline": "V2",
                "query_interpreter": query_trace,
                "retrieval": retrieval_trace,
                "context": context_trace,
                "answer": {
                    "response_kind": generated.response_kind,
                    "basis": generated.basis,
                    "used_document_count": len(generated.used_document_keys),
                    "used_document_ids": used_document_ids,
                    "used_image_count": len(generated.used_image_keys),
                    "public_leak_repair_count": repair_count,
                    "parameter_observations": observed_parameters,
                    "unsupported_parameter_count": sum(
                        item["status"] == "UNSUPPORTED" for item in observed_parameters
                    ),
                    "answer_sha256": hashlib.sha256(result.answer.encode("utf-8")).hexdigest(),
                },
            },
            timings=timings,
            next_suggestions=generated.next_suggestions,
        )
