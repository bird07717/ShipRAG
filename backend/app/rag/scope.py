from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.common.text import estimate_tokens

DEFAULT_PRODUCT_SCOPE = "本产品相关的问题，包括参数、功能、使用步骤、维护和故障排查"

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class ScopeSnapshot:
    description: str
    sha256: str
    chars: int
    tokens: int
    truncated: bool


def normalize_scope_description(value: str | None) -> str:
    return _SPACE.sub(" ", _CONTROL.sub("", value or "")).strip()


def build_scope_snapshot(description: str | None, *, max_tokens: int) -> ScopeSnapshot:
    """Build the bounded public knowledge-scope snapshot used by RAG V2."""

    public_scope = normalize_scope_description(description)
    combined = DEFAULT_PRODUCT_SCOPE
    if public_scope:
        combined += "；当前知识库公开范围：" + public_scope
    truncated = estimate_tokens(combined) > max_tokens
    if truncated:
        low, high = 0, len(combined)
        while low < high:
            middle = (low + high + 1) // 2
            if estimate_tokens(combined[:middle]) <= max_tokens:
                low = middle
            else:
                high = middle - 1
        combined = combined[:low].rstrip()
    return ScopeSnapshot(
        description=combined,
        sha256=hashlib.sha256(combined.encode("utf-8")).hexdigest(),
        chars=len(combined),
        tokens=estimate_tokens(combined),
        truncated=truncated,
    )
