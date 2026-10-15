from __future__ import annotations

import json

import pytest

from app.rag.llm import FakeLlmProvider
from app.rag.v2.conversation_answer import CONVERSATION_RULES, is_unfinished_answer
from app.rag.v2.query_interpreter import (
    FORMAT_REQUEST_RULES,
    FORMAT_ROUTE_EXAMPLES,
    INTERPRETER_ROUTE_EXAMPLES,
    INTERPRETER_RULES,
    QueryInterpretation,
    build_interpreter_prompt,
    guard_english_requirement,
    guard_format_only_out_of_scope,
    interpret_query,
)

HISTORY = [
    {"role": "USER", "content": "FFC 掉线报警怎么检查？"},
    {"role": "ASSISTANT", "content": "先检查电源指示灯和网线，再确认交换机端口。"},
]


HISTORIES_FOR_QUOTES = HISTORY


def _out_of_scope(question: str) -> QueryInterpretation:
    return QueryInterpretation(
        resolved_question=question,
        scope="OUT_OF_SCOPE",
        relation="NEW_TASK",
        response_path="CONVERSATION",
    )


@pytest.mark.parametrize(
    "question",
    [
        "answer in english",
        "Answer in English.",
        "用英文回答",
        "请翻译成英文",
        "简短一点",
        "分点说明",
    ],
)
def test_guard_inherits_the_latest_task_instead_of_refusing_a_format_request(
    question: str,
) -> None:
    guarded, reason = guard_format_only_out_of_scope(
        _out_of_scope(question), question=question, history=HISTORY, max_rounds=4
    )

    assert reason == "FORMAT_ONLY_OUT_OF_SCOPE_INHERITED"
    assert guarded.scope == "UNCERTAIN"
    assert guarded.relation == "FOLLOW_UP"
    assert guarded.response_path == "KNOWLEDGE"
    assert guarded.active_task == "FFC 掉线报警怎么检查？"
    assert guarded.resolved_question == f"FFC 掉线报警怎么检查？；当前要求：{question}"


def test_guard_leaves_real_out_of_scope_requests_alone() -> None:
    for question, history in (
        ("帮我写一首关于大海的诗", HISTORY),  # not a format request
        ("把 'good morning' 翻译成法语", HISTORIES_FOR_QUOTES),  # carries its own text to translate
        ('translate "see you" to French', HISTORIES_FOR_QUOTES),
        ("answer in english", []),  # nothing to inherit: stays a refusal decision of the model
        (
            "请把下面这段很长的文字翻译成英文并润色，同时保留原有的分段和标点符号",
            HISTORY,
        ),  # too long
    ):
        original = _out_of_scope(question)
        guarded, reason = guard_format_only_out_of_scope(
            original, question=question, history=history, max_rounds=4
        )
        assert guarded is original
        assert reason is None


def test_guard_ignores_in_scope_and_incomplete_history() -> None:
    in_scope = QueryInterpretation(
        resolved_question="FFC 掉线报警怎么检查？请用英文回答。",
        scope="IN_SCOPE",
        relation="FOLLOW_UP",
        response_path="KNOWLEDGE",
    )
    assert guard_format_only_out_of_scope(
        in_scope, question="answer in english", history=HISTORY, max_rounds=4
    ) == (in_scope, None)

    unanswered = [{"role": "USER", "content": "FFC 掉线报警怎么检查？"}]
    original = _out_of_scope("answer in english")
    assert guard_format_only_out_of_scope(
        original, question="answer in english", history=unanswered, max_rounds=4
    ) == (original, None)


@pytest.mark.asyncio
async def test_interpret_query_records_the_guard_in_the_trace() -> None:
    provider = FakeLlmProvider(
        completions=[
            json.dumps(
                {
                    "resolved_question": "answer in english",
                    "scope": "OUT_OF_SCOPE",
                    "relation": "NEW_TASK",
                    "active_task": None,
                    "response_path": "CONVERSATION",
                }
            )
        ]
    )

    outcome = await interpret_query(
        provider,
        question="answer in english",
        history=HISTORY,
        knowledge_name="VDR600",
        scope_description="",
        max_rounds=4,
        max_tokens=800,
        timeout_seconds=5,
    )

    assert outcome.status == "PASSED"
    assert outcome.interpretation.scope == "UNCERTAIN"
    assert outcome.interpretation.response_path == "KNOWLEDGE"
    assert outcome.trace_dict()["scope_guard"] == "FORMAT_ONLY_OUT_OF_SCOPE_INHERITED"


