from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from test_chat_doc_routing_flow import (
    DOC_A,
    _candidate,
    _RecordingLlmProvider,
    _RoutingRepository,
    _service,
    _turn,
)

from app.common.errors import ApiError
from app.rag.history import select_history
from app.rag.prompt import RAG_SYSTEM_GUARD, build_context, build_document_context, render_history


def round_row(number=1, **kwargs):
    return {
        "id": f"a{number}",
        "user_id": f"u{number}",
        "question": f"用户问题{number}",
        "answer": f"模型回答{number}",
        "status": "COMPLETED",
        "user_status": "COMPLETED",
        "focus_epoch": 1,
        "user_epoch": 1,
        "rag_history_context": {},
        **kwargs,
    }


def select(rows, **kwargs):
    return select_history(
        rows, **{"focus_epoch": 1, "max_messages": 10, "token_budget": 2000, **kwargs}
    )


@pytest.mark.parametrize("limit", [0, 1, 2, 3, 4, 10])
def test_history_limits_are_round_based(limit):
    rows = [round_row(i) for i in range(8, 0, -1)]
    history, diag = select(rows, max_messages=limit)
    count = limit // 2
    assert len(history) == count * 2
    assert [m["role"] for m in history] == ["USER", "ASSISTANT"] * count
    assert diag["round_count"] == count
    if count:
        assert history[-1]["message_id"] == "a8"


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"status": "FAILED"}, "INCOMPLETE_ROUND"),
        ({"status": "CANCELLED"}, "INCOMPLETE_ROUND"),
        ({"status": "STREAMING"}, "INCOMPLETE_ROUND"),
        ({"user_status": "FAILED"}, "INCOMPLETE_ROUND"),
        ({"user_id": None}, "UNLINKED_LEGACY"),
        ({"focus_epoch": 0}, "OTHER_FOCUS"),
        ({"user_epoch": 0}, "OTHER_FOCUS"),
        ({"focus_valid": False}, "FOCUS_UNAVAILABLE"),
    ],
)
def test_invalid_rounds_never_supply_evidence(changes, reason):
    history, diag = select([round_row(2, **changes), round_row()])
    assert [m["message_id"] for m in history] == ["u1", "a1"]
    assert diag["excluded"][0]["reason"] == reason


def test_oversized_latest_round_does_not_resurrect_older_context():
    history, diag = select([round_row(2, answer="很长" * 5000), round_row()])
    assert history == [] and diag["boundary"] == "TOKEN_BUDGET"


def test_budget_is_exact_and_stops_at_first_overflow():
    rows = [round_row(3), round_row(2, answer="长" * 5000), round_row()]
    history, diag = select(rows)
    assert len(history) == 2 and history[-1]["message_id"] == "a3"
    _, single = select([round_row()])
    assert len(select([round_row()], token_budget=single["estimated_tokens"])[0]) == 2
    assert select([round_row()], token_budget=single["estimated_tokens"] - 1)[0] == []
    assert "question" not in str(diag)


def test_switch_bridge_is_user_statement_without_old_model_answer():
    rows = [
        round_row(2, question="确认", rag_history_context={"origin_query": "如果更换B还失败呢？"}),
        round_row(1, focus_epoch=0, user_epoch=0, answer="旧主题结论"),
    ]
    original = deepcopy(rows)
    history, _ = select(rows)
    assert "如果更换B还失败呢" in history[0]["content"]
    assert "旧主题结论" not in render_history(history)
    assert rows == original


def test_prompt_preserves_negation_hypothesis_and_correction_with_distinct_roles():
    history, _ = select(
        [
            round_row(2, question="纠正：没有换过电源，只是假设换过。"),
            round_row(1, question="如果换过电源呢？", answer="可能是接线问题。"),
        ]
    )
    prompt = render_history(history)
    for original in ["没有换过电源", "如果换过电源", "可能是接线问题"]:
        assert original in prompt
    assert "未独立核实" in prompt and "不是事实证据" in prompt
    assert "较新陈述为准" in prompt and "历史模型猜测不得升级" in prompt
    assert "否定、条件和假设" in RAG_SYSTEM_GUARD


def test_total_budget_removes_history_before_evidence_and_keeps_question():
    rag = _service(_RoutingRepository(turn=_turn(), candidates=[]), _RecordingLlmProvider([]))
    rag.settings.m3_prompt_max_chars = 1100
    history = [{"role": role, "content": "历史" * 300} for role in ("USER", "ASSISTANT")]
    prompt = rag._render_bounded_prompt(
        "{{history}}\n{{context}}\n{{question}}",
        context="步骤" * 100,
        question="当前完整问题",
        history=history,
    )
    assert history == [] and "当前完整问题" in prompt and "步骤" * 100 in prompt
    with pytest.raises(ApiError, match="Prompt"):
        rag._render_bounded_prompt(
            "{{history}}{{context}}{{question}}",
            context="证据" * 1000,
            question="当前问题",
            history=[],
        )


