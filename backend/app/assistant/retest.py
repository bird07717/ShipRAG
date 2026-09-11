"""Replay one saved case, including its preceding questions, in an internal session."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from app.assistant.reviews import AnswerReviews
from app.assistant.service import public_complete
from app.common.errors import ApiError
from app.gateway.service import ChatGatewayService


def clarification_targets(response: dict[str, Any]) -> list[str]:
    clarification = response.get("clarification") or {}
    options = (
        clarification.get("choices")
        or clarification.get("options")
        or clarification.get("candidates")
        or []
    )
    return [
        str(option.get("document_id") or option.get("title") or option.get("label"))
        for option in options
    ]


async def run_retest(
    reviews: AnswerReviews,
    gateway: ChatGatewayService,
    *,
    kb: UUID,
    review_id: UUID,
    run_id: UUID,
    revision: int,
) -> dict[str, Any]:
    review, created = await reviews.reserve_retest(kb, review_id, run_id, revision)
    if not created:
        return review
    turns: list[dict[str, Any]] = []
    error = None
    try:
        async with asyncio.timeout(180):
            snapshot = review["snapshot"]
            history = snapshot["history"]
            inputs = [
                {"question": m["content"], **(m.get("input_payload") or {})}
                for m in history
                if m["role"] == "USER"
            ]
            originals = [
                m.get("response_payload") or {} for m in history if m["role"] == "ASSISTANT"
            ]
            if len(inputs) != len(originals):
                raise ApiError("RETEST_CONTEXT_MISSING", "前置对话缺少完整问答，无法可靠复测", 409)
            conversation_id = None
            for index, request_input in enumerate(
                [
                    *inputs,
                    {"question": snapshot["question"], **(snapshot.get("input_payload") or {})},
                ]
            ):
                question = request_input["question"]
                policy = await gateway.repository.resolve_policy(
                    knowledge_id=kb,
                    conversation_id=conversation_id,
                )
                response = await gateway.handle(
                    policy=policy,
                    knowledge_id=kb,
                    conversation_id=conversation_id,
                    question=question,
                    request_id=f"review-{run_id}-{index}",
                    user_id=f"review-{run_id}",
                    problem_code=None,
                    document_id=None,
                    choice_id=request_input.get("choice_id"),
                    include_images=request_input.get("include_images", True),
                    idempotency_key=f"review-{run_id}-{index}",
                )
                conversation_id = UUID(str(response["conversation_id"]))
                turns.append(
                    {
                        "question": question,
                        "response": public_complete(response, f"review-{run_id}-{index}"),
                        "diagnostics": await reviews.diagnostics(kb, response.get("trace_id")),
                    }
                )
                if index < len(originals) and (
                    clarification_targets(originals[index]) != clarification_targets(response)
                ):
                    raise ApiError(
                        "RETEST_CONTEXT_CHANGED",
                        "前置澄清选项已变化，已停止自动续问，请人工检查",
                        409,
                    )
    except TimeoutError:
        error = "复测超过180秒，已停止；可查看已完成轮次后重新发起"
    except asyncio.CancelledError:
        await asyncio.shield(reviews.finish_retest(run_id, turns, "复测连接或执行已中断"))
        raise
    except ApiError as exc:
        error = exc.message
    except Exception:
        error = "复测执行失败，请检查运行就绪状态和关联Trace"
    return await reviews.finish_retest(run_id, turns, error)