def test_format_rules_are_in_the_prompt_and_keep_the_previous_prompt_recoverable() -> None:
    prompt = json.loads(
        build_interpreter_prompt(
            question="answer in english",
            history=[],
            knowledge_name="VDR600",
            scope_description="",
            max_rounds=4,
        )
    )

    for rule in FORMAT_REQUEST_RULES:
        assert rule in prompt["rules"]
    for example in FORMAT_ROUTE_EXAMPLES:
        assert example in prompt["route_examples"]
    # Dropping the new entries gives back the earlier lists (the review tool relies on it).
    old_rules = [rule for rule in INTERPRETER_RULES if rule not in FORMAT_REQUEST_RULES]
    assert old_rules[0].startswith("把当前问题")
    assert len(old_rules) == len(INTERPRETER_RULES) - len(FORMAT_REQUEST_RULES)
    assert len(INTERPRETER_ROUTE_EXAMPLES) == len(FORMAT_ROUTE_EXAMPLES) + 5


@pytest.mark.parametrize(
    ("answer", "unfinished"),
    [
        ("Here is the translation of the previous response:", True),
        ("以下是翻译：", True),
        ("以下是翻译：  \n", True),
        ("To check the FFC alarm, inspect the power indicator first.", False),
        ("好的，这样处理更稳妥。", False),
    ],
)
def test_unfinished_answer_detection(answer: str, unfinished: bool) -> None:
    assert is_unfinished_answer(answer) is unfinished


def test_conversation_rules_require_the_full_translation() -> None:
    assert any("以冒号结尾的引导语" in rule for rule in CONVERSATION_RULES)


def _in_scope(resolved: str) -> QueryInterpretation:
    return QueryInterpretation(
        resolved_question=resolved, scope="IN_SCOPE", relation="NEW_TASK", response_path="KNOWLEDGE"
    )


@pytest.mark.parametrize(
    ("question", "resolved", "suffix"),
    [
        (
            "Please answer in English: how to check DAU voltage?",
            "How to check the DAU voltage on the VDR600?",
            " (Please answer in English.)",
        ),
        ("FFC 怎么升级？请用英文回答", "VDR600 的 FFC 如何升级？", "（请用英文回答）"),
        ("answer me in english, how do I reset it", "how do I reset the FFC", " (Please"),
    ],
)
def test_dropped_english_instruction_is_restored(question: str, resolved: str, suffix: str) -> None:
    guarded, reason = guard_english_requirement(_in_scope(resolved), question=question)

    assert reason == "ENGLISH_REQUIREMENT_RESTORED"
    assert guarded.resolved_question.startswith(resolved)
    assert suffix in guarded.resolved_question


def test_english_instruction_guard_leaves_other_cases_alone() -> None:
    kept = _in_scope("FFC 怎么升级？请用英文回答。")
    assert guard_english_requirement(kept, question="FFC 怎么升级？请用英文回答") == (kept, None)
    plain = _in_scope("How do I check the FFC offline alarm?")
    assert guard_english_requirement(plain, question="How do I check the FFC offline alarm?") == (
        plain,
        None,
    )
    chinese = _in_scope("如何更换 PSU 电源板？")
    assert guard_english_requirement(chinese, question="如何更换 PSU 电源板？") == (chinese, None)


@pytest.mark.asyncio
async def test_interpret_query_records_the_language_guard() -> None:
    provider = FakeLlmProvider(
        completions=[
            json.dumps(
                {
                    "resolved_question": "How to check the DAU voltage on the VDR600?",
                    "scope": "IN_SCOPE",
                    "relation": "NEW_TASK",
                    "active_task": None,
                    "response_path": "KNOWLEDGE",
                }
            )
        ]
    )

    outcome = await interpret_query(
        provider,
        question="Please answer in English: how to check DAU voltage?",
        history=[],
        knowledge_name="VDR600",
        scope_description="",
        max_rounds=4,
        max_tokens=800,
        timeout_seconds=5,
    )

    assert outcome.interpretation.resolved_question.endswith("(Please answer in English.)")
    assert outcome.trace_dict()["language_guard"] == "ENGLISH_REQUIREMENT_RESTORED"
