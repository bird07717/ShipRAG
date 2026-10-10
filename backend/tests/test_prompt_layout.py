"""Prompt layouts (ADR-0042).

The provider's implicit prefix cache only matches a shared leading part. The LLM 2 prompt used to
start with per-request data, so two different requests shared almost nothing: its static
instructions now come first. The LLM 1 prompt deliberately keeps the question first: with the
instructions first it dropped the user's explicit exclusions more often (prompt-layout-review.md),
and its prompt is too small for caching to matter. These tests pin both decisions.
"""

from __future__ import annotations

import json
import os
from typing import Any

from app.rag.v2.answer_generator import build_answer_prompt
from app.rag.v2.document_context import DocumentContext
from app.rag.v2.query_interpreter import QueryInterpretation, build_interpreter_prompt

ANSWER_STATIC = ["output_schema", "output_example", "decision_policy", "output_contract"]
INTERPRETER_STATIC = ["output_schema", "output_example", "route_examples", "rules"]


def _answer(question: str, *, history: list[dict[str, str]] | None = None) -> str:
    return build_answer_prompt(
        history=history or [],
        interpretation=QueryInterpretation(resolved_question=question, response_path="KNOWLEDGE"),
        context=DocumentContext(
            f"document of {question}", ("D1",), (), (), ("id",), 1, "FULL_DOCUMENT"
        ),
        product_name="P",
        scope_description="S",
        max_rounds=4,
    )


def _interpreter(question: str) -> str:
    return build_interpreter_prompt(
        question=question,
        history=[{"role": "user", "content": question}],
        knowledge_name="K",
        scope_description="S",
        max_rounds=4,
    )


def _keys(prompt: str) -> list[str]:
    data: dict[str, Any] = json.loads(prompt)
    return list(data)


def test_answer_prompt_starts_with_the_static_instructions() -> None:
    keys = _keys(_answer("Q"))
    assert keys[: len(ANSWER_STATIC)] == ANSWER_STATIC
    assert set(keys[len(ANSWER_STATIC) :]) == {
        "trusted_product_profile",
        "resolved_question",
        "conversation_relation",
        "active_task",
        "interpreted_scope",
        "recent_completed_history",
        "hidden_document_context",
        "available_document_keys",
        "available_image_keys",
    }


def test_interpreter_prompt_keeps_the_question_first() -> None:
    """Moving the instructions in front of the question made the rewrite drop exclusions such as
    "不要给软件升级流程" (3/3 samples on one case, 3 of 8 further questions). Do not reorder it
    without repeating run_interpreter_constraint_check.py."""

    keys = _keys(_interpreter("Q"))
    assert keys[:3] == ["current_question", "recent_completed_rounds", "knowledge_base"]
    assert keys[3:] == INTERPRETER_STATIC


def test_different_answer_requests_share_the_whole_static_prefix() -> None:
    for build, static in ((_answer, ANSWER_STATIC),):
        first, second = build("how to replace the PSU?"), build("which port?")
        shared = len(os.path.commonprefix([first, second]))
        static_chars = (
            len(
                json.dumps(
                    {k: json.loads(first)[k] for k in static},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            - 1
        )
        # the shared leading text covers every static block; only the data differs afterwards
        assert shared >= static_chars
        assert first[:static_chars] == second[:static_chars]
