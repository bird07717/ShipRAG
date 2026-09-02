"""Safety helpers for user-controlled asset names and public payloads."""

from __future__ import annotations

import re
from urllib.parse import quote

from app.common.errors import ApiError

_CONTROL_CHARACTERS = frozenset(chr(value) for value in (*range(0, 32), 127))
_ASCII_FILENAME = re.compile(r"[^A-Za-z0-9!#$&+.^_`|~-]+")


def reject_control_characters(value: str, *, field_name: str) -> str:
    """Reject persisted-name values that could escape an HTTP header or log line."""
    if any(character in _CONTROL_CHARACTERS for character in value):
        raise ApiError("VALIDATION_ERROR", f"{field_name}不能包含控制字符", 422)
    return value


def attachment_content_disposition(filename: str) -> str:
    """Build a header-safe attachment disposition for both new and legacy names.

    Upload validation prevents controls for new data. Removing them here as
    well keeps an older database row from becoming a response-header injection
    primitive during a rolling deployment or data migration.
    """
    safe_name = "".join(
        "_" if character in _CONTROL_CHARACTERS else character for character in filename.strip()
    )
    safe_name = safe_name or "download"
    fallback = _ASCII_FILENAME.sub("_", safe_name).strip("._") or "download"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(safe_name, safe='')}"
