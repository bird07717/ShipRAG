from __future__ import annotations

import json

import pytest

from app.rag.llm import FakeLlmProvider, LlmError
from app.rag.v2.answer_generator import build_answer_prompt
from app.rag.v2.document_context import DocumentContext
from app.rag.v2.history import select_complete_round_history
from app.rag.v2.query_interpreter import QueryInterpretation, interpret_query


class _CapturingFakeLlmProvider(FakeLlmProvider):
    def __init__(self, completion: str) -> None:
        super().__init__(completions=[completion])
        self.prompt = ""

    async def complete(
        self,
        prompt: str,
        *,
        system: str,
        max_tokens: int,
        temperature: float,
        timeout_seconds: float | None = None,
        thinking: bool = False,
        thinking_budget: int | None = None,
    ) -> str:
        self.prompt = prompt
        return await super().complete(
            prompt,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            thinking=thinking,
            thinking_budget=thinking_budget,
        )


class _FailOnceLlmProvider(_CapturingFakeLlmProvider):
    def __init__(self, completion: str, error: LlmError) -> None:
        super().__init__(completion)
        self.error = error
        self.calls = 0

    async def complete(
        self,
        prompt: str,
        *,
        system: str,
        max_tokens: int,
        temperature: float,
        timeout_seconds: float | None = None,
        thinking: bool = False,
        thinking_budget: int | None = None,
    ) -> str:
        self.calls += 1
        if self.calls == 1:
            raise self.error
        return await super().complete(
            prompt,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            thinking=thinking,
            thinking_budget=thinking_budget,
        )


class _AlwaysFailLlmProvider(_CapturingFakeLlmProvider):
    def __init__(self, error: LlmError) -> None:
        super().__init__("{}")
        self.error = error
        self.calls = 0

    async def complete(
        self,
        prompt: str,
        *,
        system: str,
        max_tokens: int,
        temperature: float,
        timeout_seconds: float | None = None,
        thinking: bool = False,
        thinking_budget: int | None = None,
    ) -> str:
        del system, max_tokens, temperature, timeout_seconds, thinking, thinking_budget
        self.prompt = prompt
        self.calls += 1
        raise self.error


def _round(
    number: int,
    *,
    status: str = "COMPLETED",
    user_status: str = "COMPLETED",
    linked: bool = True,
    answer: str | None = None,
    input_guard_reason: str | None = None,
) -> dict[str, object]:
    return {
        "id": f"a{number}",
        "user_id": f"u{number}" if linked else None,
        "question": f"问题 {number}",
        "answer": answer if answer is not None else f"回答 {number}",
        "status": status,
        "user_status": user_status,
        "input_guard_reason": input_guard_reason,
        # These legacy fields deliberately disagree. V2 must ignore them.
        "focus_epoch": number,
        "user_epoch": number + 100,
        "rag_history_context": {"origin_query": "旧待确认问题"},
    }


def test_selects_recent_explicit_complete_rounds_without_focus_state() -> None:
    previous_document_id = "7777ff1b-efe0-4a12-a1dc-9bdf3532042b"
    latest = _round(3)
    latest["used_document_ids"] = [previous_document_id]
    history, diagnostics = select_complete_round_history(
        [latest, _round(2), _round(1)],
        max_rounds=2,
        max_messages=10,
        token_budget=2_000,
    )

    assert history == [
        {"role": "USER", "content": "问题 2"},
        {"role": "ASSISTANT", "content": "回答 2"},
        {"role": "USER", "content": "问题 3"},
        {"role": "ASSISTANT", "content": "回答 3"},
    ]
    assert diagnostics["strategy"] == "v2_complete_rounds"
    assert diagnostics["selected_message_ids"] == ["u2", "a2", "u3", "a3"]
    assert diagnostics["round_count"] == 2
    assert diagnostics["previous_document_ids"] == [previous_document_id]
    assert diagnostics["boundary"] == "ROUND_LIMIT"


def test_skips_unlinked_and_failed_rounds_but_keeps_older_complete_round() -> None:
    history, diagnostics = select_complete_round_history(
        [_round(4, linked=False), _round(3, status="FAILED"), _round(2)],
        max_rounds=4,
        max_messages=10,
        token_budget=2_000,
    )

    assert history == [
        {"role": "USER", "content": "问题 2"},
        {"role": "ASSISTANT", "content": "回答 2"},
    ]
    assert diagnostics["excluded"][:2] == [
        {"message_id": "a4", "reason": "UNLINKED_LEGACY"},
        {"message_id": "a3", "reason": "INCOMPLETE_ROUND"},
    ]


