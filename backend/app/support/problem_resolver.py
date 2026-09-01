from __future__ import annotations

import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from app.core.config import Settings
from app.support.models import ProblemDecision, ProblemProfile, ProblemResolution


def normalize_for_match(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in value
        if not character.isspace() and not unicodedata.category(character).startswith("P")
    )


# Kept for call-site compatibility. Profile resolution deliberately never
# invokes an online classifier: uncertain routing belongs to RAG.
CatalogClassifier = Callable[[str, list[ProblemProfile]], Awaitable[str | None]]

_GENERIC_ALIAS_TERMS = {
    "升级",
    "更新",
    "配置",
    "设置",
    "检查",
    "检测",
    "异常",
    "故障",
    "报警",
    "告警",
    "怎么处理",
    "如何处理",
    "设备升级",
    "升级失败",
    "设备异常",
}
_CODE_PATTERN = re.compile(r"\b[a-z]*\d[a-z0-9_-]*\b", re.IGNORECASE)
_UPPER_ACRONYM_PATTERN = re.compile(r"[A-Z]{3,}")


@dataclass(frozen=True, slots=True)
class _ProfileScore:
    profile: ProblemProfile
    total: float
    canonical: float
    alias: float
    summary: float
    alias_specific: bool

    @property
    def direct_evidence(self) -> bool:
        # Alias alone cannot direct-route unless it is a specific identifier.
        return (
            self.canonical >= 0.86
            or (self.canonical >= 0.70 and self.summary >= 0.62)
            or (self.alias_specific and self.alias >= 0.96)
        )


def _character_bigrams(value: str) -> set[str]:
    if len(value) < 2:
        return {value} if value else set()
    return {value[index : index + 2] for index in range(len(value) - 1)}


def _similarity(question: str, profile_text: str) -> float:
    """Local similarity for short standard scenarios, not semantic retrieval."""
    left, right = normalize_for_match(question), normalize_for_match(profile_text)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    containment = len(shorter) / len(longer) if len(shorter) >= 4 and shorter in longer else 0.0
    left_bigrams, right_bigrams = _character_bigrams(left), _character_bigrams(right)
    union = left_bigrams | right_bigrams
    jaccard = len(left_bigrams & right_bigrams) / len(union) if union else 0.0
    sequence = SequenceMatcher(a=left, b=right, autojunk=False).ratio()
    return max(containment, 0.55 * sequence + 0.45 * jaccard)


def _is_specific_alias(alias: str) -> bool:
    normalized = normalize_for_match(alias)
    if len(normalized) < 6 or normalized in _GENERIC_ALIAS_TERMS:
        return False
    # A long natural-language Alias can still be broad ("设备升级失败"). Only
    # a model, error code, or other ASCII identifier is safe as a direct alias
    # anchor; every other Alias remains a RAG candidate signal.
    display_alias = unicodedata.normalize("NFKC", alias)
    return bool(_CODE_PATTERN.search(normalized) or _UPPER_ACRONYM_PATTERN.search(display_alias))


def _score_profile(
    question: str,
    profile: ProblemProfile,
    available: list[ProblemProfile],
    settings: Settings | None,
) -> _ProfileScore:
    canonical = _similarity(question, profile.canonical_question)
    aliases = [(_similarity(question, value), value) for value in profile.aliases]
    alias, alias_text = max(aliases, default=(0.0, ""), key=lambda item: item[0])
    alias_specific = bool(alias_text) and _is_specific_alias(alias_text)
    normalized_question = normalize_for_match(question)
    if alias_specific and normalize_for_match(alias_text) in normalized_question:
        # A unique model/error-code Alias embedded in a longer question is
        # still strong evidence, not a generic substring match.
        alias = max(alias, 0.96)
    summary = _similarity(question, profile.routing_summary)
    canonical_weight = settings.profile_canonical_weight if settings else 0.60
    summary_weight = settings.profile_summary_weight if settings else 0.25
    alias_weight = settings.profile_alias_weight if settings else 0.15
    if alias_text and not alias_specific:
        penalty = settings.profile_generic_alias_penalty if settings else 0.35
        alias *= penalty
    total = canonical_weight * canonical + summary_weight * summary + alias_weight * alias
    # Do not let an omitted summary or a deliberately low Alias weight erase
    # an otherwise strong standard-question match.
    total = max(total, 0.95 * canonical)
    if alias_specific:
        total = max(total, 0.90 * alias)
    return _ProfileScore(
        profile=profile,
        total=round(total, 4),
        canonical=round(canonical, 4),
        alias=round(alias, 4),
        summary=round(summary, 4),
        alias_specific=alias_specific,
    )


