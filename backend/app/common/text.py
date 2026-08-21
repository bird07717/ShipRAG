from __future__ import annotations

import math
import re

# CJK Unified Ideographs + Extension A, common CJK punctuation and fullwidth forms
_CJK_PATTERN = re.compile(
    "[\u2e80-\u9fff\u3000-\u303f\uff00-\uffef\U00020000-\U0002a6df]"
)

_MOJIBAKE_MARKERS = (
    "锟斤拷",
    "鈥",
    "銆",
    "锛",
    "锝",
    "鏁",
    "瑙",
    "鍥",
    "閫",
    "濡",
    "浣",
)


def _mojibake_score(value: str) -> int:
    marker_score = sum(value.count(marker) for marker in _MOJIBAKE_MARKERS)
    private_use_score = sum(2 for character in value if "\ue000" <= character <= "\uf8ff")
    return marker_score + private_use_score + value.count("�") * 3


def repair_utf8_mojibake(value: str) -> str:
    """Repair UTF-8 text that was accidentally decoded as GB18030.

    The conversion is accepted only when it measurably removes common mojibake
    markers, so ordinary Chinese names are left unchanged.
    """

    original_score = _mojibake_score(value)
    if original_score == 0:
        return value
    try:
        repaired = value.encode("gb18030").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    return repaired if _mojibake_score(repaired) < original_score else value


def estimate_tokens(text: str) -> int:
    """Estimate LLM token usage with CJK-aware weighting.

    CJK characters tokenize roughly 1:1 for mainstream bilingual models,
    while ASCII text averages about 4 characters per token. The legacy
    ``len(text) / 3`` heuristic underestimated pure-Chinese content by ~3x,
    which silently inflated every token budget built on top of it.
    """
    if not text:
        return 0
    cjk_count = len(_CJK_PATTERN.findall(text))
    other_count = len(text) - cjk_count
    return max(1, math.ceil(cjk_count + other_count / 4))
