from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.rag.models import ModelSnapshot


class SupportMode(StrEnum):
    DOCUMENT_EXPLANATION = "DOCUMENT_EXPLANATION"
    STEP_GUIDANCE = "STEP_GUIDANCE"
    GENERAL_OPERATION_GUIDANCE = "GENERAL_OPERATION_GUIDANCE"
    DIAGNOSTIC_ASSISTANCE = "DIAGNOSTIC_ASSISTANCE"
    NEED_MORE_INFORMATION = "NEED_MORE_INFORMATION"


class EvidenceType(StrEnum):
    DOCUMENT_FACT = "DOCUMENT_FACT"
    DOCUMENT_EXPLANATION = "DOCUMENT_EXPLANATION"
    GENERAL_GUIDANCE = "GENERAL_GUIDANCE"
    UNVERIFIED_HYPOTHESIS = "UNVERIFIED_HYPOTHESIS"
    NEED_INFORMATION = "NEED_INFORMATION"
    WARNING = "WARNING"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ProblemDecision(StrEnum):
    MATCHED = "MATCHED"
    AMBIGUOUS = "AMBIGUOUS"
    NO_MATCH = "NO_MATCH"


@dataclass(slots=True)
class ProblemProfile:
    id: UUID
    document_id: UUID
    knowledge_id: UUID
    problem_code: str
    canonical_question: str
    aliases: list[str]
    keywords: list[str]
    product_name: str | None = None
    model_names: list[str] = field(default_factory=list)
    task_type: str | None = None
    enabled: bool = True
    support_ready: bool = True
    readiness_reasons: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class ProblemResolution:
    decision: ProblemDecision
    method: str
    confidence: float
    profile: ProblemProfile | None = None
    choices: list[ProblemProfile] = field(default_factory=list)


@dataclass(slots=True)
class DocumentContentBlock:
    element_id: UUID
    sequence_no: int
    type: str
    text: str = ""
    image_asset_id: UUID | None = None
    caption: str | None = None
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    ocr_text: str = ""


@dataclass(slots=True)
class FocusedDocument:
    id: UUID
    title: str
    problem_code: str | None
    index_id: UUID
    blocks: list[DocumentContentBlock]


class SupportSection(BaseModel):
    type: EvidenceType
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=8_000)
    evidence_element_ids: list[str] = Field(default_factory=list)
    risk_level: RiskLevel | None = None
    disclaimer: str | None = None


class WorkflowUpdate(BaseModel):
    confirmed_completed_steps: list[int] = Field(default_factory=list)
    user_observations: list[str] = Field(default_factory=list)
    collected_information: dict[str, str] = Field(default_factory=dict)
    missing_information: list[str] = Field(default_factory=list)


class EscalationDraft(BaseModel):
    required: bool = False
    reason: str | None = None


class SupportDraft(BaseModel):
    mode: SupportMode
    answer: str = Field(min_length=1, max_length=12_000)
    sections: list[SupportSection] = Field(default_factory=list)
    image_ids: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    workflow_update: WorkflowUpdate = Field(default_factory=WorkflowUpdate)
    document_switch: dict[str, str] | None = None
    escalation: EscalationDraft = Field(default_factory=EscalationDraft)


@dataclass(slots=True)
class SupportTurn:
    trace_id: UUID
    conversation_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID
    knowledge_id: UUID
    index_id: UUID
    question: str
    focus_document_id: UUID | None
    chat_context: dict[str, Any]
    context_version: int
    history: list[dict[str, str]]
    idempotency_key: str | None = None
    llm: ModelSnapshot | None = None
    replay: dict[str, Any] | None = None


class WorkflowState(BaseModel):
    current_step: int | None = None
    completed_steps: list[int] = Field(default_factory=list)
    skipped_steps: list[int] = Field(default_factory=list)
    blocked_step: int | None = None
    observations: list[dict[str, str]] = Field(default_factory=list)
    collected_information: dict[str, str] = Field(default_factory=dict)
    missing_information: list[str] = Field(default_factory=list)
    pending_options: list[dict[str, Any]] = Field(default_factory=list)
    escalation: dict[str, Any] = Field(default_factory=lambda: {"required": False, "reason": None})
