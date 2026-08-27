from __future__ import annotations

import re
import unicodedata
from collections.abc import Awaitable, Callable
from typing import Any

from app.support.models import ProblemDecision, ProblemProfile, ProblemResolution


def normalize_for_match(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in value
        if not character.isspace() and not unicodedata.category(character).startswith("P")
    )


CatalogClassifier = Callable[[str, list[ProblemProfile]], Awaitable[str | None]]

_TASK_TERMS: dict[str, tuple[str, ...]] = {
    "UPGRADE": ("升级", "更新", "刷版本", "update"),
    "CHECK": ("检查", "检测", "自检", "check"),
    "REPLACEMENT": ("更换", "替换", "replacement", "replace"),
    "CONFIGURATION": ("配置", "设置", "修改", "configuration", "configure"),
    "ALARM": ("报警", "告警", "异常", "alarm"),
}

_GENERIC_ANCHORS = {
    "升级",
    "更新",
    "异常",
    "故障",
    "更换",
    "替换",
    "报警",
    "告警",
    "检查",
    "配置",
    "设置",
    "怎么处理",
    "如何处理",
}
_ASCII_IDENTIFIER = re.compile(r"[a-z]+[a-z0-9_-]*\d+[a-z0-9_-]*|[a-z]{3,}", re.IGNORECASE)


def _normalized_terms(values: list[str]) -> set[str]:
    return {term for value in values if (term := normalize_for_match(value))}


def _profile_product_terms(profile: ProblemProfile) -> set[str]:
    values = [*profile.model_names]
    if profile.product_name:
        values.append(profile.product_name)
    # Existing generated Profiles often carry a product/model identifier in
    # keywords. Treat only ASCII identifiers as product evidence; ordinary
    # Chinese keywords remain weak supporting terms.
    values.extend(value for value in profile.keywords if _ASCII_IDENTIFIER.search(value))
    return _normalized_terms(values)


def _profile_task_terms(profile: ProblemProfile) -> set[str]:
    result: set[str] = set()
    if profile.task_type:
        result.update(
            _normalized_terms(
                list(_TASK_TERMS.get(profile.task_type.upper(), (profile.task_type,)))
            )
        )
    normalized_keywords = _normalized_terms(profile.keywords)
    for terms in _TASK_TERMS.values():
        normalized_group = _normalized_terms(list(terms))
        if normalized_keywords & normalized_group:
            result.update(normalized_group)
    return result


def _matches_exclusion(normalized_question: str, profile: ProblemProfile) -> bool:
    return any(
        len(term) >= 4 and term in normalized_question
        for term in _normalized_terms(profile.exclusions)
    )


def _is_distinctive_anchor(alias: str, profile: ProblemProfile) -> bool:
    normalized = normalize_for_match(alias)
    if len(normalized) < 6 or normalized in _GENERIC_ANCHORS:
        return False
    product_terms = _profile_product_terms(profile)
    return any(term in normalized for term in product_terms) or bool(
        _ASCII_IDENTIFIER.search(alias)
    )


class ProblemResolver:
    """Closed-set resolver. It never uses chunk retrieval or produces an answer."""

    def __init__(self, catalog_classifier: CatalogClassifier | None = None) -> None:
        self.catalog_classifier = catalog_classifier

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
        routable = [profile for profile in available if not _matches_exclusion(normalized, profile)]
        canonical = [p for p in routable if normalize_for_match(p.canonical_question) == normalized]
        if len(canonical) == 1:
            return ProblemResolution(
                ProblemDecision.MATCHED, "CANONICAL_QUESTION", 1.0, canonical[0]
            )
        if len(canonical) > 1:
            return ProblemResolution(
                ProblemDecision.AMBIGUOUS, "CANONICAL_CONFLICT", 0.0, choices=canonical
            )
        aliases = [
            p for p in routable if normalized in {normalize_for_match(alias) for alias in p.aliases}
        ]
        if len(aliases) == 1:
            return ProblemResolution(ProblemDecision.MATCHED, "ALIAS", 1.0, aliases[0])
        if len(aliases) > 1:
            return ProblemResolution(
                ProblemDecision.AMBIGUOUS, "ALIAS_CONFLICT", 0.0, choices=aliases
            )

        # A reviewed, distinctive alias may anchor a longer natural-language
        # question. Exact alias matching above remains the primary path. This
        # extension deliberately rejects generic aliases and requires catalog
        # uniqueness so words such as "升级" can never route by substring.
        anchor_matches: list[ProblemProfile] = []
        for profile in routable:
            for alias in profile.aliases:
                anchor = normalize_for_match(alias)
                if (
                    len(normalized) > len(anchor)
                    and anchor in normalized
                    and _is_distinctive_anchor(alias, profile)
                ):
                    anchor_matches.append(profile)
                    break
        unique_anchor_matches = list(
            {profile.document_id: profile for profile in anchor_matches}.values()
        )
        if len(unique_anchor_matches) == 1:
            return ProblemResolution(
                ProblemDecision.MATCHED,
                "ALIAS_ANCHOR",
                0.95,
                unique_anchor_matches[0],
            )
        if len(unique_anchor_matches) > 1:
            return ProblemResolution(
                ProblemDecision.AMBIGUOUS,
                "ALIAS_ANCHOR_CONFLICT",
                0.0,
                choices=unique_anchor_matches,
            )

        # Structured routing requires two independent evidence dimensions.
        # Multiple ordinary keywords never count as independent evidence.
        candidates: list[ProblemProfile] = []
        for profile in routable:
            product_match = any(term in normalized for term in _profile_product_terms(profile))
            task_match = any(term in normalized for term in _profile_task_terms(profile))
            symptom_match = any(
                len(term) >= 4 and term in normalized
                for term in _normalized_terms(profile.symptoms)
            )
            if (product_match and (task_match or symptom_match)) or (task_match and symptom_match):
                candidates.append(profile)
        if len(candidates) == 1:
            return ProblemResolution(
                ProblemDecision.MATCHED, "DETERMINISTIC_RULE", 0.9, candidates[0]
            )
        if len(candidates) > 1:
            # A classifier can disambiguate only from this closed profile set.
            if self.catalog_classifier:
                code = await self.catalog_classifier(question, candidates)
                if code:
                    matching = [profile for profile in candidates if profile.problem_code == code]
                    if len(matching) == 1:
                        return ProblemResolution(
                            ProblemDecision.MATCHED, "LLM_CATALOG", 0.7, matching[0]
                        )
            return ProblemResolution(
                ProblemDecision.AMBIGUOUS,
                "STRUCTURED_CONFLICT",
                0.0,
                choices=candidates,
            )

        if self.catalog_classifier and routable:
            code = await self.catalog_classifier(question, routable)
            if code:
                matching = [profile for profile in routable if profile.problem_code == code]
                if len(matching) == 1:
                    return ProblemResolution(
                        ProblemDecision.MATCHED, "LLM_CATALOG", 0.7, matching[0]
                    )
        method = "EXCLUSION" if routable != available else "NO_MATCH"
        return ProblemResolution(ProblemDecision.NO_MATCH, method, 0.0)
