from __future__ import annotations

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
        canonical = [
            p for p in available if normalize_for_match(p.canonical_question) == normalized
        ]
        if len(canonical) == 1:
            return ProblemResolution(
                ProblemDecision.MATCHED, "CANONICAL_QUESTION", 1.0, canonical[0]
            )
        if len(canonical) > 1:
            return ProblemResolution(
                ProblemDecision.AMBIGUOUS, "CANONICAL_CONFLICT", 0.0, choices=canonical
            )
        aliases = [
            p
            for p in available
            if normalized in {normalize_for_match(alias) for alias in p.aliases}
        ]
        if len(aliases) == 1:
            return ProblemResolution(ProblemDecision.MATCHED, "ALIAS", 1.0, aliases[0])
        if len(aliases) > 1:
            return ProblemResolution(
                ProblemDecision.AMBIGUOUS, "ALIAS_CONFLICT", 0.0, choices=aliases
            )

        # A model name by itself is commonly shared by many problem documents.
        # Deterministic routing therefore requires product/model + intent when
        # both dimensions exist.  Legacy profiles without structured product
        # metadata need at least two keyword hits; otherwise the closed catalog
        # classifier makes the decision.
        candidates: list[ProblemProfile] = []
        for profile in available:
            product_terms = [*profile.model_names]
            if profile.product_name:
                product_terms.append(profile.product_name)
            product_words = {normalize_for_match(item) for item in product_terms if item.strip()}
            intent_terms = list(profile.keywords)
            if profile.task_type:
                intent_terms.extend(
                    _TASK_TERMS.get(profile.task_type.upper(), (profile.task_type,))
                )
            intent_words = {normalize_for_match(item) for item in intent_terms if item.strip()}
            product_match = any(term in normalized for term in product_words)
            intent_hits = sum(term in normalized for term in intent_words)
            if product_words and intent_words and product_match and intent_hits:
                candidates.append(profile)
            elif not product_words and intent_words and intent_hits >= min(2, len(intent_words)):
                candidates.append(profile)
        if len(candidates) == 1:
            return ProblemResolution(
                ProblemDecision.MATCHED, "DETERMINISTIC_RULE", 0.9, candidates[0]
            )
        if len(candidates) > 1:
            # A classifier can disambiguate only from this closed profile set.
            available = candidates

        if self.catalog_classifier and available:
            code = await self.catalog_classifier(question, available)
            if code:
                matching = [profile for profile in available if profile.problem_code == code]
                if len(matching) == 1:
                    return ProblemResolution(
                        ProblemDecision.MATCHED, "LLM_CATALOG", 0.7, matching[0]
                    )
        return ProblemResolution(ProblemDecision.NO_MATCH, "NO_MATCH", 0.0)
