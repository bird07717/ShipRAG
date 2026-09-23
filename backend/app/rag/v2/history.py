"""RAG V2 history selection based only on explicit, completed turn links."""

from __future__ import annotations

from typing import Any

from app.common.text import estimate_tokens

_NON_TECHNICAL_INPUT_GUARDS = frozenset({"GREETING", "META_ACK"})


def select_complete_round_history(
    rounds: list[dict[str, Any]],
    *,
    max_rounds: int,
    max_messages: int,
    token_budget: int,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Select a recent contiguous window of complete user/assistant rounds.

    ``rounds`` must be newest first.  The caller resolves associations from
    ``message.rag_user_message_id`` or the durable Assistant request link; no
    sequence adjacency or legacy conversational state is inferred.
    """

    selected: list[tuple[list[dict[str, str]], list[str], list[str]]] = []
    excluded: list[dict[str, str]] = []
    used_tokens = 0
    boundary: str | None = None
    round_limit = min(max_rounds, max_messages // 2)

    for row in rounds:
        assistant_id = str(row["id"])
        if row.get("input_guard_reason") in _NON_TECHNICAL_INPUT_GUARDS:
            excluded.append({"message_id": assistant_id, "reason": "NON_TECHNICAL_SHORTCUT"})
            continue
        if not row.get("user_id"):
            excluded.append({"message_id": assistant_id, "reason": "UNLINKED_LEGACY"})
            continue
        if row.get("status") != "COMPLETED" or row.get("user_status") != "COMPLETED":
            excluded.append({"message_id": assistant_id, "reason": "INCOMPLETE_ROUND"})
            continue

        pair = [
            {"role": "USER", "content": str(row.get("question") or "")},
            {"role": "ASSISTANT", "content": str(row.get("answer") or "")},
        ]
        pair_tokens = sum(estimate_tokens(item["content"]) + 16 for item in pair)
        if len(selected) >= round_limit or used_tokens + pair_tokens > token_budget:
            boundary = "ROUND_LIMIT" if len(selected) >= round_limit else "TOKEN_BUDGET"
            excluded.append({"message_id": assistant_id, "reason": boundary})
            break

        raw_document_ids = row.get("used_document_ids")
        used_document_ids = (
            [str(value) for value in raw_document_ids if str(value).strip()][:3]
            if isinstance(raw_document_ids, list)
            else []
        )
        selected.append(
            (
                pair,
                [str(row["user_id"]), assistant_id],
                used_document_ids,
            )
        )
        used_tokens += pair_tokens

    chronological = list(reversed(selected))
    history = [message for pair, _ids, _documents in chronological for message in pair]
    selected_ids = [message_id for _pair, ids, _documents in chronological for message_id in ids]
    previous_document_ids = selected[0][2] if selected else []
    return history, {
        "strategy": "v2_complete_rounds",
        "selected_message_ids": selected_ids,
        "round_count": len(selected),
        "estimated_tokens": used_tokens,
        "previous_document_ids": previous_document_ids,
        "boundary": boundary,
        "excluded": excluded,
    }
