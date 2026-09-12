"""Deterministic, focus-scoped complete rounds; never summarize user claims."""

from typing import Any

from app.common.text import estimate_tokens


def select_history(
    rounds: list[dict[str, Any]], *, focus_epoch: int, max_messages: int, token_budget: int
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Input is newest first, with explicit user/assistant associations."""
    selected: list[list[dict[str, str]]] = []
    excluded: list[dict[str, str]] = []
    used = 0
    boundary: str | None = None
    for row in rounds:
        reason = None
        if row.get("focus_valid") is False:
            reason = "FOCUS_UNAVAILABLE"
        elif not row.get("user_id"):
            reason = "UNLINKED_LEGACY"
        elif row.get("status") != "COMPLETED" or row.get("user_status") != "COMPLETED":
            reason = "INCOMPLETE_ROUND"
        elif row.get("focus_epoch") != focus_epoch or row.get("user_epoch") != focus_epoch:
            reason = "OTHER_FOCUS"
        if reason:
            excluded.append({"message_id": str(row["id"]), "reason": reason})
            continue
        user = str(row.get("question") or "")
        bridge = (row.get("rag_history_context") or {}).get("origin_query")
        if bridge:
            user = "前置待确认问题（用户陈述）：" + str(bridge) + "\n本轮用户回复：" + user
        pair = [
            {"role": "USER", "content": user, "message_id": str(row["user_id"])},
            {
                "role": "ASSISTANT",
                "content": str(row.get("answer") or ""),
                "message_id": str(row["id"]),
            },
        ]
        cost = sum(estimate_tokens(item["content"]) + 16 for item in pair)
        if len(selected) * 2 + 2 > max_messages or used + cost > token_budget:
            boundary = "ROUND_LIMIT" if len(selected) * 2 + 2 > max_messages else "TOKEN_BUDGET"
            excluded.append({"message_id": str(row["id"]), "reason": boundary})
            break  # never skip an oversized recent round to impersonate continuity
        selected.append(pair)
        used += cost
    history = [item for pair in reversed(selected) for item in pair]
    return history, {
        "strategy": "focused",
        "focus_epoch": focus_epoch,
        "selected_message_ids": [item["message_id"] for item in history],
        "round_count": len(selected),
        "estimated_tokens": used,
        "boundary": boundary,
        "excluded": excluded,
    }
