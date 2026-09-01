from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5_000)


class KnowledgeBaseResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    status: Literal["ENABLED", "DISABLED", "DELETING"]
    # active_index_id is the rolling compatibility alias for RAG.
    active_index_id: UUID | None
    rag_active_index_id: UUID | None = None
    support_active_index_id: UUID | None = None
    support_active_release_id: UUID | None = None
    rebuild_required: bool
    runtime_state: str
    created_at: datetime
    updated_at: datetime
    document_count: int = 0
    active_chunk_count: int = 0
    building_index_id: UUID | None = None


class TaskSubmissionResponse(BaseModel):
    task_id: UUID
    task_type: str
    status: Literal["QUEUED", "RUNNING"]
    replayed: bool = False


class PublishedProfileSummary(BaseModel):
    """Live Profile fields shown in the document Profile & Alias tab."""

    id: UUID
    problem_code: str
    canonical_question: str
    aliases: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    product_name: str | None = None
    model_names: list[str] = Field(default_factory=list)
    task_type: str | None = None
    routing_summary: str = ""
    symptoms: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    enabled: bool = True
    profile_enrichment_version: int = 0
    updated_at: datetime | None = None


class CandidateProfileDraftSummary(BaseModel):
    """Unpublished candidate draft fields, explicitly flagged as not live."""

    id: UUID
    release_id: UUID
    index_id: UUID
    index_version: int
    release_status: str
    status: Literal["DRAFT", "APPROVED", "REJECTED", "PUBLISHED"]
    operation: Literal["CREATE", "UPDATE", "NONE"]
    target_profile_id: UUID | None = None
    problem_code: str
    canonical_question: str
    aliases: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    product_name: str | None = None
    model_names: list[str] = Field(default_factory=list)
    task_type: str | None = None
    routing_summary: str = ""
    enabled: bool = True
    quality_status: Literal["PASS", "REVIEW_REQUIRED", "FALLBACK", "MANUAL_REVIEWED"] = "PASS"
    quality_notes: list[str] = Field(default_factory=list)
    generation_source: Literal["LLM", "FALLBACK", "MANUAL"] | None = None
    updated_at: datetime | None = None


class DocumentProfileContextResponse(BaseModel):
    document_id: UUID
    published_profile: PublishedProfileSummary | None = None
    candidate_draft: CandidateProfileDraftSummary | None = None
    # Attribute-model fields.  Legacy names above remain optional during the
    # compatibility window so older admin clients keep rendering safely.
    profile_status: str = "NOT_GENERATED"
    active_profile: dict[str, Any] | None = None
    pending_profile: dict[str, Any] | None = None
    pending_state: str | None = None
    pending_error_code: str | None = None
    pending_error_message: str | None = None


