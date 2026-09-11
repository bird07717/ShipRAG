from __future__ import annotations

import math
import re
import unicodedata
from functools import partial

# CJK Unified Ideographs + Extension A, common CJK punctuation and fullwidth forms
_CJK_PATTERN = re.compile("[\u2e80-\u9fff\u3000-\u303f\uff00-\uffef\U00020000-\U0002a6df]")
_MARKDOWN_INLINE_SPECIAL = re.compile(r"([\\`*_{}\[\]<>#+\-.!|])")

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


def markdown_document_title(title: str) -> str:
    """Render an untrusted document title as a bold Markdown label."""
    escaped = _MARKDOWN_INLINE_SPECIAL.sub(r"\\\1", title.strip())
    return f"**《{escaped}》**"


def strip_leading_document_heading(content: str) -> str:
    """Drop the first nonblank text line: source documents start with a title.

    Call once on the document's first nonblank TEXT element, at delivery time.
    Keep subsequent lines verbatim; never compare with the display name.
    """
    lines = content.splitlines(keepends=True)
    first_index = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first_index is None:
        return content
    return "".join(lines[first_index + 1 :])


def _emphasized_title_replacement(match: re.Match[str], *, source: str, rendered: str) -> str:
    before = source[match.start() - 1] if match.start() else ""
    after = source[match.end()] if match.end() < len(source) else ""
    leading = (
        ""
        if not before or before.isspace() or unicodedata.category(before).startswith("P")
        else " "
    )
    trailing = (
        "" if not after or after.isspace() or unicodedata.category(after).startswith("P") else " "
    )
    return leading + rendered + trailing


def emphasize_document_title_mentions(text: str, titles: list[str]) -> str:
    """Bold exact 《title》 mentions without touching already formatted labels."""
    result = text
    for title in sorted({item.strip() for item in titles if item.strip()}, key=len, reverse=True):
        label = f"《{title}》"
        rendered = markdown_document_title(title)
        result = re.sub(
            rf"(?<!\*\*){re.escape(label)}(?!\*\*)",
            partial(_emphasized_title_replacement, source=result, rendered=rendered),
            result,
        )
    return result
