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
DraftOperation = Literal["CREATE", "UPDATE", "REMOVE", "NONE"]
DraftStatus = Literal["DRAFT", "APPROVED", "REJECTED", "PUBLISHED"]
PROFILE_ENRICHMENT_VERSION = 1
ProfileQualityStatus = Literal["PASS", "REVIEW_REQUIRED", "FALLBACK", "MANUAL_REVIEWED"]


class ProfileEvidence(BaseModel):
    field: str = Field(min_length=1, max_length=80)
    excerpt: str = Field(min_length=1, max_length=1_000)
    element_hint: str | None = Field(default=None, max_length=120)


class ProfileScope(BaseModel):
    """Document-grounded constraints for a generated Profile."""

    core_subject: str = Field(min_length=1, max_length=300)
    task: str | None = Field(default=None, max_length=300)
    symptoms: list[str] = Field(default_factory=list, max_length=12)
    exclusions: list[str] = Field(default_factory=list, max_length=8)
    evidence: list[ProfileEvidence] = Field(default_factory=list, max_length=12)


class GeneratedProfile(BaseModel):
    """Untrusted model output; publication still requires validation and review."""

    problem_code: str = Field(min_length=1, max_length=100)
    canonical_question: str = Field(min_length=1, max_length=500)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    keywords: list[str] = Field(default_factory=list, max_length=12)
    product_name: str | None = Field(default=None, max_length=200)
    model_names: list[str] = Field(default_factory=list, max_length=10)
    task_type: str | None = Field(default=None, max_length=50)
    routing_summary: str = Field(default="", max_length=1_000)
    symptoms: list[str] = Field(default_factory=list, max_length=12)
    exclusions: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(default=0.5, ge=0, le=1)
    reasoning: str | None = Field(default=None, max_length=1_000)

    @field_validator("aliases", "keywords", "model_names", "symptoms", "exclusions")
    @classmethod
    def clean_items(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            normalized = item.strip()
            if normalized and normalized not in cleaned:
                cleaned.append(normalized)
        return cleaned


class ProfileQualityReview(BaseModel):
    """Second offline LLM pass.  Its output remains a reviewable draft."""

    profile: GeneratedProfile
    quality_status: Literal["PASS", "REVIEW_REQUIRED"]
    quality_notes: list[str] = Field(default_factory=list, max_length=12)
    evidence: list[ProfileEvidence] = Field(default_factory=list, max_length=16)


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
    routing_summary: str | None = Field(default=None, max_length=1_000)
    symptoms: list[str] | None = None
    exclusions: list[str] | None = None
    enabled: bool | None = None
    status: Literal["DRAFT", "APPROVED", "REJECTED"] | None = None

    @field_validator("aliases", "keywords", "model_names", "symptoms", "exclusions")
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
    publishable: bool = False
    errors: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[dict[str, str]] = Field(default_factory=list)
    review_required_count: int = 0
    review_required: list[dict[str, str]] = Field(default_factory=list)
    checked_draft_count: int = 0


class RagIndexRollbackRequest(BaseModel):
    """Explicit RAG rollback target.  The version is never guessed."""

    target_index_id: UUID


class SupportReleaseRollbackRequest(BaseModel):
    """Explicit Support rollback target.  The version is never guessed."""

    target_release_id: UUID


class ReleaseRebaseReport(BaseModel):
    source_release_id: UUID
    target_release_id: UUID
    target_index_id: UUID
    target_index_version: int
    reused_draft_count: int = 0
    review_required_draft_count: int = 0
    regenerate_document_count: int = 0
    remove_suggestion_count: int = 0
    create_suggestion_count: int = 0
    backfill_submitted: bool = False