@pytest.mark.parametrize("input_guard_reason", ["GREETING", "META_ACK"])
def test_skips_nontechnical_shortcuts_and_keeps_previous_technical_evidence(
    input_guard_reason: str,
) -> None:
    previous_document_id = "7777ff1b-efe0-4a12-a1dc-9bdf3532042b"
    technical = _round(2)
    technical["used_document_ids"] = [previous_document_id]

    history, diagnostics = select_complete_round_history(
        [_round(3, input_guard_reason=input_guard_reason), technical, _round(1)],
        max_rounds=2,
        max_messages=10,
        token_budget=2_000,
    )

    assert history == [
        {"role": "USER", "content": "问题 1"},
        {"role": "ASSISTANT", "content": "回答 1"},
        {"role": "USER", "content": "问题 2"},
        {"role": "ASSISTANT", "content": "回答 2"},
    ]
    assert diagnostics["previous_document_ids"] == [previous_document_id]
    assert diagnostics["excluded"] == [{"message_id": "a3", "reason": "NON_TECHNICAL_SHORTCUT"}]


def test_keeps_low_information_shortcut_for_condition_followup() -> None:
    history, diagnostics = select_complete_round_history(
        [_round(2, input_guard_reason="SINGLE_CHARACTER"), _round(1)],
        max_rounds=2,
        max_messages=10,
        token_budget=2_000,
    )

    assert history == [
        {"role": "USER", "content": "问题 1"},
        {"role": "ASSISTANT", "content": "回答 1"},
        {"role": "USER", "content": "问题 2"},
        {"role": "ASSISTANT", "content": "回答 2"},
    ]
    assert diagnostics["excluded"] == []


def test_does_not_skip_an_oversized_recent_complete_round() -> None:
    history, diagnostics = select_complete_round_history(
        [_round(2, answer="很长" * 200), _round(1)],
        max_rounds=4,
        max_messages=10,
        token_budget=100,
    )

    assert history == []
    assert diagnostics["boundary"] == "TOKEN_BUDGET"
    assert diagnostics["excluded"] == [{"message_id": "a2", "reason": "TOKEN_BUDGET"}]


def test_answer_prompt_keeps_corrections_and_marks_history_as_non_evidence() -> None:
    prompt = build_answer_prompt(
        question="不是 VAU，现在问 PDC 怎么检查",
        history=[
            {"role": "USER", "content": "VAU 怎么配置"},
            {"role": "ASSISTANT", "content": "旧回答中的参数可能是错的"},
        ],
        interpretation=QueryInterpretation(
            resolved_question="PDC 怎么检查",
            search_queries=["PDC 检查"],
            scope="IN_SCOPE",
            relation="CORRECTION",
            active_task="检查 PDC",
        ),
        context=DocumentContext(
            prompt_context="",
            document_keys=(),
            image_bindings=(),
            title_aliases=(),
            document_ids=(),
            estimated_tokens=0,
            context_mode="FULL_DOCUMENT",
        ),
        product_name="VDR600",
        scope_description="VDR600 技术支持",
        max_rounds=4,
    )

    assert "不是 VAU，现在问 PDC 怎么检查" in prompt
    assert "旧回答中的参数可能是错的" in prompt
    assert "历史助手回答只用于指代" in prompt
    assert "均不是产品事实" in prompt
    assert "多文档时先找主任务文档" in prompt
    assert "used_document_keys 只列实际采用事实的全部文档" in prompt
    assert "每张图片使用一个完整" in prompt
    payload = json.loads(prompt)
    assert payload["conversation_relation"] == "CORRECTION"
    assert payload["active_task"] == "检查 PDC"
    assert "answer_depth" not in payload
    assert "最小充分回答" in prompt
    assert "操作题给完成任务必需的连续步骤" in prompt
    assert "按需选择而非全部返回" in prompt
    assert payload["trusted_product_profile"] == {
        "name": "VDR600",
        "description": "VDR600 技术支持",
    }
    assert "product" not in payload
    assert "scope" not in payload
    assert "IN_SCOPE 不得返回 OUT_OF_SCOPE" in prompt
    assert "文档没有覆盖时不能据此拒答" in prompt
    assert "使用专业通用知识继续回答" in prompt
    assert "正式产品分类或型号定位" in prompt
    assert "只回答产品技术部分" in prompt