class DocumentProfilePatchRequest(BaseModel):
    """The three user-maintained fields of a document Profile."""

    canonical_question: str = Field(min_length=1, max_length=500)
    aliases: list[str] = Field(default_factory=list, max_length=5)
    routing_summary: str = Field(default="", max_length=500)
    enabled: bool = True

    @field_validator("aliases")
    @classmethod
    def non_empty_items(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("列表项不能为空")
        return list(dict.fromkeys(item.strip() for item in value))


class DocumentProfileSummaryResponse(BaseModel):
    document_id: UUID
    document_name: str
    document_status: str
    profile_status: str
    active_profile: dict[str, Any] | None = None
    pending_profile: dict[str, Any] | None = None
    pending_state: str | None = None
    pending_error_code: str | None = None
    pending_error_message: str | None = None
    active_updated_at: datetime | None = None
    pending_updated_at: datetime | None = None


class KnowledgeProfileSummaryResponse(BaseModel):
    knowledge_id: UUID
    online_count: int = 0
    pending_count: int = 0
    attention_count: int = 0
    failed_count: int = 0
    generating_count: int = 0
    profiles: list[DocumentProfileSummaryResponse] = Field(default_factory=list)


class ProfilePublishCheckResponse(BaseModel):
    valid: bool
    publishable: bool
    pending_count: int = 0
    errors: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[dict[str, str]] = Field(default_factory=list)


class DocumentResponse(BaseModel):
    id: UUID
    knowledge_id: UUID
    filename: str
    display_name: str
    file_hash: str
    file_size: int
    status: str
    created_at: datetime
    updated_at: datetime


class BuildRequestResponse(BaseModel):
    requested: bool
    coalesced: bool
    index_id: UUID | None
    task_id: UUID | None
    rebuild_required: bool


class IndexBuildRequest(BaseModel):
    reason: Literal["MANUAL", "REPROCESS", "MODEL_CHANGED"] = "MANUAL"
    # All new builds are staged candidates.  Index/Profile publication is
    # owned by the knowledge-release transaction.
    activate_on_success: Literal[False] = False


class IndexBuildResponse(BaseModel):
    requested: bool
    coalesced: bool
    index_id: UUID | None
    task_id: UUID | None
    rebuild_required: bool


class GarbageCollectionResponse(BaseModel):
    deleted_index_ids: list[str]
    deleted_count: int
    skipped_index_ids: list[str] = []
    skipped_count: int = 0
    purged_conversation_ids: list[str] = []
    purged_conversation_count: int = 0
    released_conversation_count: int = 0


class DocumentUploadResponse(BaseModel):
    document: DocumentResponse
    build_request: BuildRequestResponse


class DocumentDeleteResponse(BaseModel):
    document_id: UUID
    deleted: bool
    build_request: BuildRequestResponse


class IndexResponse(BaseModel):
    id: UUID
    kb_id: UUID
    version: int
    status: str
    embedding_model_name: str
    embedding_dimension: int
    bm25_engine: str
    document_count: int
    element_count: int
    chunk_count: int
    build_reason: str
    activate_on_success: bool
    error_code: str | None
    error_message: str | None
    created_at: datetime
    finished_at: datetime | None
    activated_at: datetime | None


class TaskResponse(BaseModel):
    id: UUID
    task_type: str
    status: str
    stage: str
    progress: int
    attempt: int
    kb_id: UUID | None
    index_id: UUID | None
    document_id: UUID | None
    error_code: str | None
    error_message: str | None
    metadata: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


class ElementResponse(BaseModel):
    id: UUID
    index_id: UUID
    index_document_id: UUID
    document_id: UUID
    element_type: str
    sequence_no: int
    content: str
    section_path: list[str]
    metadata: dict[str, Any]
    image_asset_id: UUID | None
    image_bucket: str | None
    image_object_key: str | None
    image_mime_type: str | None
    created_at: datetime


class ImageAssetResponse(BaseModel):
    id: UUID
    index_id: UUID
    document_id: UUID
    element_id: UUID
    mime_type: str
    width: int | None
    height: int | None
    ocr_text: str | None
    vision_caption: str | None
    ocr_status: Literal["PENDING", "READY", "FAILED", "SKIPPED"]
    vision_status: Literal["PENDING", "READY", "FAILED", "SKIPPED"]
    ocr_provider: str | None
    ocr_model_name: str | None
    ocr_error_code: str | None
    vision_provider: str | None
    vision_model_name: str | None
    vision_error_code: str | None
    processed_at: datetime | None
    metadata: dict[str, Any]
    created_at: datetime


class ChunkResponse(BaseModel):
    id: UUID
    kb_id: UUID
    index_id: UUID
    index_document_id: UUID
    document_id: UUID
    chunk_type: str
    sequence_no: int
    content: str
    search_text: str
    token_count: int
    section_path: list[str]
    metadata: dict[str, Any]
    parent_id: UUID | None
    previous_chunk_id: UUID | None
    next_chunk_id: UUID | None
    suspected_incomplete: bool
    incomplete_reasons: list[str]
    is_procedural: bool
    embedding_ready: bool
    created_at: datetime


class ParentChunkResponse(BaseModel):
    id: UUID
    kb_id: UUID
    index_id: UUID
    index_document_id: UUID
    document_id: UUID
    parent_type: Literal["SECTION", "SECTION_WINDOW"]
    sequence_no: int
    content: str
    token_count: int
    section_path: list[str]
    metadata: dict[str, Any]
    child_chunk_ids: list[UUID]
    created_at: datetime


class IndexedItemsResponse(BaseModel):
    index_id: UUID
    items: list[dict[str, Any]]


class ChatStreamRequest(BaseModel):
    knowledge_id: UUID
    conversation_id: UUID | None = None
    question: str = Field(min_length=1, max_length=4_000)
    user_id: str | None = Field(default=None, min_length=1, max_length=100)
    problem_code: str | None = Field(default=None, min_length=1, max_length=100)
    document_id: UUID | None = None
    choice_id: str | None = Field(default=None, min_length=1, max_length=100)
    request_id: str | None = Field(default=None, min_length=1, max_length=200)
    include_images: bool = True


class WelcomeResponse(BaseModel):
    knowledge_base: dict[str, Any]
    message: str
    suggestions: list[str]


class ConversationListItem(BaseModel):
    id: UUID
    knowledge_id: UUID
    user_id: str | None = None
    title: str | None = None
    message_count: int
    last_execution_engine: str | None = None
    conversation_mode: str | None = None
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    items: list[ConversationListItem]


class ConversationDetailResponse(ConversationListItem):
    rag_index_id: UUID | None = None
    support_index_id: UUID | None = None
    focus_index_id: UUID | None = None


class ConversationDeleteResponse(BaseModel):
    conversation_id: UUID
    deleted: bool


class RuntimePolicyUpdate(BaseModel):
    execution_policy: Literal["RAG_ONLY", "SUPPORT_WITH_RAG_FALLBACK"]


class RuntimePolicyResponse(BaseModel):
    knowledge_id: UUID
    execution_policy: Literal["RAG_ONLY", "SUPPORT_WITH_RAG_FALLBACK"]
    config_version: int
    fallback_conditions: list[str]
    source: Literal["DATABASE", "DEFAULT"]
    updated_at: datetime | None = None
    readiness: dict[str, Any]


class PromptResponse(BaseModel):
    id: UUID
    name: str
    version: int
    content: str
    active: bool
    source: Literal["DATABASE", "RUNTIME"] = "DATABASE"
    description: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MessageResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    role: Literal["USER", "ASSISTANT"]
    content: str
    sources: list[dict[str, Any]]
    status: Literal["STREAMING", "COMPLETED", "FAILED", "CANCELLED"]
    content_blocks: list[dict[str, Any]] = []
    response_type: str | None = None
    support_mode: str | None = None
    created_at: datetime
    updated_at: datetime


class ConversationMessageListResponse(BaseModel):
    """Complete, ordered messages used by the administrative replay view."""

    items: list[MessageResponse]


class RagTraceResponse(BaseModel):
    id: UUID
    trace_id: UUID
    request_id: str
    source: Literal["ASSISTANT_API", "INTERNAL_CHAT", "PLAYGROUND"] = "INTERNAL_CHAT"
    mode: Literal["CHAT", "PLAYGROUND"]
    kb_id: UUID
    index_id: UUID
    conversation_id: UUID | None
    message_id: UUID | None
    question: str
    retrieval_result: dict[str, Any]
    rerank_result: dict[str, Any]
    selected_context: list[dict[str, Any]]
    prompt: str | None
    answer: str | None
    sources: list[dict[str, Any]]
    model_usage: dict[str, Any]
    runtime_snapshot: dict[str, Any] = Field(default_factory=dict)
    latency: dict[str, Any]
    citation_result: dict[str, Any]
    status: Literal["RUNNING", "COMPLETED", "FAILED", "CANCELLED"]
    user_id: str | None = None
    response_type: (
        Literal[
            "ANSWERED",
            "UNCONFIRMED",
            "OUT_OF_SCOPE",
            "DOC_DELIVERED",
            "DOCUMENT_DELIVERED",
            "CLARIFICATION_REQUIRED",
            "NEED_MORE_INFORMATION",
            "NO_MATCH",
            "ESCALATION_REQUIRED",
        ]
        | None
    ) = None
    answer_mode: (
        Literal[
            "PRODUCT_KNOWLEDGE",
            "PRODUCT_EXPLAINED",
            "PRODUCT_GENERAL",
            "UNCONFIRMED",
            "OUT_OF_SCOPE",
        ]
        | None
    ) = None
    error: dict[str, Any]
    created_at: datetime
    finished_at: datetime | None
    engine: Literal["SUPPORT", "RAG"] = "RAG"
    stage: str | None = None
    parent_trace_id: UUID | None = None
    fallback_from: Literal["SUPPORT", "RAG"] | None = None
    fallback_reason: str | None = None


class PlaygroundOptions(BaseModel):
    vector_top_k: int | None = Field(default=None, ge=1, le=100)
    bm25_top_k: int | None = Field(default=None, ge=1, le=100)
    fusion_top_k: int | None = Field(default=None, ge=1, le=100)
    rerank_top_n: int | None = Field(default=None, ge=1, le=100)
    context_max_chunks: int | None = Field(default=None, ge=1, le=100)
    include_prompt: bool = True


class PlaygroundRequest(BaseModel):
    knowledge_id: UUID
    conversation_id: UUID | None = None
    question: str = Field(min_length=1, max_length=4_000)
    options: PlaygroundOptions = Field(default_factory=PlaygroundOptions)


class TraceSummaryResponse(BaseModel):
    trace_id: UUID
    request_id: str
    source: Literal["ASSISTANT_API", "INTERNAL_CHAT", "PLAYGROUND"] = "INTERNAL_CHAT"
    mode: Literal["CHAT", "PLAYGROUND"]
    kb_id: UUID
    index_id: UUID
    question: str
    status: Literal["RUNNING", "COMPLETED", "FAILED", "CANCELLED"]
    user_id: str | None = None
    response_type: (
        Literal[
            "ANSWERED",
            "UNCONFIRMED",
            "OUT_OF_SCOPE",
            "DOC_DELIVERED",
            "DOCUMENT_DELIVERED",
            "CLARIFICATION_REQUIRED",
            "NEED_MORE_INFORMATION",
            "NO_MATCH",
            "ESCALATION_REQUIRED",
        ]
        | None
    ) = None
    answer_mode: (
        Literal[
            "PRODUCT_KNOWLEDGE",
            "PRODUCT_EXPLAINED",
            "PRODUCT_GENERAL",
            "UNCONFIRMED",
            "OUT_OF_SCOPE",
        ]
        | None
    ) = None
    latency: dict[str, Any]
    error: dict[str, Any]
    created_at: datetime
    finished_at: datetime | None
    engine: Literal["SUPPORT", "RAG"] = "RAG"
    stage: str | None = None
    parent_trace_id: UUID | None = None
    fallback_from: Literal["SUPPORT", "RAG"] | None = None
    fallback_reason: str | None = None


class MetricsRange(BaseModel):
    from_: str = Field(alias="from")
    to: str
    mode: str = "CHAT"

    model_config = {"populate_by_name": True}


class MetricsLatencySummary(BaseModel):
    p50_ms: int
    p95_ms: int
    p99_ms: int
    avg_ms: int


class MetricsOverviewResponse(BaseModel):
    range: MetricsRange
    total_traces: int
    completed: int
    failed: int
    cancelled: int
    response_type_counts: dict[str, int]
    classified_traces: int
    no_answer_rate: float
    doc_delivered_rate: float
    failure_rate: float
    citation_missing_rate: float
    rerank_degraded_rate: float
    unique_users: int
    latency: MetricsLatencySummary
    tokens: dict[str, int]


class MetricsTimeseriesPoint(BaseModel):
    bucket: str
    queries: int
    failed: int
    answered: int
    unconfirmed: int
    out_of_scope: int
    doc_delivered: int
    no_answer_rate: float
    p95_ms: int
    llm_tokens: int
    rerank_tokens: int


class MetricsLatencyResponse(BaseModel):
    range: MetricsRange
    total: MetricsLatencySummary
    stages: dict[str, int]


class SupportMetricsResponse(BaseModel):
    """Operational metrics calculated only from persisted Support traces.

    Metrics which require a labelled expected document (for example Hit@1 and
    document-switch accuracy) deliberately do not appear here.  They belong
    to the small developer acceptance suite rather than production telemetry.
    """

    range: MetricsRange
    support_turns: int
    first_turns: int
    followup_turns: int
    document_delivered: int
    clarification_required: int
    clarification_resolved: int
    no_match: int
    escalation_required: int
    canonical_or_alias_matches: int
    llm_catalog_matches: int
    general_guidance_turns: int
    evidence_missing: int
    high_risk_intercepts: int
    structured_output_retries: int
    workflow_conflicts: int
    model_calls: int
    document_delivery_rate: float
    clarification_rate: float
    clarification_resolution_rate: float
    escalation_rate: float
    general_guidance_rate: float
    workflow_conflict_rate: float
    first_turn_latency: MetricsLatencySummary
    followup_latency: MetricsLatencySummary
    tokens: dict[str, int]


class RequestErrorLogSummary(BaseModel):
    id: UUID
    request_id: str
    method: str
    path: str
    status_code: int
    error_code: str
    message: str
    created_at: datetime


class RequestErrorLogResponse(RequestErrorLogSummary):
    detail: dict[str, Any]


class RequestErrorLogListResponse(BaseModel):
    items: list[RequestErrorLogSummary]
    total: int
    limit: int
    offset: int


class TraceFailureGroup(BaseModel):
    error_code: str
    count: int
    last_seen: datetime


class RequestErrorGroup(BaseModel):
    path: str
    status_code: int
    count: int
    last_seen: datetime


class RequestErrorsByDay(BaseModel):
    day: str
    count: int


class FailureSummaryResponse(BaseModel):
    """Failure distribution for post-launch review: failed support turns
    grouped by error code, plus failed HTTP requests grouped by endpoint."""

    from_: datetime = Field(alias="from")
    to: datetime
    scope: dict[str, Any]
    trace_failures: list[TraceFailureGroup]
    request_errors: list[RequestErrorGroup]
    request_errors_by_day: list[RequestErrorsByDay]

    model_config = {"populate_by_name": True}


class ModelConfigResponse(BaseModel):
    id: UUID
    name: str
    model_type: Literal["LLM", "EMBEDDING", "RERANK", "OCR", "VISION"]
    provider: str
    base_url: str
    model_name: str
    parameters: dict[str, Any]
    enabled: bool
    api_key_configured: bool
    api_key_source: Literal["DATABASE", "ENV"] | None = None
    supported_providers: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    # Fleet rebuilds that were auto-triggered by an EMBEDDING identity
    # change (absent for every other update).
    rebuilds: list[dict[str, Any]] | None = None


class ApiKeyUpdate(BaseModel):
    api_key: str = Field(min_length=1, max_length=4_096)


class ModelProbeResponse(BaseModel):
    ok: bool
    model_type: Literal["LLM", "EMBEDDING", "RERANK", "OCR", "VISION"]
    provider: str
    model_name: str
    latency_ms: int
    detail: str
    dimension: int | None = None


def _validate_model_parameters(parameters: dict[str, Any]) -> None:
    """Validate the well-known model_config.parameters keys (best effort).

    Unknown keys pass through untouched; the knobs consumed at runtime are
    checked so admin typos cannot silently degrade chat quality.
    """
    temperature = parameters.get("temperature")
    if temperature is not None and (
        isinstance(temperature, bool)
        or not isinstance(temperature, int | float)
        or not 0 <= temperature <= 2
    ):
        raise ValueError("parameters.temperature 必须是 0 到 2 之间的数值")
    max_tokens = parameters.get("max_tokens")
    if max_tokens is not None and (
        isinstance(max_tokens, bool)
        or not isinstance(max_tokens, int)
        or not 100 <= max_tokens <= 100_000
    ):
        raise ValueError("parameters.max_tokens 必须是 100 到 100000 之间的整数")
    thinking = parameters.get("thinking")
    if thinking is not None and (
        not isinstance(thinking, dict) or thinking.get("type") not in ("enabled", "disabled")
    ):
        raise ValueError('parameters.thinking 必须是 {"type": "enabled" | "disabled"}')


# Typographic dash codepoints (U+2010..U+2015, U+2212, U+FF0D).  Model ids
# pasted from docs/slides often carry these; upstreams reject them outright
# ("modelCode: 不存在"), so they are normalized to the ASCII hyphen.
_MODEL_NAME_DASHES = str.maketrans({chr(codepoint): "-" for codepoint in range(0x2010, 0x2016)})
_MODEL_NAME_DASHES[chr(0x2212)] = "-"
_MODEL_NAME_DASHES[chr(0xFF0D)] = "-"


class ModelConfigUpdate(BaseModel):
    provider: str | None = Field(default=None, min_length=1, max_length=64)
    model_name: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, max_length=2_000)
    api_key: str | None = Field(default=None, min_length=1, max_length=4_096)
    parameters: dict[str, Any] | None = None
    enabled: bool | None = None
    # Changing the EMBEDDING identity (model_name / base_url) reshapes the
    # vector space and requires a full RAG rebuild of every knowledge base.
    # The flag is the admin's explicit acknowledgement of that cost.
    rebuild_all_confirmed: bool = False

    @field_validator("model_name")
    @classmethod
    def normalize_model_name(cls, value: str) -> str:
        return value.strip().translate(_MODEL_NAME_DASHES).replace(" ", "")

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str | None) -> str | None:
        return value.strip().lower() if value is not None else None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.startswith(("https://", "http://")) or not value.endswith("/")
        ):
            raise ValueError("base_url 必须以 http(s):// 开头并以 / 结尾")
        return value

    @model_validator(mode="after")
    def require_update_field(self) -> Self:
        if (
            self.model_name is None
            and self.provider is None
            and self.base_url is None
            and self.api_key is None
            and self.parameters is None
            and self.enabled is None
        ):
            raise ValueError("至少提供一个待更新字段")
        if self.parameters is not None:
            _validate_model_parameters(self.parameters)
        return self


