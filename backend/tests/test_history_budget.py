"""Unit tests for RagRepository._budget_history take-then-stop semantics.

Token math uses CJK characters ("啊" and friends), which cost exactly one
token each under the shared CJK-aware estimator in app.common.text.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.rag.repository import RagRepository


def _repository(token_budget: int) -> RagRepository:
    settings = Settings(
        _env_file=None,
        app_env="test",
        m3_history_token_budget=token_budget,
    )
    return RagRepository(cast(AsyncEngine, None), settings)


def _messages(*specs: tuple[str, str, int]) -> list[dict[str, Any]]:
    # oldest -> newest, as returned by the begin_turn history query
    return [{"role": role, "content": content * size} for role, content, size in specs]


def test_budget_history_keeps_recent_window_within_budget() -> None:
    repo = _repository(token_budget=300)
    messages = _messages(
        ("USER", "甲", 100),
        ("ASSISTANT", "乙", 100),
        ("USER", "丙", 100),
    )

    history = repo._budget_history(messages)

    # each message costs 100 tokens; budget 300 fits all three, in order
    assert [item["content"][0] for item in history] == ["甲", "乙", "丙"]


def test_budget_history_drops_oldest_not_newest() -> None:
    repo = _repository(token_budget=200)
    messages = _messages(
        ("USER", "甲", 100),
        ("ASSISTANT", "乙", 100),
        ("USER", "丙", 100),
    )

    history = repo._budget_history(messages)

    # only the two newest fit; the oldest is dropped, never the newest
    assert [item["content"][0] for item in history] == ["乙", "丙"]


def test_budget_history_never_leaves_a_hole_in_the_middle() -> None:
    repo = _repository(token_budget=300)
    # newest assistant answer is huge; the three older turns are compact
    messages = _messages(
        ("USER", "甲", 100),
        ("ASSISTANT", "乙", 100),
        ("USER", "丙", 100),
        ("ASSISTANT", "丁", 560),
    )

    history = repo._budget_history(messages)

    # the oversized newest message is skipped, the rest is kept contiguously
    assert [item["content"][0] for item in history] == ["甲", "乙", "丙"]


def test_budget_history_stops_at_first_overflow_after_taking() -> None:
    repo = _repository(token_budget=200)
    # middle message overflows once filling has started
    messages = _messages(
        ("USER", "甲", 100),
        ("ASSISTANT", "乙", 400),
        ("USER", "丙", 100),
    )

    history = repo._budget_history(messages)

    # "丙" is taken first; "乙" overflows and stops the walk: "甲" must NOT be
    # selected behind a hole even though it would fit on its own
    assert [item["content"][0] for item in history] == ["丙"]


def test_budget_history_all_oversized_returns_empty() -> None:
    repo = _repository(token_budget=100)
    messages = _messages(
        ("USER", "甲", 400),
        ("ASSISTANT", "乙", 400),
    )

    history = repo._budget_history(messages)

    assert history == []


def test_budget_history_preserves_order_and_roles() -> None:
    repo = _repository(token_budget=10_000)
    messages = [
        {"role": "USER", "content": "怎么插U盘"},
        {"role": "ASSISTANT", "content": "将U盘插入USB端口。"},
        {"role": "USER", "content": "然后呢"},
    ]

    history = repo._budget_history(messages)

    assert [(item["role"], item["content"]) for item in history] == [
        ("USER", "怎么插U盘"),
        ("ASSISTANT", "将U盘插入USB端口。"),
        ("USER", "然后呢"),
    ]
