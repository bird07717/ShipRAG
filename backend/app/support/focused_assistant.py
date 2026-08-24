from __future__ import annotations

from typing import Any

from app.rag.llm import LlmProvider
from app.support.models import FocusedDocument, SupportDraft
from app.support.prompt import FOCUSED_SUPPORT_SYSTEM, build_focused_prompt
from app.support.validator import validate_draft


class FocusedSupportAssistant:
    def __init__(
        self, provider: LlmProvider, *, max_tokens: int, temperature: float, timeout_seconds: float
    ) -> None:
        self.provider = provider
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds

    async def answer(
        self,
        *,
        question: str,
        document: FocusedDocument,
        workflow: dict[str, Any],
        history: list[dict[str, str]],
    ) -> tuple[SupportDraft, dict[str, str], dict[str, str]]:
        prompt, evidence_registry, image_registry = build_focused_prompt(
            question=question, document=document, workflow=workflow, history=history
        )
        draft = await self.provider.complete_structured(
            prompt,
            system=FOCUSED_SUPPORT_SYSTEM,
            schema=SupportDraft,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            timeout_seconds=self.timeout_seconds,
        )
        return (
            validate_draft(
                draft,
                document=document,
                evidence_registry=evidence_registry,
                image_registry=image_registry,
                question=question,
            ),
            evidence_registry,
            image_registry,
        )
