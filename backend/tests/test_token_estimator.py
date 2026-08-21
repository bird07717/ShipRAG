"""Unit tests for the shared CJK-aware token estimator."""

from __future__ import annotations

from app.common.text import estimate_tokens


def test_empty_text_is_zero() -> None:
    assert estimate_tokens("") == 0


def test_cjk_counts_one_token_per_character() -> None:
    text = "将U盘插入VDR主机箱内的USB端口上。" * 10  # 130 CJK + 70 ASCII
    assert estimate_tokens(text) == 130 + 18


def test_pure_ascii_averages_four_chars_per_token() -> None:
    assert estimate_tokens("abcdefgh" * 10) == 20


def test_mixed_text_weighs_both_scripts() -> None:
    text = "将U盘插入" + "abcdefgh"  # 5 CJK + 8 ASCII
    assert estimate_tokens(text) == 5 + 2


def test_minimum_is_one_token() -> None:
    assert estimate_tokens("a") == 1
    assert estimate_tokens("甲") == 1


def test_cjk_punctuation_counts_as_cjk() -> None:
    assert estimate_tokens("，。：；！？") == 6