class RagConfigResponse(BaseModel):
    vector_top_k: int
    bm25_top_k: int
    fusion_top_k: int
    rerank_top_n: int
    context_max_chunks: int
    updated_at: datetime | None = None


class RagConfigUpdate(BaseModel):
    vector_top_k: int | None = Field(default=None, ge=1, le=100)
    bm25_top_k: int | None = Field(default=None, ge=1, le=100)
    fusion_top_k: int | None = Field(default=None, ge=1, le=100)
    rerank_top_n: int | None = Field(default=None, ge=1, le=100)
    context_max_chunks: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def require_update_field(self) -> Self:
        if all(
            value is None
            for value in (
                self.vector_top_k,
                self.bm25_top_k,
                self.fusion_top_k,
                self.rerank_top_n,
                self.context_max_chunks,
            )
        ):
            raise ValueError("至少提供一个待更新字段")
        return self


class EngineRetrievalConfigResponse(RagConfigResponse):
    execution_policy: Literal["RAG_ONLY", "SUPPORT_WITH_RAG_FALLBACK"]


class EngineRetrievalConfigUpdate(RagConfigUpdate):
    pass


class ChatRequest(BaseModel):
    knowledge_id: UUID
    conversation_id: UUID | None = None
    question: str = Field(min_length=1, max_length=4_000)
    user_id: str | None = Field(default=None, min_length=1, max_length=100)
    problem_code: str | None = Field(default=None, min_length=1, max_length=100)
    document_id: UUID | None = None
    choice_id: str | None = Field(default=None, min_length=1, max_length=100)
    request_id: str | None = Field(default=None, min_length=1, max_length=200)
    include_images: bool = True


