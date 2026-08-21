from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5_000)


class KnowledgeBaseResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    status: Literal["ENABLED", "DISABLED"]
    active_index_id: UUID | None
    rebuild_required: bool
    runtime_state: str
    created_at: datetime
    updated_at: datetime
    document_count: int = 0
    active_chunk_count: int = 0
    building_index_id: UUID | None = None


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
    activate_on_success: bool = True


class IndexBuildResponse(BaseModel):
    requested: bool
    coalesced: bool
    index_id: UUID | None
    task_id: UUID | None
    rebuild_required: bool


class GarbageCollectionResponse(BaseModel):
    deleted_index_ids: list[str]
    deleted_count: int


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


class WelcomeResponse(BaseModel):
    knowledge_base: dict[str, Any]
    message: str
    suggestions: list[str]


class PromptResponse(BaseModel):
    id: UUID
    name: str
    version: int
    content: str
    active: bool
    created_at: datetime
    updated_at: datetime


class MessageResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    role: Literal["USER", "ASSISTANT"]
    content: str
    sources: list[dict[str, Any]]
    status: Literal["STREAMING", "COMPLETED", "FAILED", "CANCELLED"]
    created_at: datetime
    updated_at: datetime


class RagTraceResponse(BaseModel):
    id: UUID
    trace_id: UUID
    request_id: str
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
    latency: dict[str, Any]
    citation_result: dict[str, Any]
    status: Literal["RUNNING", "COMPLETED", "FAILED", "CANCELLED"]
    error: dict[str, Any]
    created_at: datetime
    finished_at: datetime | None


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
    mode: Literal["CHAT", "PLAYGROUND"]
    kb_id: UUID
    index_id: UUID
    question: str
    status: Literal["RUNNING", "COMPLETED", "FAILED", "CANCELLED"]
    latency: dict[str, Any]
    error: dict[str, Any]
    created_at: datetime
    finished_at: datetime | None


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
    created_at: datetime
    updated_at: datetime


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


class ModelConfigUpdate(BaseModel):
    model_name: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, max_length=2_000)
    parameters: dict[str, Any] | None = None
    enabled: bool | None = None

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
            and self.base_url is None
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


class ChatRequest(BaseModel):
    knowledge_id: UUID
    conversation_id: UUID | None = None
    question: str = Field(min_length=1, max_length=4_000)


class ChatContentBlock(BaseModel):
    type: Literal["text", "image"]
    text: str | None = None
    source_id: str | None = None
    image_asset_id: str | None = None


class ChatReference(BaseModel):
    document_id: str
    title: str
    section_paths: list[list[str]] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    download_url: str


class ChatResponse(BaseModel):
    message_id: str
    conversation_id: str
    trace_id: str
    response_type: Literal["ANSWERED", "UNCONFIRMED", "OUT_OF_SCOPE", "DOC_DELIVERED"]
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
    content: list[ChatContentBlock]
    references: list[ChatReference]
    usage: dict[str, Any]
    latency_ms: int
