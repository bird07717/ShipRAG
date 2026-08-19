from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


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
