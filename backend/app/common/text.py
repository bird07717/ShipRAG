from __future__ import annotations

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
