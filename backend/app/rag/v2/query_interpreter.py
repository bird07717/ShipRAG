from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.rag.llm import LlmProvider


class QueryInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolved_question: str = Field(min_length=1, max_length=4_000)
    search_queries: list[str] = Field(default_factory=list, max_length=3)
    key_constraints: list[str] = Field(default_factory=list, max_length=8)
    scope: Literal["IN_SCOPE", "OUT_OF_SCOPE", "UNCERTAIN"] = "UNCERTAIN"
    relation: Literal["NEW_TASK", "FOLLOW_UP", "CORRECTION"] = "NEW_TASK"
    active_task: str | None = Field(default=None, max_length=1_000)

    @field_validator("resolved_question")
    @classmethod
    def clean_question(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("search_queries", "key_constraints")
    @classmethod
    def clean_list(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = " ".join(value.split())
            key = cleaned.casefold()
            if cleaned and key not in seen:
                seen.add(key)
                result.append(cleaned)
        return result

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

    def trace_dict(self) -> dict[str, object]:
        active_task = self.interpretation.active_task or ""
        return {
            "status": self.status,
            "reason": self.reason,
            "latency_ms": self.latency_ms,
            "scope": self.interpretation.scope,
            "resolved_question_chars": len(self.interpretation.resolved_question),
            "search_query_count": len(self.interpretation.search_queries),
            "constraint_count": len(self.interpretation.key_constraints),
            "relation": self.interpretation.relation,
            "active_task_chars": len(active_task),
            "active_task_sha256": (
                hashlib.sha256(active_task.encode("utf-8")).hexdigest() if active_task else None
            ),
        }


def fallback_interpretation(question: str) -> QueryInterpretation:
    return QueryInterpretation(
        resolved_question=question.strip(),
        search_queries=[],
        key_constraints=[],
        scope="UNCERTAIN",
    )


def _history_window(history: list[dict[str, str]], max_rounds: int) -> list[dict[str, str]]:
    return history[-(max_rounds * 2) :]


def _immediate_previous_round(
    history: list[dict[str, str]], max_rounds: int
) -> dict[str, str] | None:
    window = _history_window(history, max_rounds)
    for index in range(len(window) - 2, -1, -1):
        item = window[index]
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
        return {
            "user_request": item["content"],
            "assistant_answer_untrusted": assistant["content"],
        }
    return None


async def interpret_query(
    provider: LlmProvider,
    *,
    question: str,
    history: list[dict[str, str]],
    knowledge_name: str,
    scope_description: str,
    max_rounds: int,
    variant_limit: int,
    max_tokens: int,
    timeout_seconds: float,
) -> QueryInterpretationOutcome:
    started_at = time.perf_counter()
    prompt = json.dumps(
        {
            "current_question": question,
            "recent_completed_history": _history_window(history, max_rounds),
            "immediate_previous_round": _immediate_previous_round(history, max_rounds),
            "knowledge_base": {
                "name": knowledge_name,
                "scope": scope_description,
            },
            "output_schema": QueryInterpretation.model_json_schema(),
            "output_example": {
                "resolved_question": "配置 VAU 图像时电脑网卡应设置什么 IP",
                "search_queries": ["VAU 图像配置 电脑网络参数"],
                "key_constraints": ["电脑网卡参数"],
                "scope": "IN_SCOPE",
                "relation": "FOLLOW_UP",
                "active_task": "配置 VAU 图像",
            },
            "rules": [
                "把当前问题还原为可独立检索的问题，不决定回答篇幅或公开形式。",
                "历史助手回答只用于理解指代，不得当作产品事实。",
                (
                    "先判断 relation：独立新任务为 NEW_TASK，承接上一轮为 FOLLOW_UP，"
                    "纠正上一轮对象或条件为 CORRECTION。"
                ),
                (
                    "短追问省略设备或任务时，必须从 immediate_previous_round.user_request "
                    "继承最近的设备、部件和操作目标；不得从历史助手回答新增任务事实。"
                ),
                (
                    "FOLLOW_UP/CORRECTION 应输出简短 active_task，并让 resolved_question "
                    "同时包含该任务和当前所问属性；新问题明确给出其他设备或操作时必须 "
                    "NEW_TASK，不得继承。"
                ),
                "必须保留当前所问属性的技术含义，不得把地址、目录、路径或参数互相替换。",
                f"search_queries 最多 {variant_limit} 条，只补充有助召回的同义表达。",
                "每条补充查询都应包含已还原的设备或部件名称，避免检索到其他设备的相似操作。",
                "原始问题由服务器强制参与检索，不需要重复堆砌。",
                "只有明显与知识库产品无关时才输出 OUT_OF_SCOPE。",
                "scope 只能是 IN_SCOPE、OUT_OF_SCOPE 或 UNCERTAIN。",
                "relation 只能是 NEW_TASK、FOLLOW_UP 或 CORRECTION。",
                "只返回符合 Schema 的 JSON。",
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    try:
        result = await provider.complete_structured(
            prompt,
            system="你是企业技术知识库的检索问题解释器，不直接回答用户问题。",
            schema=QueryInterpretation,
            max_tokens=max_tokens,
            temperature=0.0,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        return QueryInterpretationOutcome(
            "DEGRADED",
            fallback_interpretation(question),
            round((time.perf_counter() - started_at) * 1000),
            reason=str(getattr(exc, "code", type(exc).__name__))[:100],
        )
    return QueryInterpretationOutcome(
        "PASSED",
        result.model_copy(update={"search_queries": result.search_queries[:variant_limit]}),
        round((time.perf_counter() - started_at) * 1000),
    )
