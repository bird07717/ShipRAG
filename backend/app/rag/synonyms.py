"""BM25 domain-synonym query expansion.

The corpus describes one product family whose documents shift vocabulary for
the same action ("自检" vs "检查", "替换" vs "更换"). Exact-token BM25 is
blind to the paraphrase, and the vector channel demonstrably does not rank
the right document high enough (verified 2026-08-31: 《如何手动自检VDR》
sits outside the vector top-12 for the query "如何检查VDR").

Every group is deliberately small and high-precision: expansion only ADDS
candidates (the original query's results are never rewritten), and rerank
plus the routing thresholds restore precision downstream.
"""

from __future__ import annotations

DOMAIN_SYNONYM_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"检查", "自检", "检测", "排查"}),
    frozenset({"更换", "替换"}),
    frozenset({"报警", "告警"}),
    frozenset({"核心板", "主板"}),
    frozenset({"升级", "update"}),
    frozenset({"麦克风", "mic"}),
)


def bm25_expansion_terms(query: str) -> list[str]:
    """Return group members worth an extra BM25 pass for this query.

    A group contributes its other members only when the query already uses
    one of them (substring match, case-insensitive for latin terms).
    """
    lowered = query.casefold()
    terms: list[str] = []
    for group in DOMAIN_SYNONYM_GROUPS:
        present = [member for member in group if member.casefold() in lowered]
        if present:
            terms.extend(member for member in group if member not in present)
    return sorted(terms)
