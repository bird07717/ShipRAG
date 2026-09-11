from __future__ import annotations

import math
import re
import unicodedata
from functools import partial

# CJK Unified Ideographs + Extension A, common CJK punctuation and fullwidth forms
_CJK_PATTERN = re.compile("[\u2e80-\u9fff\u3000-\u303f\uff00-\uffef\U00020000-\U0002a6df]")
_MARKDOWN_INLINE_SPECIAL = re.compile(r"([\\`*_{}\[\]<>#+\-.!|])")
_LEGACY_DOCUMENT_HEADING_PREFIX = re.compile(
    r"^\s*故障解决文档\s*\d*\s*[\u2014\u2013\-:：]\s*",
    flags=re.IGNORECASE,
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


def markdown_document_title(title: str) -> str:
    """Render an untrusted document title as a bold Markdown label."""
    escaped = _MARKDOWN_INLINE_SPECIAL.sub(r"\\\1", title.strip())
    return f"**《{escaped}》**"


def _normalize_heading(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in normalized
        if not character.isspace() and not unicodedata.category(character).startswith("P")
    )


def strip_redundant_document_heading(content: str, title: str) -> str:
    """Remove a duplicated leading document-title line while preserving the body."""
    if not content.strip() or not title.strip():
        return content
    lines = content.splitlines()
    first_index = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first_index is None:
        return content
    heading = lines[first_index].strip()
    normalized_title = _normalize_heading(title)
    without_legacy_prefix = _LEGACY_DOCUMENT_HEADING_PREFIX.sub("", heading).strip()
    if _normalize_heading(heading) != normalized_title and (
        without_legacy_prefix == heading
        or _normalize_heading(without_legacy_prefix) != normalized_title
    ):
        return content
    remaining = lines[:first_index] + lines[first_index + 1 :]
    return "\n".join(remaining).lstrip("\n")


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
