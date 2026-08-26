# ruff: noqa: E501
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from app.common.errors import ApiError
from app.core.config import Settings
from app.rag.doc_router import ACTION_CLARIFY, ACTION_DELIVER
from app.rag.llm import LlmError, create_llm_provider
from app.support.document_delivery import deliver_document
from app.support.document_router import SupportDocumentRouter
from app.support.escalation import build_escalation
from app.support.focused_assistant import FocusedSupportAssistant
from app.support.models import ProblemDecision, ProblemProfile, SupportTurn
from app.support.problem_resolver import ProblemResolver
from app.support.repository import SupportRepository
from app.support.workflow_state import apply_workflow_update, read_workflow, reset_for_document


def _elapsed(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _sse(event: str, payload: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n".encode()


def _failure_trace(
    turn: SupportTurn,
    code: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "support_version": 1,
        "failure": {"code": code},
    }
    if details:
        result["failure"]["details"] = details
    if turn.focus_document_id is not None:
        result["focus_document_id"] = str(turn.focus_document_id)
    if code in {
        "INVALID_EVIDENCE",
        "MISSING_EVIDENCE",
        "INVALID_IMAGE_REFERENCE",
        "UNSUPPORTED_PRODUCT_PARAMETER",
    }:
        result["validation_result"] = {"valid": False, "code": code}
    return result


def _failure_stage(code: str, current_stage: str) -> str:
    # Direct delivery is a distinct product stage.  A missing document or
    # image discovered there must not be reported as the focused-chat loading
    # stage merely because both paths use the same repository errors.
    if current_stage == "DOCUMENT_DELIVERY":
        return current_stage
    if code in {
        "INVALID_EVIDENCE",
        "MISSING_EVIDENCE",
        "INVALID_IMAGE_REFERENCE",
        "UNSUPPORTED_PRODUCT_PARAMETER",
    }:
        return "VALIDATION"
    if code == "WORKFLOW_CONFLICT":
        return "WORKFLOW"
    if code in {
        "DOCUMENT_NOT_FOUND",
        "DOCUMENT_NOT_SUPPORT_READY",
        "IMAGE_ASSET_MISSING",
    }:
        return "DOCUMENT_LOADING"
    if code == "MODEL_NOT_CONFIGURED":
        return "LLM"
    return current_stage


class TechnicalSupportService:
    """The only orchestration entry point for formal technical-support chat."""

    def __init__(
        self,
        repository: SupportRepository,
        settings: Settings,
        *,
        document_router: SupportDocumentRouter | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.document_router = document_router
        if self.document_router is None and isinstance(repository, SupportRepository):
            self.document_router = SupportDocumentRouter(repository, settings)

    def _base_response(self, turn: SupportTurn, started: float) -> dict[str, Any]:
        return {
            "message_id": str(turn.assistant_message_id),
            "conversation_id": str(turn.conversation_id),
            "trace_id": str(turn.trace_id),
            "index_id": str(turn.index_id),
            "content": [],
            "images": [],
            "sections": [],
            "references": [],
            "usage": {},
            "latency_ms": _elapsed(started),
        }

    @staticmethod
    def _reference(document_id: UUID, title: str) -> dict[str, Any]:
        return {
            "document_id": str(document_id),
            "title": title,
            "section_paths": [],
            "source_ids": [],
            "download_url": f"/api/v1/documents/{document_id}/content",
        }

    async def _deliver(
        self,
        *,
        turn: SupportTurn,
        profile: ProblemProfile | None,
        document_id: UUID,
        method: str,
        confidence: float,
        include_images: bool,
        started: float,
    ) -> tuple[dict[str, Any], dict[str, Any], UUID | None]:
        await self.repository.set_stage(turn, "DOCUMENT_DELIVERY")
        document = await self.repository.get_document(
            knowledge_id=turn.knowledge_id,
            index_id=turn.index_id,
            document_id=document_id,
            problem_code=profile.problem_code if profile else None,
        )
        content, images = deliver_document(document, include_images=include_images)
        response = self._base_response(turn, started)
        response.update(
            {
                "response_type": "DOCUMENT_DELIVERED",
                "answer": f"【已提供文档】《{document.title}》",
                "document": {
                    "id": str(document.id),
                    "problem_code": profile.problem_code if profile else None,
                    "title": document.title,
                    "download_url": f"/api/v1/documents/{document.id}/content",
                },
                "routing": {"method": method, "confidence": confidence},
                "content": content,
                "images": images,
                "references": [self._reference(document.id, document.title)],
                "latency_ms": _elapsed(started),
            }
        )
        context = reset_for_document(
            str(document.id), profile.problem_code if profile else "", str(turn.index_id)
        )
        return response, context, document.id

    async def handle(
        self,
        *,
        knowledge_id: UUID,
        conversation_id: UUID | None,
        question: str,
        request_id: str,
        user_id: str | None = None,
        problem_code: str | None = None,
        document_id: UUID | None = None,
        choice_id: str | None = None,
        include_images: bool = True,
        idempotency_key: str | None = None,
        return_internal_ids: bool = False,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        clean_question = question.strip()
        if not clean_question:
            raise ApiError("VALIDATION_ERROR", "问题不能为空", 422)
        request_hash = self.repository.request_hash(
            question=clean_question,
            conversation_id=conversation_id,
            document_id=document_id,
            problem_code=problem_code,
            choice_id=choice_id,
        )
        turn = await self.repository.begin_turn(
            knowledge_id=knowledge_id,
            conversation_id=conversation_id,
            question=clean_question,
            request_id=request_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if turn.replay is not None:
            replay = dict(turn.replay)
            if not return_internal_ids:
                replay.pop("_fallback_message_pair", None)
                replay.pop("_gateway_response", None)
            return replay
        current_stage = "PROFILE_RESOLUTION"
        try:
            # A choice is the only path that can confirm a pending document
            # switch. It precedes focus-based assistant behavior.
            profiles = await self.repository.list_profiles(
                knowledge_id, enabled_only=True, ready_only=True
            )
            # Profile resolution is intentionally deterministic.  The RAG
            # fallback below handles natural-language misses using document
            # evidence, avoiding an online LLM scan over the full catalog.
            resolver = ProblemResolver()
            pending_raw = turn.chat_context.get("pending_options", [])
            pending = pending_raw if isinstance(pending_raw, list) else []
            if choice_id:
                selected = await resolver.resolve(
                    question=clean_question,
                    profiles=profiles,
                    pending_options=pending,
                    choice_id=choice_id,
                )
                selected_profile: ProblemProfile | None
                selected_document_id: UUID
                selected_method: str
                selected_confidence: float
                if selected.decision == ProblemDecision.MATCHED and selected.profile is not None:
                    selected_profile = selected.profile
                    selected_document_id = selected.profile.document_id
                    selected_method = selected.method
                    selected_confidence = selected.confidence
                else:
                    option = next(
                        (
                            item
                            for item in pending if isinstance(item, dict)
                            and item.get("choice_id") == choice_id
                            and item.get("document_id")
                        ),
                        None,
                    )
                    if option is None:
                        raise ApiError("INVALID_CHOICE", "选择不属于当前会话的待确认文档", 409)
                    try:
                        selected_document_id = UUID(str(option["document_id"]))
                    except (TypeError, ValueError) as exc:
                        raise ApiError("INVALID_CHOICE", "待确认文档无效", 409) from exc
                    selected_profile = await self.repository.get_profile_by_document(
                        knowledge_id, selected_document_id
                    )
                    selected_method = str(option.get("routing_method") or "DOCUMENT_CHOICE")
                    selected_confidence = float(option.get("confidence") or 0.0)
                current_stage = "DOCUMENT_DELIVERY"
                response, context, delivered_focus = await self._deliver(
                    turn=turn,
                    profile=selected_profile,
                    document_id=selected_document_id,
                    method=selected_method,
                    confidence=selected_confidence,
                    include_images=include_images,
                    started=started,
                )
                response["routing"]["source"] = "DOCUMENT_CHOICE"
                await self.repository.finish_turn(
                    turn,
                    response=response,
                    context=context,
                    focus_document_id=delivered_focus,
                    trace={
                        "support_version": 1,
                        "problem_resolution": {
                            "method": selected_method,
                            "decision": "MATCHED",
                        },
                    },
                )
                return response

            if turn.focus_document_id is None:
                focus: UUID | None = None
                if document_id is not None:
                    profile = await self.repository.get_profile_by_document(
                        knowledge_id, document_id
                    )
                    if profile is None or not profile.support_ready:
                        raise ApiError(
                            "DOCUMENT_NOT_SUPPORT_READY",
                            "文档尚未满足技术支持发布条件",
                            409,
                        )
                    current_stage = "DOCUMENT_DELIVERY"
                    response, context, focus = await self._deliver(
                        turn=turn,
                        profile=profile,
                        document_id=document_id,
                        method="DOCUMENT_ID",
                        confidence=1.0,
                        include_images=include_images,
                        started=started,
                    )
                    response["routing"]["source"] = "EXPLICIT"
                elif problem_code is not None:
                    profile = await self.repository.get_profile_by_code(knowledge_id, problem_code)
                    if profile is None:
                        raise ApiError("PROBLEM_CODE_NOT_FOUND", "问题编码不存在或未启用", 404)
                    if not profile.support_ready:
                        raise ApiError(
                            "DOCUMENT_NOT_SUPPORT_READY",
                            "问题对应文档尚未满足技术支持发布条件",
                            409,
                        )
                    current_stage = "DOCUMENT_DELIVERY"
                    response, context, focus = await self._deliver(
                        turn=turn,
                        profile=profile,
                        document_id=profile.document_id,
                        method="PROBLEM_CODE",
                        confidence=1.0,
                        include_images=include_images,
                        started=started,
                    )
                    response["routing"]["source"] = "EXPLICIT"
                else:
                    resolution = await resolver.resolve(question=clean_question, profiles=profiles)
                    if resolution.decision == ProblemDecision.MATCHED and resolution.profile:
                        current_stage = "DOCUMENT_DELIVERY"
                        response, context, focus = await self._deliver(
                            turn=turn,
                            profile=resolution.profile,
                            document_id=resolution.profile.document_id,
                            method=resolution.method,
                            confidence=resolution.confidence,
                            include_images=include_images,
                            started=started,
                        )
                        response["routing"]["source"] = "PROFILE"
                    else:
                        route = None
                        if self.settings.support_rag_fallback_enabled and self.document_router:
                            current_stage = "RAG_DOCUMENT_RESOLUTION"
                            await self.repository.set_stage(turn, current_stage)
                            route = await self.document_router.resolve(
                                knowledge_id=knowledge_id,
                                index_id=turn.index_id,
                                question=clean_question,
                            )
                        if route is not None and route.action == ACTION_DELIVER and route.document_id:
                            profile = await self.repository.get_profile_by_document(
                                knowledge_id, route.document_id
                            )
                            current_stage = "DOCUMENT_DELIVERY"
                            response, context, focus = await self._deliver(
                                turn=turn,
                                profile=profile,
                                document_id=route.document_id,
                                method="RAG_FALLBACK",
                                confidence=route.confidence,
                                include_images=include_images,
                                started=started,
                            )
                            response["routing"] = {
                                **response["routing"],
                                "source": "RAG_FALLBACK",
                                "document_routing": route.trace.get("document_routing", {}),
                            }
                        else:
                            response = self._base_response(turn, started)
                            if route is not None and route.action == ACTION_CLARIFY:
                                profile_by_document = {
                                    str(profile.document_id): profile for profile in profiles
                                }
                                public_options: list[dict[str, Any]] = []
                                pending_options: list[dict[str, Any]] = []
                                for number, item in enumerate(route.options, start=1):
                                    profile = profile_by_document.get(str(item["document_id"]))
                                    option = {
                                        "choice_id": f"rag_{number}",
                                        "document_id": str(item["document_id"]),
                                        "problem_code": profile.problem_code if profile else None,
                                        "title": str(item["title"]),
                                    }
                                    public_options.append(option)
                                    pending_options.append(
                                        {
                                            **option,
                                            "routing_method": "RAG_FALLBACK_CHOICE",
                                            "confidence": route.confidence,
                                        }
                                    )
                                response.update(
                                    {
                                        "response_type": "CLARIFICATION_REQUIRED",
                                        "answer": "找到多个可能相关的文档，请确认要处理的文档。",
                                        "clarification": {
                                            "type": "DOCUMENT_CHOICE",
                                            "message": "找到多个可能相关的文档，请确认要处理的文档。",
                                            "choices": public_options,
                                        },
                                        "routing": {
                                            "source": "RAG_FALLBACK",
                                            "method": "RAG_FALLBACK",
                                            "decision": ACTION_CLARIFY,
                                            "confidence": route.confidence,
                                            "document_routing": route.trace.get("document_routing", {}),
                                        },
                                        "latency_ms": _elapsed(started),
                                    }
                                )
                                context = {"support_version": 1, "pending_options": pending_options}
                            elif resolution.choices:
                                options = [
                                    {
                                        "choice_id": f"match_{number}",
                                        "document_id": str(item.document_id),
                                        "problem_code": item.problem_code,
                                        "title": item.canonical_question,
                                    }
                                    for number, item in enumerate(resolution.choices, start=1)
                                ]
                                response.update(
                                    {
                                        "response_type": "CLARIFICATION_REQUIRED",
                                        "answer": "请确认你要处理的文档。",
                                        "clarification": {
                                            "type": "DOCUMENT_CHOICE",
                                            "message": "请确认你要处理的文档。",
                                            "choices": options,
                                        },
                                        "routing": {
                                            "source": "PROFILE",
                                            "method": resolution.method,
                                            "decision": resolution.decision.value,
                                            "rag_fallback": route.trace if route is not None else None,
                                        },
                                        "latency_ms": _elapsed(started),
                                    }
                                )
                                context = {"support_version": 1, "pending_options": options}
                            else:
                                response.update(
                                    {
                                        "response_type": "NO_MATCH",
                                        "answer": "当前知识库未匹配到对应文档。请确认已选择正确的知识库，并补充设备类型、具体告警或完整现象。",
                                        "clarification": {
                                            "type": "NEED_MORE_INFORMATION",
                                            "message": "请确认已选择正确的知识库，并补充设备类型、具体告警或完整现象。",
                                            "choices": [],
                                        },
                                        "routing": {
                                            "source": (
                                                route.trace.get("source")
                                                if route is not None
                                                else "PROFILE"
                                            ),
                                            "method": "RAG_FALLBACK" if route is not None else resolution.method,
                                            "decision": "NO_MATCH",
                                            "rag_fallback": route.trace if route is not None else None,
                                        },
                                        "latency_ms": _elapsed(started),
                                    }
                                )
                                context = {"support_version": 1}
                        if route is None or route.action != ACTION_DELIVER:
                            focus = None
                if response.get("response_type") == "NO_MATCH":
                    response["_fallback_message_pair"] = {
                        "user_message_id": str(turn.user_message_id),
                        "assistant_message_id": str(turn.assistant_message_id),
                    }
                await self.repository.finish_turn(
                    turn,
                    response=response,
                    context=context,
                    focus_document_id=focus,
                    trace={
                        "support_version": 1,
                        "problem_resolution": response.get(
                            "routing", {"decision": response["response_type"]}
                        ),
                    },
                )
                if not return_internal_ids:
                    response.pop("_fallback_message_pair", None)
                return response

            # Focused turns load the complete, frozen document directly and
            # never invoke the Generic RAG stack.
            current_stage = "DOCUMENT_LOADING"
            await self.repository.set_stage(turn, current_stage)
            profile = await self.repository.get_profile_by_document(
                knowledge_id, turn.focus_document_id
            )
            document = await self.repository.get_document(
                knowledge_id=knowledge_id,
                index_id=turn.index_id,
                document_id=turn.focus_document_id,
                problem_code=profile.problem_code if profile else None,
            )
            current_stage = "LLM"
            await self.repository.set_stage(turn, current_stage)
            llm = await self.repository.get_llm()
            provider = create_llm_provider(self.settings, llm)
            try:
                assistant = FocusedSupportAssistant(
                    provider,
                    max_tokens=self.settings.support_llm_max_tokens,
                    temperature=self.settings.support_llm_temperature,
                    timeout_seconds=self.settings.m3_provider_timeout_seconds,
                    prompt_token_budget=self.settings.m3_prompt_token_budget,
                )
                draft, evidence, images = await assistant.answer(
                    question=clean_question,
                    document=document,
                    workflow=turn.chat_context,
                    history=turn.history,
                )
                current_stage = "VALIDATION"
                await self.repository.set_stage(turn, current_stage)
                prompt_usage = assistant.prompt_usage
                # The model can only propose a query. Resolve it again and
                # require an explicit subsequent choice before changing focus.
                if draft.document_switch and draft.document_switch.get("query"):
                    switch = await resolver.resolve(
                        question=draft.document_switch["query"], profiles=profiles
                    )
                    if (
                        switch.decision == ProblemDecision.MATCHED
                        and switch.profile
                        and switch.profile.document_id != document.id
                    ):
                        option = {
                            "choice_id": "switch_1",
                            "document_id": str(switch.profile.document_id),
                            "problem_code": switch.profile.problem_code,
                            "title": switch.profile.canonical_question,
                        }
                        response = self._base_response(turn, started)
                        response.update(
                            {
                                "response_type": "CLARIFICATION_REQUIRED",
                                "answer": f"你现在的问题更接近《{switch.profile.canonical_question}》，是否切换？",
                                "clarification": {
                                    "type": "CONFIRM_DOCUMENT_SWITCH",
                                    "message": f"你现在的问题更接近《{switch.profile.canonical_question}》，是否切换？",
                                    "choices": [option],
                                },
                                "document": {
                                    "id": str(document.id),
                                    "problem_code": document.problem_code,
                                    "title": document.title,
                                },
                                "latency_ms": _elapsed(started),
                            }
                        )
                        context = dict(turn.chat_context)
                        context["pending_options"] = [option]
                        await self.repository.finish_turn(
                            turn,
                            response=response,
                            context=context,
                            focus_document_id=document.id,
                            trace={
                                "support_version": 1,
                                "document_switch": {
                                    "query": draft.document_switch["query"],
                                    "candidate": option,
                                },
                            },
                        )
                        return response

                current_stage = "WORKFLOW"
                await self.repository.set_stage(turn, current_stage)
                context = apply_workflow_update(turn.chat_context, draft.workflow_update)
                response = self._base_response(turn, started)
                selected_images = [
                    {"image_asset_id": asset_id, "url": f"/api/v1/image-assets/{asset_id}/content"}
                    for short_id, asset_id in images.items()
                    if short_id in draft.image_ids
                ]
                response.update(
                    {
                        "response_type": "ESCALATION_REQUIRED"
                        if draft.escalation.required
                        else "ANSWERED",
                        "support_mode": draft.mode.value,
                        "answer": draft.answer,
                        "sections": [section.model_dump(mode="json") for section in draft.sections],
                        "images": selected_images,
                        "document": {
                            "id": str(document.id),
                            "problem_code": document.problem_code,
                            "title": document.title,
                        },
                        "references": [self._reference(document.id, document.title)],
                        "workflow": read_workflow(context).model_dump(mode="json"),
                        "usage": {**provider.usage, "support_prompt": prompt_usage},
                        "latency_ms": _elapsed(started),
                    }
                )
                if prompt_usage["truncated"]:
                    response["disclaimer"] = (
                        "当前文档超过模型上下文预算，本轮仅使用与问题最相关的完整文档元素；"
                        "未覆盖部分不会作为回答依据。"
                    )
                if draft.escalation.required:
                    response["escalation"] = build_escalation(
                        reason=draft.escalation.reason or "当前文档不足以安全处理该问题",
                        problem_code=document.problem_code,
                        workflow=context,
                        trace_id=str(turn.trace_id),
                    )
                else:
                    response["escalation"] = {"required": False, "reason": None}
                await self.repository.finish_turn(
                    turn,
                    response=response,
                    context=context,
                    focus_document_id=document.id,
                    trace={
                        "support_version": 1,
                        "focus_document_id": str(document.id),
                        "support_mode": draft.mode.value,
                        "document_claims": [
                            section.evidence_element_ids
                            for section in draft.sections
                            if section.type.value == "DOCUMENT_FACT"
                        ],
                        "workflow_before": turn.chat_context,
                        "workflow_after": context,
                        "validation_result": {"valid": True},
                        "prompt_context": prompt_usage,
                        "general_guidance_count": sum(
                            section.type.value == "GENERAL_GUIDANCE" for section in draft.sections
                        ),
                        "risk_flags": [
                            section.risk_level.value
                            for section in draft.sections
                            if section.risk_level is not None
                        ],
                        "escalation": response["escalation"],
                        "high_risk_intercepted": bool(
                            draft.escalation.required
                            and any(
                                section.risk_level is not None
                                and section.risk_level.value in {"HIGH", "CRITICAL"}
                                for section in draft.sections
                            )
                        ),
                        "model_calls": 1,
                        "structured_output_retries": max(
                            0, int(provider.usage.get("structured_attempts", 1)) - 1
                        ),
                    },
                    usage=response["usage"],
                )
                return response
            finally:
                await provider.aclose()
        except ApiError as exc:
            await self.repository.fail_turn(
                turn,
                code=exc.code,
                message=exc.message,
                trace=_failure_trace(turn, exc.code, details=exc.details or None),
                stage=_failure_stage(exc.code, current_stage),
                detail=exc.details or None,
            )
            raise
        except LlmError as exc:
            await self.repository.fail_turn(
                turn,
                code=exc.code,
                message=exc.public_message,
                trace=_failure_trace(
                    turn, exc.code, details={"retryable": exc.retryable, "detail": exc.detail}
                ),
                stage="LLM",
                retryable=exc.retryable,
                detail=exc.detail,
            )
            raise ApiError(exc.code, exc.public_message, 502) from exc
        except Exception as exc:
            await self.repository.fail_turn(
                turn,
                code="INTERNAL_ERROR",
                message="技术支持处理失败",
                trace=_failure_trace(turn, "INTERNAL_ERROR"),
                stage=current_stage,
                detail=f"{type(exc).__name__}: {exc}"[:500],
            )
            raise

    async def save_gateway_response(
        self,
        *,
        knowledge_id: UUID,
        idempotency_key: str | None,
        support_trace_id: UUID,
        response: dict[str, Any],
    ) -> None:
        if idempotency_key is None:
            return
        await self.repository.save_gateway_response(
            knowledge_id=knowledge_id,
            idempotency_key=idempotency_key,
            support_trace_id=support_trace_id,
            response=response,
        )

    async def stream(self, **kwargs: Any) -> AsyncIterator[bytes]:
        yield _sse("status", {"stage": "support_processing"})
        try:
            result = await self.handle(**kwargs)
        except ApiError as exc:
            # The HTTP response has already started.  Returning a regular
            # FastAPI error here would disconnect the browser without a useful
            # message, so keep the error in the SSE protocol.
            yield _sse(
                "error",
                {"code": exc.code, "message": exc.message, "status_code": exc.status_code},
            )
            return
        yield _sse(
            "trace",
            {
                "trace_id": result["trace_id"],
                "conversation_id": result["conversation_id"],
            },
        )
        answer = str(result.get("answer", ""))
        for offset in range(0, len(answer), 24):
            yield _sse("message", {"delta": answer[offset : offset + 24]})
            await asyncio.sleep(0)
        yield _sse("done", result)
