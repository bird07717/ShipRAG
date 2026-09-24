from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.rag.llm import LlmError, LlmProvider

_SHORT_FOLLOW_UP_HINT = re.compile(
    r"(哪个|哪一个|哪里|多久|下一步|然后|之后|为什么|怎么办|怎么处理|"
    r"是否|呢|还要|可以吗|用什么|是多少|什么端口|什么参数|"
    r"已经|仍然|还是|当前|现在|更换后|操作时)"
)
_EXPLICIT_NEW_TASK = re.compile(
    r"(如何|怎么|怎样).{0,8}(配置|更换|升级|安装|拆卸|检查|排查|设置|"
    r"启动|关闭|连接|恢复|标定|使用)"
)


class QueryInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolved_question: str = Field(min_length=1, max_length=4_000)
    scope: Literal["IN_SCOPE", "OUT_OF_SCOPE", "UNCERTAIN"] = "UNCERTAIN"
    relation: Literal["NEW_TASK", "FOLLOW_UP", "CORRECTION"] = "NEW_TASK"
    active_task: str | None = Field(default=None, max_length=1_000)
    response_path: Literal["KNOWLEDGE", "CONVERSATION"]

    @field_validator("resolved_question")
    @classmethod
    def clean_question(cls, value: str) -> str:
        return " ".join(value.split())

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_retrieval_plan(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        normalized.pop("search_queries", None)
        normalized.pop("key_constraints", None)
        return normalized

    @field_validator("active_task")
    @classmethod
    def clean_active_task(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None


@dataclass(frozen=True, slots=True)
class QueryInterpretationOutcome:
    status: Literal["PASSED", "DEGRADED"]
    interpretation: QueryInterpretation
    latency_ms: int
    reason: str | None = None
    attempts: int = 1
    history_fallback: Literal["NOT_NEEDED", "APPLIED", "REJECTED"] = "NOT_NEEDED"
    history_fallback_reason: str | None = None

    def trace_dict(self) -> dict[str, object]:
        active_task = self.interpretation.active_task or ""
        return {
            "status": self.status,
            "reason": self.reason,
            "latency_ms": self.latency_ms,
            "attempts": self.attempts,
            "llm1_timeout": self.reason == "UPSTREAM_TIMEOUT",
            "history_fallback_applied": self.history_fallback == "APPLIED",
            "history_fallback_rejected": self.history_fallback == "REJECTED",
            "history_fallback_reason": self.history_fallback_reason,
            "scope": self.interpretation.scope,
            "resolved_question_chars": len(self.interpretation.resolved_question),
            "relation": self.interpretation.relation,
            "response_path": self.interpretation.response_path,
            "active_task_chars": len(active_task),
            "active_task_sha256": (
                hashlib.sha256(active_task.encode("utf-8")).hexdigest() if active_task else None
            ),
        }


def fallback_interpretation(
    question: str,
    *,
    history: list[dict[str, str]] | None = None,
    knowledge_name: str = "",
    max_rounds: int = 4,
) -> tuple[QueryInterpretation, Literal["APPLIED", "REJECTED"], str]:
    """Build a conservative no-LLM interpretation after an upstream failure.

    Only an elliptical short follow-up may inherit the latest complete user
    task. Explicit standalone operations keep the original question, so this
    fallback does not recreate the retired focus/query-planner state machine.
    """

    clean_question = " ".join(question.split())
    rounds = _completed_rounds(history or [], max_rounds)
    if not rounds:
        return (
            QueryInterpretation(resolved_question=clean_question, response_path="KNOWLEDGE"),
            "REJECTED",
            "NO_COMPLETE_HISTORY",
        )

    latest = rounds[-1]
    if len(clean_question) > 60:
        return (
            QueryInterpretation(resolved_question=clean_question, response_path="KNOWLEDGE"),
            "REJECTED",
            "QUESTION_NOT_SHORT",
        )
    if knowledge_name.strip() and knowledge_name.casefold() in clean_question.casefold():
        return (
            QueryInterpretation(resolved_question=clean_question, response_path="KNOWLEDGE"),
            "REJECTED",
            "EXPLICIT_PRODUCT",
        )
    if _EXPLICIT_NEW_TASK.search(clean_question):
        return (
            QueryInterpretation(resolved_question=clean_question, response_path="KNOWLEDGE"),
            "REJECTED",
            "EXPLICIT_NEW_TASK",
        )

    previous_answer = latest["assistant_answer_untrusted"]
    assistant_requested_condition = len(clean_question) <= 20 and any(
        marker in previous_answer for marker in ("请补充", "请确认", "哪个", "哪一", "？")
    )
    if not _SHORT_FOLLOW_UP_HINT.search(clean_question) and not assistant_requested_condition:
        return (
            QueryInterpretation(resolved_question=clean_question, response_path="KNOWLEDGE"),
            "REJECTED",
            "NO_FOLLOW_UP_SIGNAL",
        )

    active_task = latest["user_request"][:1_000]
    return (
        QueryInterpretation(
            resolved_question=f"{active_task}；当前追问：{clean_question}",
            scope="UNCERTAIN",
            relation="FOLLOW_UP",
            active_task=active_task,
            response_path="KNOWLEDGE",
        ),
        "APPLIED",
        "SHORT_ELLIPTICAL_FOLLOW_UP",
    )


def _history_window(history: list[dict[str, str]], max_rounds: int) -> list[dict[str, str]]:
    return history[-(max_rounds * 2) :]


def _completed_rounds(history: list[dict[str, str]], max_rounds: int) -> list[dict[str, str]]:
    window = _history_window(history, max_rounds)
    rounds: list[dict[str, str]] = []
    for index, item in enumerate(window):
        if item.get("role") != "USER" or not item.get("content", "").strip():
            continue
        assistant = next(
            (
                candidate
                for candidate in window[index + 1 :]
                if candidate.get("role") == "ASSISTANT" and candidate.get("content", "").strip()
            ),
            None,
        )
        if assistant is None:
            continue
        rounds.append(
            {
                "user_request": item["content"],
                "assistant_answer_untrusted": assistant["content"],
            }
        )
    return rounds


async def interpret_query(
    provider: LlmProvider,
    *,
    question: str,
    history: list[dict[str, str]],
    knowledge_name: str,
    scope_description: str,
    max_rounds: int,
    max_tokens: int,
    timeout_seconds: float,
) -> QueryInterpretationOutcome:
    started_at = time.perf_counter()
    prompt = json.dumps(
        {
            "current_question": question,
            "recent_completed_rounds": _completed_rounds(history, max_rounds),
            "knowledge_base": {
                "name": knowledge_name,
                "scope": scope_description,
            },
            "output_schema": QueryInterpretation.model_json_schema(),
            "output_example": {
                "resolved_question": "配置 VAU 图像时电脑网卡应设置什么 IP",
                "scope": "IN_SCOPE",
                "relation": "FOLLOW_UP",
                "active_task": "配置 VAU 图像",
                "response_path": "KNOWLEDGE",
            },
            "route_examples": [
                {
                    "current_question": "你能帮我做什么",
                    "response_path": "CONVERSATION",
                    "reason": "只需概括助手能力，不需要列举或核实具体文档覆盖",
                },
                {
                    "current_question": "好的，我同意联系海兰信进行专业检查",
                    "response_path": "CONVERSATION",
                    "reason": "只确认既有建议，没有请求新判断",
                },
                {
                    "current_question": "我需要联系海兰信专业人员吗",
                    "response_path": "CONVERSATION",
                    "reason": "询问是否寻求人工协助，不要求从文档确认技术事实或官方规定",
                },
                {
                    "current_question": "文档是否规定 LRU 故障必须联系工作人员",
                    "response_path": "KNOWLEDGE",
                    "reason": "需要核实文档中的具体规定",
                },
                {
                    "current_question": "上面的内容我看不懂，请简单解释",
                    "response_path": "CONVERSATION",
                    "reason": "只改写最近回答，不请求新事实",
                },
            ],
            "rules": [
                "把当前问题还原为可独立理解的请求，不决定回答篇幅或公开形式。",
                (
                    "knowledge_base.name 和 knowledge_base.scope 是管理员维护的可信产品"
                    "档案。询问该产品本身是什么、用途、组成、原理、使用、维护或故障时，"
                    "即使档案没有列出对应文档，也属于产品范围。"
                ),
                (
                    "先判断 current_question 能否脱离历史独立确定设备、部件、任务和阶段。"
                    "不能独立确定且最近用户请求提供了这些信息时，必须是 FOLLOW_UP；"
                    "修正最近任务的对象或条件时是 CORRECTION。"
                ),
                (
                    "例如“断电后等多久”“下一步呢”“为什么”“用哪个端口”等省略任务对象"
                    "的追问，必须继承最近一轮 user_request；历史助手回答只可帮助理解指代，"
                    "不得成为产品事实或新增任务来源。"
                ),
                (
                    "只有 current_question 自身已明确给出可独立理解的新对象或新目标时才是"
                    " NEW_TASK；不要因为追问很短或出现通用工程词就误判为新任务。"
                ),
                (
                    "FOLLOW_UP/CORRECTION 必须输出简短 active_task，并让 resolved_question "
                    "同时包含继承任务和当前所问属性；NEW_TASK 不得携带旧任务。"
                ),
                "必须保留当前所问属性的技术含义，不得把地址、目录、路径或参数互相替换。",
                (
                    "只有请求目标明显与知识库产品及其技术原理无关时才输出 OUT_OF_SCOPE；"
                    "仅把产品名称当作无关创作或闲聊素材，不会让无关任务变成产品问题。"
                ),
                (
                    "如果 current_question 同时包含产品技术任务和写作、娱乐、闲聊等无关"
                    "附加任务，只保留产品技术任务写入 resolved_question，"
                    "忽略无关部分，并按产品技术任务判断 scope。"
                ),
                (
                    "response_path 是是否检索的唯一决定，依据是回答是否需要文档提供产品专有"
                    "事实或技术依据，而不是是否需要作出新判断。确认/同意、致谢、概括助手"
                    "能提供哪类帮助、"
                    "改写既有回答，以及一般性的沟通和人工协助建议，无需文档时输出 "
                    "CONVERSATION；该路径可以给谨慎的通用建议，但不得声称文档或官方要求。"
                ),
                (
                    "产品参数、操作步骤、故障原因或下一步技术处置，以及官方服务能力、"
                    "联系方式、服务条款或文档是否规定某事等需要核实的事实，输出 "
                    "KNOWLEDGE；同时包含一般建议和技术任务时也输出 KNOWLEDGE。"
                    "无法判断能否脱离文档安全回答时，保守输出 KNOWLEDGE。"
                ),
                (
                    "不要因为消息出现产品名、厂商名或历史技术内容，就把无需文档的请求判为"
                    " KNOWLEDGE；也不得为了走 CONVERSATION 删除消息中真实存在的技术任务。"
                ),
                "scope 只能是 IN_SCOPE、OUT_OF_SCOPE 或 UNCERTAIN。",
                "relation 只能是 NEW_TASK、FOLLOW_UP 或 CORRECTION。",
                "response_path 只能是 KNOWLEDGE 或 CONVERSATION。",
                "只返回符合 Schema 的 JSON。",
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    attempts = 0
    try:
        while True:
            attempts += 1
            try:
                result = await provider.complete_structured(
                    prompt,
                    system="你是企业技术知识库的检索问题解释器，不直接回答用户问题。",
                    schema=QueryInterpretation,
                    max_tokens=max_tokens,
                    temperature=0.0,
                    timeout_seconds=timeout_seconds,
                )
                break
            except LlmError as exc:
                # Immediate connection/429/5xx failures may be transient and
                # return quickly. A full timeout is never retried here because
                # it would consume most of the turn deadline.
                if attempts == 1 and exc.code == "UPSTREAM_UNAVAILABLE" and exc.retryable:
                    continue
                raise
    except Exception as exc:
        fallback, fallback_status, fallback_reason = fallback_interpretation(
            question,
            history=history,
            knowledge_name=knowledge_name,
            max_rounds=max_rounds,
        )
        return QueryInterpretationOutcome(
            "DEGRADED",
            fallback,
            round((time.perf_counter() - started_at) * 1000),
            reason=str(getattr(exc, "code", type(exc).__name__))[:100],
            attempts=attempts,
            history_fallback=fallback_status,
            history_fallback_reason=fallback_reason,
        )
    return QueryInterpretationOutcome(
        "PASSED",
        result,
        round((time.perf_counter() - started_at) * 1000),
        attempts=attempts,
    )
