from __future__ import annotations

from app.rag.v2.answer_generator import build_answer_prompt
from app.rag.v2.document_context import DocumentContext
from app.rag.v2.history import select_complete_round_history
from app.rag.v2.query_interpreter import QueryInterpretation


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
    history, diagnostics = select_complete_round_history(
        [_round(3), _round(2), _round(1)],
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
    assert "历史助手回答只用于理解指代" in prompt
    assert "都不是产品事实证据" in prompt