class ChatContentBlock(BaseModel):
    type: Literal["text", "table", "image"]
    sequence_no: int | None = None
    text: str | None = None
    source_id: str | None = None
    image_asset_id: str | None = None
    caption: str | None = None
    url: str | None = None
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None


class ChatReference(BaseModel):
    document_id: str
    title: str
    section_paths: list[list[str]] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    download_url: str


class ChatImage(BaseModel):
    image_asset_id: str
    sequence_no: int | None = None
    caption: str | None = None
    url: str
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None


class ChatDocument(BaseModel):
    id: str
    problem_code: str | None = None
    title: str
    download_url: str | None = None


class ChatRouting(BaseModel):
    model_config = ConfigDict(extra="allow")

    method: str | None = None
    confidence: float | None = None
    decision: str | None = None


class ChatSupportSection(BaseModel):
    type: Literal[
        "DOCUMENT_FACT",
        "DOCUMENT_EXPLANATION",
        "GENERAL_GUIDANCE",
        "UNVERIFIED_HYPOTHESIS",
        "NEED_INFORMATION",
        "WARNING",
    ]
    title: str
    content: str
    evidence_element_ids: list[str] = Field(default_factory=list)
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] | None = None
    disclaimer: str | None = None


class ChatChoice(BaseModel):
    choice_id: str
    document_id: str
    problem_code: str | None = None
    title: str