class ProblemResolver:
    """High-confidence Profile shortcut; all ambiguity falls through to RAG."""

    def __init__(
        self,
        catalog_classifier: CatalogClassifier | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings
        self.catalog_classifier = catalog_classifier

    @property
    def _direct_threshold(self) -> float:
        return self.settings.profile_direct_threshold if self.settings else 0.84

    @property
    def _candidate_threshold(self) -> float:
        return self.settings.profile_candidate_threshold if self.settings else 0.60

    @property
    def _direct_gap(self) -> float:
        return self.settings.profile_direct_gap if self.settings else 0.08

    async def resolve(
        self,
        *,
        question: str,
        profiles: list[ProblemProfile],
        pending_options: list[dict[str, Any]] | None = None,
        choice_id: str | None = None,
    ) -> ProblemResolution:
        available = [profile for profile in profiles if profile.enabled]
        if choice_id:
            for option in pending_options or []:
                if option.get("choice_id") == choice_id:
                    document_id = str(option.get("document_id", ""))
                    for profile in available:
                        if str(profile.document_id) == document_id:
                            return ProblemResolution(
                                ProblemDecision.MATCHED, "PENDING_CHOICE", 1.0, profile
                            )
            return ProblemResolution(ProblemDecision.NO_MATCH, "INVALID_CHOICE", 0.0)

        normalized = normalize_for_match(question)
        if not normalized:
            return ProblemResolution(ProblemDecision.NO_MATCH, "NO_MATCH", 0.0)

        exact_canonical = [
            profile
            for profile in available
            if normalize_for_match(profile.canonical_question) == normalized
        ]
        if len(exact_canonical) == 1:
            return ProblemResolution(
                ProblemDecision.MATCHED, "CANONICAL_QUESTION", 1.0, exact_canonical[0]
            )
        if len(exact_canonical) > 1:
            return ProblemResolution(ProblemDecision.NO_MATCH, "CANONICAL_CONFLICT_RAG", 1.0)

        exact_alias = [
            profile
            for profile in available
            if normalized in {normalize_for_match(alias) for alias in profile.aliases}
        ]
        if len(exact_alias) == 1:
            alias = next(
                value
                for value in exact_alias[0].aliases
                if normalize_for_match(value) == normalized
            )
            if _is_specific_alias(alias):
                return ProblemResolution(ProblemDecision.MATCHED, "ALIAS", 1.0, exact_alias[0])
            return ProblemResolution(ProblemDecision.NO_MATCH, "GENERIC_ALIAS_RAG", 1.0)
        if len(exact_alias) > 1:
            return ProblemResolution(ProblemDecision.NO_MATCH, "ALIAS_CONFLICT_RAG", 1.0)

        ranked = sorted(
            (_score_profile(question, profile, available, self.settings) for profile in available),
            key=lambda item: item.total,
            reverse=True,
        )
        if not ranked or ranked[0].total < self._candidate_threshold:
            return ProblemResolution(ProblemDecision.NO_MATCH, "NO_MATCH", 0.0)

        top = ranked[0]
        runner = ranked[1] if len(ranked) > 1 else None
        runner_score = runner.total if runner else 0.0
        top_is_clear = top.total - runner_score >= self._direct_gap
        runner_has_evidence = runner is not None and runner.direct_evidence
        if (
            top.total >= self._direct_threshold
            and top.direct_evidence
            and (top_is_clear or not runner_has_evidence)
        ):
            return ProblemResolution(
                ProblemDecision.MATCHED, "WEIGHTED_PROFILE", top.total, top.profile
            )

        close_candidates = [
            item
            for item in ranked
            if item.total >= self._candidate_threshold and top.total - item.total < self._direct_gap
        ]
        method = (
            "PROFILE_MULTI_CANDIDATE"
            if len(close_candidates) >= 2
            else "PROFILE_BELOW_DIRECT_THRESHOLD"
        )
        return ProblemResolution(ProblemDecision.NO_MATCH, method, top.total)
