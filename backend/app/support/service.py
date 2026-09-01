# ruff: noqa: E501
from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from app.common.errors import ApiError
from app.core.config import Settings
from app.rag.llm import LlmError, create_llm_provider
from app.rag.models import RetrievalConfig
from app.support.document_delivery import deliver_document
from app.support.document_router import (
    ACTION_CLARIFY,
    ACTION_DELIVER,
    ACTION_OFFER_SWITCH,
    ACTION_STAY,
    SupportDocumentRouter,
)
from app.support.escalation import build_escalation
from app.support.focused_assistant import FocusedSupportAssistant
from app.support.models import ProblemDecision, ProblemProfile, SupportTurn
from app.support.problem_resolver import ProblemResolver
from app.support.repository import SupportRepository
from app.support.workflow_state import (
    apply_workflow_update,
    discovery_context,
    read_workflow,
    reset_for_document,
)


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


_DISCOVERY_FOLLOW_UP = re.compile(
    r"^(?:还是|仍然|依然|然后|接着|下一步|继续|再|这个|那个|它|该|上述|上面|前面|刚才)"
)


def _discovery_routing_query(question: str, history: list[dict[str, str]]) -> str:
    """Add one recent user turn only for an obviously elliptical follow-up."""
    normalized = "".join(question.split())
    if len(normalized) > 16 or not _DISCOVERY_FOLLOW_UP.search(normalized):
        return question
    previous = next(
        (
            str(item.get("content", "")).strip()
            for item in reversed(history)
            if str(item.get("role", "")).upper() == "USER" and str(item.get("content", "")).strip()
        ),
        "",
    )
    if not previous or previous == question:
        return question
    return f"{previous}。{question}"


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
        switch_origin = None
        if turn.focus_document_id is not None and document.id != turn.focus_document_id:
            discovery = turn.chat_context.get("discovery", {})
            if isinstance(discovery, dict):
                value = discovery.get("last_clarification_question")
                if isinstance(value, str) and value.strip():
                    switch_origin = value.strip()
        context = reset_for_document(
            str(document.id),
            profile.problem_code if profile else "",
            str(turn.index_id),
            switch_origin_question=switch_origin,
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
        retrieval_config: RetrievalConfig | None = None,
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
                knowledge_id,
                enabled_only=True,
                ready_only=True,
                index_id=turn.index_id,
            )
            # Profile resolution is intentionally deterministic.  The RAG
            # fallback below handles natural-language misses using document
            # evidence, avoiding an online LLM scan over the full catalog.
            resolver = ProblemResolver(settings=self.settings)
            # Pending choices are conversation state, not model workflow
            # memory.  Bind V1 candidates to the focus epoch that produced
            # them so an old switch prompt cannot change a newer focus.
            pending_source = turn.pending_switch_options
            if not pending_source:
                # Read pre-V1 state during the migration window. New writes
                # never mirror candidates back into chat_context.
                legacy_pending = turn.chat_context.get("pending_options", [])
                pending_source = legacy_pending if isinstance(legacy_pending, list) else []
            pending = [
                item
                for item in pending_source
                if isinstance(item, dict)
                and (
                    item.get("origin_focus_epoch") is None
                    or item.get("origin_focus_epoch") == turn.focus_epoch
                )
            ]
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
                            for item in pending
                            if isinstance(item, dict)
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
                        knowledge_id, selected_document_id, index_id=turn.index_id
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
                        knowledge_id, document_id, index_id=turn.index_id
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
                    profile = await self.repository.get_profile_by_code(
                        knowledge_id, problem_code, index_id=turn.index_id
                    )
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
                        profile_resolution = {
                            "method": resolution.method,
                            "decision": resolution.decision.value,
                            "confidence": resolution.confidence,
                        }
                        route = None
                        if self.settings.support_rag_fallback_enabled and self.document_router:
                            current_stage = "RAG_DOCUMENT_RESOLUTION"
                            await self.repository.set_stage(turn, current_stage)
                            route = await self.document_router.resolve(
                                knowledge_id=knowledge_id,
                                index_id=turn.index_id,
                                question=_discovery_routing_query(clean_question, turn.history),
                                retrieval=retrieval_config,
                                profiles=profiles,
                            )
                        if (
                            route is not None
                            and route.action == ACTION_DELIVER
                            and route.document_id
                        ):
                            profile = await self.repository.get_profile_by_document(
                                knowledge_id, route.document_id, index_id=turn.index_id
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
                                "profile_resolution": profile_resolution,
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
                                            "profile_resolution": profile_resolution,
                                            "document_routing": route.trace.get(
                                                "document_routing", {}
                                            ),
                                        },
                                        "latency_ms": _elapsed(started),
                                    }
                                )
                                pending_options = [
                                    {
                                        **option,
                                        "origin_focus_epoch": turn.focus_epoch,
                                        "origin_document_id": None,
                                    }
                                    for option in pending_options
                                ]
                                context = discovery_context(
                                    turn.chat_context,
                                    last_clarification_question=clean_question,
                                )
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
                                            "method": "RAG_FALLBACK"
                                            if route is not None
                                            else resolution.method,
                                            "decision": "NO_MATCH",
                                            "profile_resolution": profile_resolution,
                                            "rag_fallback": route.trace
                                            if route is not None
                                            else None,
                                        },
                                        "latency_ms": _elapsed(started),
                                    }
                                )
                                pending_options = []
                                context = discovery_context(turn.chat_context)
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
                    pending_switch_options=(
                        pending_options
                        if response["response_type"] == "CLARIFICATION_REQUIRED"
                        else []
                    ),
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
            # never invoke the Generic RAG answer stack. Before asking the
            # focused assistant, run the closed Profile/document-only router
            # once for an obvious topic change. A candidate is always a
            # proposal: the current focus is retained until choice_id confirms
            # it on a following turn.
            switch_options: list[dict[str, Any]] = []
            focused_resolution = await resolver.resolve(question=clean_question, profiles=profiles)
            if (
                focused_resolution.decision == ProblemDecision.MATCHED
                and focused_resolution.profile is not None
                and focused_resolution.profile.document_id != turn.focus_document_id
            ):
                switch_options.append(
                    {
                        "choice_id": "switch_1",
                        "document_id": str(focused_resolution.profile.document_id),
                        "problem_code": focused_resolution.profile.problem_code,
                        "title": focused_resolution.profile.canonical_question,
                        "routing_method": focused_resolution.method,
                        "confidence": focused_resolution.confidence,
                    }
                )
            elif focused_resolution.decision == ProblemDecision.AMBIGUOUS:
                for number, candidate in enumerate(focused_resolution.choices, start=1):
                    if candidate.document_id == turn.focus_document_id:
                        continue
                    switch_options.append(
                        {
                            "choice_id": f"switch_{number}",
                            "document_id": str(candidate.document_id),
                            "problem_code": candidate.problem_code,
                            "title": candidate.canonical_question,
                            "routing_method": focused_resolution.method,
                            "confidence": focused_resolution.confidence,
                        }
                    )
            elif self.settings.support_rag_fallback_enabled and self.document_router:
                current_stage = "RAG_DOCUMENT_RESOLUTION"
                await self.repository.set_stage(turn, current_stage)
                topic_route = await self.document_router.resolve(
                    knowledge_id=knowledge_id,
                    index_id=turn.index_id,
                    question=clean_question,
                    retrieval=retrieval_config,
                    profiles=profiles,
                    focus_document_id=turn.focus_document_id,
                )
                if (
                    topic_route.action == ACTION_DELIVER
                    and topic_route.document_id
                    and topic_route.document_id != turn.focus_document_id
                ):
                    candidate_profile = await self.repository.get_profile_by_document(
                        knowledge_id, topic_route.document_id, index_id=turn.index_id
                    )
                    switch_options.append(
                        {
                            "choice_id": "switch_1",
                            "document_id": str(topic_route.document_id),
                            "problem_code": (
                                candidate_profile.problem_code if candidate_profile else None
                            ),
                            "title": topic_route.document_title
                            or (
                                candidate_profile.canonical_question
                                if candidate_profile
                                else "候选文档"
                            ),
                            "routing_method": "DOCUMENT_ROUTER",
                            "confidence": topic_route.confidence,
                        }
                    )
                elif topic_route.action in {ACTION_OFFER_SWITCH, ACTION_CLARIFY}:
                    for number, route_option in enumerate(topic_route.options, start=1):
                        if str(route_option.get("document_id")) == str(turn.focus_document_id):
                            continue
                        candidate_profile = await self.repository.get_profile_by_document(
                            knowledge_id,
                            UUID(str(route_option["document_id"])),
                            index_id=turn.index_id,
                        )
                        switch_options.append(
                            {
                                "choice_id": f"switch_{number}",
                                "document_id": str(route_option["document_id"]),
                                "problem_code": (
                                    candidate_profile.problem_code if candidate_profile else None
                                ),
                                "title": str(route_option.get("title") or "候选文档"),
                                "routing_method": "DOCUMENT_ROUTER",
                                "confidence": topic_route.confidence,
                            }
                        )
                elif topic_route.action == ACTION_STAY:
                    # The focus-aware document router found no evidence strong
                    # enough to leave the current document. Continue with the
                    # focused assistant instead of turning weak alternatives
                    # into a switch confirmation.
                    pass
            if switch_options:
                response = self._base_response(turn, started)
                public_options = [
                    {
                        key: option[key]
                        for key in ("choice_id", "document_id", "problem_code", "title")
                    }
                    for option in switch_options
                ]
                response.update(
                    {
                        "response_type": "CLARIFICATION_REQUIRED",
                        "answer": "当前问题可能属于另一篇文档，请确认是否切换焦点文档。",
                        "clarification": {
                            "type": "CONFIRM_DOCUMENT_SWITCH",
                            "message": "当前问题可能属于另一篇文档，请确认是否切换焦点文档。",
                            "choices": public_options,
                        },
                        "routing": {
                            "source": "FOCUSED_SWITCH_CHECK",
                            "decision": "OFFER_SWITCH",
                        },
                        "latency_ms": _elapsed(started),
                    }
                )
                switch_options = [
                    {
                        **option,
                        "origin_focus_epoch": turn.focus_epoch,
                        "origin_document_id": str(turn.focus_document_id),
                    }
                    for option in switch_options
                ]
                context = discovery_context(
                    turn.chat_context,
                    last_clarification_question=clean_question,
                )
                await self.repository.finish_turn(
                    turn,
                    response=response,
                    context=context,
                    focus_document_id=turn.focus_document_id,
                    pending_switch_options=switch_options,
                    trace={
                        "support_version": 1,
                        "focus_document_id": str(turn.focus_document_id),
                        "document_switch": {"candidates": public_options},
                    },
                )
                return response

            current_stage = "DOCUMENT_LOADING"
            await self.repository.set_stage(turn, current_stage)
            profile = await self.repository.get_profile_by_document(
                knowledge_id, turn.focus_document_id, index_id=turn.index_id
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
            if isinstance(self.repository, SupportRepository):
                await self.repository.record_trace_llm_snapshot(trace_id=turn.trace_id, model=llm)
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
                    workflow=read_workflow(turn.chat_context).model_dump(mode="json"),
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
                        option = {
                            **option,
                            "origin_focus_epoch": turn.focus_epoch,
                            "origin_document_id": str(turn.focus_document_id),
                        }
                        context = discovery_context(
                            turn.chat_context,
                            last_clarification_question=clean_question,
                        )
                        await self.repository.finish_turn(
                            turn,
                            response=response,
                            context=context,
                            focus_document_id=document.id,
                            pending_switch_options=[option],
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
        except asyncio.CancelledError:
            await asyncio.shield(
                self.repository.fail_turn(
                    turn,
                    code="CLIENT_DISCONNECTED",
                    message="客户端已断开",
                    trace=_failure_trace(turn, "CLIENT_DISCONNECTED"),
                    stage=current_stage,
                    cancelled=True,
                )
            )
            raise
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