class ChatClarification(BaseModel):
    type: Literal["DOCUMENT_CHOICE", "CONFIRM_DOCUMENT_SWITCH", "NEED_MORE_INFORMATION"]
    message: str
    choices: list[ChatChoice] = Field(default_factory=list)


class ChatWorkflow(BaseModel):
    current_step: int | None = None
    completed_steps: list[int] = Field(default_factory=list)
    skipped_steps: list[int] = Field(default_factory=list)
    blocked_step: int | None = None
    observations: list[dict[str, str]] = Field(default_factory=list)
    collected_information: dict[str, str] = Field(default_factory=dict)
    missing_information: list[str] = Field(default_factory=list)
    pending_options: list[dict[str, Any]] = Field(default_factory=list)
    escalation: dict[str, Any] = Field(default_factory=dict)


class ChatEscalationSummary(BaseModel):
    problem_code: str | None = None
    current_step: int | None = None
    completed_steps: list[int] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    checks_performed: list[int] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)


class ChatEscalation(BaseModel):
    required: bool
    reason: str | None = None
    summary: ChatEscalationSummary | None = None
    trace_id: str | None = None


class ChatResponse(BaseModel):
    message_id: str
    conversation_id: str
    trace_id: str
    index_id: str | None = None
    response_type: Literal[
        "ANSWERED",
        "UNCONFIRMED",
        "OUT_OF_SCOPE",
        "DOC_DELIVERED",
        "DOCUMENT_DELIVERED",
        "CLARIFICATION_REQUIRED",
        "NEED_MORE_INFORMATION",
        "NO_MATCH",
        "ESCALATION_REQUIRED",
    ]
    answer_mode: (
        Literal[
            "PRODUCT_KNOWLEDGE",
            "PRODUCT_EXPLAINED",
            "PRODUCT_GENERAL",
            "UNCONFIRMED",
            "OUT_OF_SCOPE",
        ]
        | None
    ) = None
    disclaimer: str | None = None
    answer: str = ""
    support_mode: str | None = None
    sections: list[ChatSupportSection] = Field(default_factory=list)
    content: list[ChatContentBlock] = Field(default_factory=list)
    images: list[ChatImage] = Field(default_factory=list)
    document: ChatDocument | None = None
    routing: ChatRouting | None = None
    workflow: ChatWorkflow | None = None
    clarification: ChatClarification | None = None
    escalation: ChatEscalation | None = None
    references: list[ChatReference] = Field(default_factory=list)
    fallback: dict[str, Any] | None = None
    execution_engine: Literal["SUPPORT", "RAG"] | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int
    # Clickable retry questions for NO_MATCH routing turns; empty otherwise.
    next_suggestions: list[str] = Field(default_factory=list)


