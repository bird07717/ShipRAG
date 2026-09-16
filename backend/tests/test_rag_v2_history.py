from __future__ import annotations

import json

import pytest

from app.rag.llm import FakeLlmProvider
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


def _round(
    number: int,
    *,
    status: str = "COMPLETED",
    user_status: str = "COMPLETED",
    linked: bool = True,
    answer: str | None = None,
) -> dict[str, object]:
    return {
        "id": f"a{number}",
        "user_id": f"u{number}" if linked else None,
        "question": f"问题 {number}",
        "answer": answer if answer is not None else f"回答 {number}",
        "status": status,
        "user_status": user_status,
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
