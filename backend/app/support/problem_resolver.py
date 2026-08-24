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

        # Deterministic catalog rules require a conjunction of any supplied
        # product/model and an action/keyword, avoiding a loose keyword match.
        candidates: list[ProblemProfile] = []
        for profile in available:
            terms = [*profile.keywords, *profile.model_names]
            term_words = {normalize_for_match(item) for item in terms if item.strip()}
            if term_words and any(term in normalized for term in term_words):
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
