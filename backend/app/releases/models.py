from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

ReleaseStatus = Literal[
    "DRAFT",
    "VALIDATING",
    "READY_TO_PUBLISH",
    "VALIDATION_FAILED",
    "PUBLISHED",
    "CANCELLED",
]
DraftOperation = Literal["CREATE", "UPDATE", "NONE"]
DraftStatus = Literal["DRAFT", "APPROVED", "REJECTED", "PUBLISHED"]


class GeneratedProfile(BaseModel):
    """Untrusted model output; publication still requires validation and review."""

    problem_code: str = Field(min_length=1, max_length=100)
    canonical_question: str = Field(min_length=1, max_length=500)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    keywords: list[str] = Field(default_factory=list, max_length=12)
    product_name: str | None = Field(default=None, max_length=200)
    model_names: list[str] = Field(default_factory=list, max_length=10)
    task_type: str | None = Field(default=None, max_length=50)
    confidence: float = Field(default=0.5, ge=0, le=1)
    reasoning: str | None = Field(default=None, max_length=1_000)

    @field_validator("aliases", "keywords", "model_names")
    @classmethod
    def clean_items(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            normalized = item.strip()
            if normalized and normalized not in cleaned:
                cleaned.append(normalized)
        return cleaned


class ProfileDraftPatch(BaseModel):
    operation: DraftOperation | None = None
    target_profile_id: UUID | None = None
    problem_code: str | None = Field(default=None, min_length=1, max_length=100)
    canonical_question: str | None = Field(default=None, min_length=1, max_length=500)
    aliases: list[str] | None = None
    keywords: list[str] | None = None
    product_name: str | None = Field(default=None, max_length=200)
    model_names: list[str] | None = None
    task_type: str | None = Field(default=None, max_length=50)
    enabled: bool | None = None
    status: Literal["DRAFT", "APPROVED", "REJECTED"] | None = None

    @field_validator("aliases", "keywords", "model_names")
    @classmethod
    def clean_optional_items(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned: list[str] = []
        for item in value:
            normalized = item.strip()
            if normalized and normalized not in cleaned:
                cleaned.append(normalized)
        return cleaned

    def changes(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True, mode="json")


class ValidationReport(BaseModel):
    valid: bool
    errors: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[dict[str, str]] = Field(default_factory=list)
    checked_draft_count: int = 0