@pytest.mark.asyncio
async def test_query_interpreter_exposes_previous_user_task_for_short_follow_up() -> None:
    provider = _CapturingFakeLlmProvider(
        '{"resolved_question":"更换 VDR600 PSU 电源板并拆装 3359 核心板时使用哪个端口",'
        '"search_queries":["PSU 3359 操作端口"],"key_constraints":["PSU 更换操作"],'
        '"scope":"IN_SCOPE","relation":"FOLLOW_UP",'
        '"active_task":"更换 VDR600 PSU 电源板并拆装 3359 核心板"}'
    )

    outcome = await interpret_query(
        provider,
        question="操作时使用哪个端口？",
        history=[
            {"role": "USER", "content": "请完整展示更换 PSU 电源板的步骤"},
            {"role": "ASSISTANT", "content": "需要拆下 3359 核心板及底板。"},
        ],
        knowledge_name="VDR600",
        scope_description="VDR600 技术支持",
        max_rounds=4,
        variant_limit=3,
        max_tokens=512,
        timeout_seconds=5,
    )

    prompt = json.loads(provider.prompt)
    assert "answer_depth" not in prompt["output_example"]
    assert any("不决定回答篇幅" in rule for rule in prompt["rules"])
    assert any("可信产品档案" in rule for rule in prompt["rules"])
    assert any("该产品本身是什么" in rule for rule in prompt["rules"])
    assert any("无关创作或闲聊素材" in rule for rule in prompt["rules"])
    assert prompt["recent_completed_rounds"] == [
        {
            "user_request": "请完整展示更换 PSU 电源板的步骤",
            "assistant_answer_untrusted": "需要拆下 3359 核心板及底板。",
        }
    ]
    assert "recent_completed_history" not in prompt
    assert "immediate_previous_round" not in prompt
    assert outcome.interpretation.relation == "FOLLOW_UP"
    assert outcome.interpretation.active_task == "更换 VDR600 PSU 电源板并拆装 3359 核心板"
    trace = outcome.trace_dict()
    assert trace["relation"] == "FOLLOW_UP"
    assert "answer_depth" not in trace
    assert trace["active_task_chars"] == len(outcome.interpretation.active_task)
    assert trace["active_task_sha256"]
    assert outcome.interpretation.active_task not in json.dumps(trace, ensure_ascii=False)


@pytest.mark.asyncio
async def test_query_interpreter_timeout_inherits_elliptical_short_follow_up() -> None:
    provider = _AlwaysFailLlmProvider(
        LlmError("UPSTREAM_TIMEOUT", "模型服务响应超时", retryable=True)
    )

    outcome = await interpret_query(
        provider,
        question="操作时使用哪个端口？",
        history=[
            {"role": "USER", "content": "请完整展示更换 PSU 电源板的步骤"},
            {"role": "ASSISTANT", "content": "需要拆下 3359 核心板及底板。"},
        ],
        knowledge_name="VDR600",
        scope_description="VDR600 技术支持",
        max_rounds=4,
        variant_limit=3,
        max_tokens=512,
        timeout_seconds=25,
    )

    assert provider.calls == 1
    assert outcome.status == "DEGRADED"
    assert outcome.interpretation.relation == "FOLLOW_UP"
    assert outcome.interpretation.active_task == "请完整展示更换 PSU 电源板的步骤"
    assert "操作时使用哪个端口" in outcome.interpretation.resolved_question
    trace = outcome.trace_dict()
    assert trace["llm1_timeout"] is True
    assert trace["history_fallback_applied"] is True
    assert trace["history_fallback_rejected"] is False
    assert trace["history_fallback_reason"] == "SHORT_ELLIPTICAL_FOLLOW_UP"


@pytest.mark.asyncio
async def test_query_interpreter_timeout_does_not_inherit_explicit_new_task() -> None:
    provider = _AlwaysFailLlmProvider(
        LlmError("UPSTREAM_TIMEOUT", "模型服务响应超时", retryable=True)
    )

    outcome = await interpret_query(
        provider,
        question="怎么更换 PDC？",
        history=[
            {"role": "USER", "content": "如何配置 VAU 图像？"},
            {"role": "ASSISTANT", "content": "先设置电脑网卡。"},
        ],
        knowledge_name="VDR600",
        scope_description="VDR600 技术支持",
        max_rounds=4,
        variant_limit=3,
        max_tokens=512,
        timeout_seconds=25,
    )

    assert outcome.interpretation.relation == "NEW_TASK"
    assert outcome.interpretation.active_task is None
    assert outcome.interpretation.resolved_question == "怎么更换 PDC？"
    trace = outcome.trace_dict()
    assert trace["history_fallback_applied"] is False
    assert trace["history_fallback_rejected"] is True
    assert trace["history_fallback_reason"] == "EXPLICIT_NEW_TASK"


@pytest.mark.asyncio
async def test_query_interpreter_retries_one_immediate_transient_failure() -> None:
    provider = _FailOnceLlmProvider(
        '{"resolved_question":"配置 VAU 图像","search_queries":[],"key_constraints":[],'
        '"scope":"IN_SCOPE","relation":"NEW_TASK","active_task":null}',
        LlmError("UPSTREAM_UNAVAILABLE", "模型服务暂时不可用", retryable=True),
    )

    outcome = await interpret_query(
        provider,
        question="如何配置 VAU 图像？",
        history=[],
        knowledge_name="VDR600",
        scope_description="VDR600 技术支持",
        max_rounds=4,
        variant_limit=3,
        max_tokens=512,
        timeout_seconds=25,
    )

    assert provider.calls == 2
    assert outcome.status == "PASSED"
    assert outcome.attempts == 2
    assert outcome.trace_dict()["history_fallback_applied"] is False
