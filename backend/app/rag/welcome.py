"""Welcome / guidance message generation for the demo chat entry.

Pure template logic: the welcome text is deterministic (no LLM call), derived
from the knowledge base metadata and its document catalog. It is rendered as
a frontend-only virtual message and is never persisted into a conversation,
so it neither creates conversations eagerly nor pollutes retrieval history.
"""

from __future__ import annotations

import re
from typing import Any

# "故障解决文档13\u2014FFC 如何检查接线盒" -> "FFC 如何检查接线盒"
# (em dash, en dash and plain hyphen all serve as title separators)
_TITLE_PREFIX_PATTERN = re.compile(r"^[^\u2014\u2013\-]{2,20}[\u2014\u2013\-]\s*")
_SUGGESTION_LIMIT = 3
_SUGGESTION_MIN_CHARS = 4


def build_suggestions(catalog: list[dict[str, Any]]) -> list[str]:
    """Turn document titles into clickable starter questions."""
    suggestions: list[str] = []
    for item in catalog:
        if len(suggestions) >= _SUGGESTION_LIMIT:
            break
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        stripped = _TITLE_PREFIX_PATTERN.sub("", title).strip()
        if len(stripped) < _SUGGESTION_MIN_CHARS:
            continue
        suggestions.append(stripped + "？")
    return suggestions


def build_welcome(kb: dict[str, Any], catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the welcome payload for a knowledge base."""
    name = str(kb.get("name", "")).strip() or "知识库"
    description = str(kb.get("description") or "").strip()
    enabled = str(kb.get("status", "")) == "ENABLED"
    ready = enabled and bool(kb.get("active_index_id")) and bool(catalog)

    if not enabled:
        return {
            "knowledge_base": {"id": str(kb["id"]), "name": name, "ready": False},
            "message": f"「{name}」知识库当前已停用，请联系管理员启用后再试。",
            "suggestions": [],
        }

    if not ready:
        message = (
            f"你好！我是「{name}」知识库的文档助手。\n"
            "当前知识库还没有可用的文档索引。请先在管理页上传文档并构建索引，"
            "完成后我就可以基于文档内容为你解答产品相关问题。"
        )
        if not catalog:
            message = (
                f"你好！我是「{name}」知识库的文档助手。\n"
                "当前知识库还没有收录文档。请先在管理页上传产品文档，"
                "构建索引完成后我就可以为你定位文档并解答问题。"
            )
        return {
            "knowledge_base": {"id": str(kb["id"]), "name": name, "ready": False},
            "message": message,
            "suggestions": [],
        }

    message = f"你好！我是「{name}」知识库的文档助手，当前已收录 {len(catalog)} 份文档。"
    if description:
        message += f"\n{description}"
    message += (
        "\n你可以直接描述故障现象或操作目标（例如：如何升级、如何更换部件），"
        "我会为你定位相关文档，并基于文档内容解答；也可以点击下方的问题开始。"
    )
    return {
        "knowledge_base": {"id": str(kb["id"]), "name": name, "ready": True},
        "message": message,
        "suggestions": build_suggestions(catalog),
    }