def test_legacy_strategy_does_not_mutate_history_for_budget():
    rag = _service(_RoutingRepository(turn=_turn(), candidates=[]), _RecordingLlmProvider([]))
    rag.settings.m3_history_strategy = "legacy"
    rag.settings.m3_prompt_max_chars = 1000
    history = [{"role": "USER", "content": "长" * 2000}]
    with pytest.raises(ApiError):
        rag._render_bounded_prompt(
            "{{history}}{{context}}{{question}}", context="正文", question="问题", history=history
        )
    assert len(history) == 1


@pytest.mark.parametrize("first", ["旧标题\n步骤一", "旧标题", " \n旧标题\n步骤一"])
def test_document_prompt_uses_display_name_without_touching_later_body(first):
    doc = {
        "document_id": str(DOC_A),
        "title": "管理新名称",
        "elements": [
            {"element_type": "TEXT", "content": first},
            {"element_type": "TABLE", "content": "表格正文"},
            {"element_type": "TEXT", "content": "步骤二：此处正文提及旧标题，不应全局删除。"},
        ],
    }
    before = deepcopy(doc)
    prompt, _ = build_document_context(doc)
    assert "当前文档显示名称：管理新名称" in prompt
    assert "\n旧标题\n" not in prompt
    assert "表格正文" in prompt and "正文提及旧标题" in prompt
    assert doc == before


def test_document_prompt_preserves_ambiguous_oversized_first_line():
    merged_body = "这可能是标题，也可能是被解析器合并的正文。" * 20
    doc = {
        "document_id": str(DOC_A),
        "title": "管理新名称",
        "elements": [{"element_type": "TEXT", "content": merged_body}],
    }
    prompt, _ = build_document_context(doc)
    assert merged_body in prompt


async def test_chunk_heading_removal_requires_element_identity_and_exact_prefix():
    rag = _service(_RoutingRepository(turn=_turn(), candidates=[]), _RecordingLlmProvider([]))
    element = uuid4()
    doc = {
        "document_id": str(DOC_A),
        "title": "新名称",
        "elements": [{"element_id": element, "element_type": "TEXT", "content": "旧标题\n步骤一"}],
    }
    first = replace(_candidate(DOC_A, 1), content="旧标题\n步骤一", element_ids=[element])
    unrelated = replace(_candidate(DOC_A, 2), content="旧标题\n普通正文")
    mismatch = replace(first, content="普通章节\n正文")
    result = await rag._generation_candidates(_turn(), [first, unrelated, mismatch], document=doc)
    assert [c.content for c in result] == ["步骤一", "旧标题\n普通正文", "普通章节\n正文"]
    assert all(c.document == "新名称" for c in result)
    assert first.content == "旧标题\n步骤一"
    prompt, sources = build_context(result)
    assert "文档：新名称" in prompt and all(s.document == "新名称" for s in sources)


async def test_heading_metadata_query_is_scoped_to_kb_index_and_candidates():
    repo = _RoutingRepository(turn=_turn(), candidates=[])
    repo.get_generation_headings = AsyncMock(return_value={})
    rag = _service(repo, _RecordingLlmProvider([]))
    candidate = _candidate(DOC_A, 1)
    assert await rag._generation_candidates(repo.turn, [candidate]) == [candidate]
    repo.get_generation_headings.assert_awaited_once_with(
        index_id=repo.turn.index_id, knowledge_id=repo.turn.knowledge_id, document_ids=[DOC_A]
    )


async def test_generation_diagnostics_keep_history_ids_without_copying_text():
    turn = replace(
        _turn(),
        history=[
            {"role": "USER", "content": "用户敏感陈述", "message_id": "user-message"},
            {"role": "ASSISTANT", "content": "历史模型回答", "message_id": "assistant-message"},
        ],
        history_diagnostics={
            "strategy": "focused",
            "selected_message_ids": ["user-message", "assistant-message"],
        },
        has_prior_messages=True,
    )
    repo = _RoutingRepository(turn=turn, candidates=[_candidate(DOC_A, 1)])
    rag = _service(repo, _RecordingLlmProvider([]))
    prepared = await rag.prepare(
        knowledge_id=turn.knowledge_id,
        conversation_id=turn.conversation_id,
        question="继续说明",
        request_id="history-diagnostics",
    )
    plan, error = await rag._safely_plan_chat_turn(prepared, "继续说明", "产品", "支持")
    assert error is None
    history = plan.routing["generation"]["history"]
    assert history["used_message_ids"] == ["user-message", "assistant-message"]
    assert history["used_message_count"] == 2
    assert "用户敏感陈述" not in str(history)
    assert "历史模型回答" not in str(history)
