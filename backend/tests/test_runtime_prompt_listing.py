"""The admin's read-only prompt listing is generated from the real prompt constants.

It used to be a hand-written paraphrase that drifted: it kept listing outputs that had been removed
and said the opposite of the real image rule. These tests pin the listing to what the model gets.
"""

from __future__ import annotations

import json
from typing import Any

from app.rag.prompt import runtime_prompt_entries
from app.rag.v2.answer_generator import build_answer_prompt
from app.rag.v2.conversation_answer import build_conversation_prompt
from app.rag.v2.document_context import DocumentContext
from app.rag.v2.query_interpreter import (
    QueryInterpretation,
    build_interpreter_prompt,
)


def _entry(name: str) -> dict[str, Any]:
    item = next(item for item in runtime_prompt_entries() if item["name"] == name)
    return json.loads(item["content"])  # type: ignore[no-any-return]


def _answer_prompt() -> dict[str, Any]:
    return json.loads(
        build_answer_prompt(
            history=[],
            interpretation=QueryInterpretation(resolved_question="Q", response_path="KNOWLEDGE"),
            context=DocumentContext("", (), (), (), (), 0, "FULL_DOCUMENT"),
            product_name="P",
            scope_description="S",
            max_rounds=4,
        )
    )


def test_the_answer_stage_listing_is_the_real_prompt() -> None:
    entry, prompt = _entry("runtime-v2-answer-generator"), _answer_prompt()
    assert entry["rules"] == prompt["decision_policy"]
    assert entry["output_contract"] == prompt["output_contract"]
    assert entry["output_example"] == prompt["output_example"]
    assert entry["output"] == list(prompt["output_schema"]["properties"])
    assert set(entry["input"]) == {
        key
        for key in prompt
        if key not in {"output_schema", "output_example", "decision_policy", "output_contract"}
    }


def test_the_interpreter_stage_listing_is_the_real_prompt() -> None:
    entry = _entry("runtime-v2-query-interpreter")
    prompt = json.loads(
        build_interpreter_prompt(
            question="Q", history=[], knowledge_name="K", scope_description="S", max_rounds=4
        )
    )
    assert entry["rules"] == prompt["rules"]
    assert entry["route_examples"] == prompt["route_examples"]
    assert entry["output_example"] == prompt["output_example"]
    assert entry["output"] == list(prompt["output_schema"]["properties"])
    # outputs removed from the schema must not reappear in the listing
    assert "search_queries" not in entry["output"]
    assert "key_constraints" not in entry["output"]
    assert "immediate_previous_round" not in entry["input"]


def test_the_conversation_stage_is_listed_and_matches() -> None:
    entry = _entry("runtime-v2-conversation-answer")
    prompt = json.loads(
        build_conversation_prompt(
            question="Q", resolved_question="R", history=[], product_name="P", scope_description="S"
        )
    )
    assert entry["rules"] == prompt["rules"]
    assert entry["output"] == list(prompt["output_schema"]["properties"])


def test_the_listing_cannot_contradict_the_image_rule_again() -> None:
    """The old listing said images are not all used, while the real rule 12 asks for every image
    of the written steps in a full operation. The listing must carry the rule verbatim."""

    rules = _entry("runtime-v2-answer-generator")["rules"]
    assert any("完整操作" in rule and "全部放入" in rule for rule in rules)
    assert not any("不因上下文存在图片而全部使用" in rule for rule in rules)


def test_listing_ids_and_names_are_stable() -> None:
    entries = runtime_prompt_entries()
    assert [(str(item["id"])[-2:], item["name"]) for item in entries] == [
        ("11", "runtime-v2-query-interpreter"),
        ("12", "runtime-v2-answer-generator"),
        ("13", "runtime-v2-conversation-answer"),
    ]
    assert all(item["source"] == "RUNTIME" and item["active"] is False for item in entries)