# External Assistant API ---------------------------------------------------
# Keep these separate from the internal/admin schemas.  In particular, the
# public request must reject model, engine, prompt, and policy override keys.


class AssistantStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssistantChatStreamRequest(AssistantStrictModel):
    knowledge_id: UUID
    conversation_id: UUID | None = None
    question: str = Field(min_length=1, max_length=4_000)
    choice_id: str | None = Field(default=None, min_length=1, max_length=100)
    include_images: bool = True


class AssistantKnowledgeBaseItem(BaseModel):
    knowledge_id: UUID
    name: str
    description: str | None = None
    ready: bool


class AssistantKnowledgeBaseListResponse(BaseModel):
    items: list[AssistantKnowledgeBaseItem]


class AssistantWelcomeResponse(BaseModel):
    knowledge_id: UUID
    message: str
    suggestions: list[str]


class AssistantConversationKnowledge(BaseModel):
    knowledge_id: UUID
    name: str


class AssistantConversationItem(BaseModel):
    conversation_id: UUID
    knowledge: AssistantConversationKnowledge
    title: str | None = None
    message_count: int
    created_at: datetime
    last_activity_at: datetime
    expires_at: datetime | None = None
    status: Literal["ACTIVE"] = "ACTIVE"


class AssistantConversationListResponse(BaseModel):
    items: list[AssistantConversationItem]
    next_cursor: str | None = None


class AssistantMessageError(BaseModel):
    code: str
    message: str
    retryable: bool | None = None


class AssistantMessageResponse(BaseModel):
    message_id: UUID
    conversation_id: UUID
    role: Literal["USER", "ASSISTANT"]
    status: Literal["GENERATING", "COMPLETED", "FAILED", "CANCELLED"]
    content: str
    trace_id: UUID | None = None
    response_type: str | None = None
    content_blocks: list[dict[str, Any]] = Field(default_factory=list)
    sections: list[dict[str, Any]] = Field(default_factory=list)
    document: dict[str, Any] | None = None
    references: list[dict[str, Any]] = Field(default_factory=list)
    clarification: dict[str, Any] | None = None
    escalation: dict[str, Any] | None = None
    disclaimer: str | None = None
    error: AssistantMessageError | None = None
    created_at: datetime
    updated_at: datetime


class AssistantMessageListResponse(BaseModel):
    items: list[AssistantMessageResponse]
    next_before: str | None = None


class ProblemProfileUpsert(BaseModel):
    document_id: UUID
    problem_code: str = Field(min_length=1, max_length=100)
    canonical_question: str = Field(min_length=1, max_length=500)
    aliases: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    product_name: str | None = Field(default=None, max_length=200)
    model_names: list[str] = Field(default_factory=list)
    task_type: str | None = Field(default=None, max_length=50)
    routing_summary: str = Field(default="", max_length=1_000)
    symptoms: list[str] = Field(default_factory=list, max_length=12)
    exclusions: list[str] = Field(default_factory=list, max_length=8)
    enabled: bool = True

    @field_validator("aliases", "keywords", "model_names", "symptoms", "exclusions")
    @classmethod
    def non_empty_string_items(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("列表项不能为空")
        return value


class ProblemProfileResponse(ProblemProfileUpsert):
    id: UUID
    knowledge_id: UUID
    profile_enrichment_version: int = Field(default=0, ge=0)
    support_ready: bool
    readiness_reasons: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
