from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import SecretStr


@dataclass(frozen=True, slots=True)
class ModelSnapshot:
    provider: str
    model_name: str
    parameters: dict[str, Any]
    base_url: str | None = None
    # Decrypted DB-stored API key; None means fall back to the env setting.
    api_key: SecretStr | None = None


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    vector_top_k: int = 10
    bm25_top_k: int = 10
    fusion_top_k: int = 20
    rerank_top_n: int = 10
    context_max_chunks: int = 8


@dataclass(frozen=True, slots=True)
class Turn:
    trace_id: UUID
    conversation_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID
    knowledge_id: UUID
    index_id: UUID
    embedding_model_name: str
    llm: ModelSnapshot
    history: list[dict[str, str]]
    history_diagnostics: dict[str, Any] = field(default_factory=dict)
    rerank: ModelSnapshot | None = None
    # Enabled EMBEDDING row snapshot at turn start.  Query-side embedding
    # prefers it when it matches the active index; None falls back to env.
    embedding: ModelSnapshot | None = None
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    knowledge_name: str = ""
    knowledge_description: str | None = None
    knowledge_updated_at: datetime | None = None
    scope_description: str = ""
    scope_sha256: str = ""
    scope_chars: int = 0
    scope_tokens: int = 0
    scope_truncated: bool = False


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    chunk_id: UUID
    document_id: UUID
    document: str
    chunk_type: str
    content: str
    token_count: int
    section_path: list[str]
    element_ids: list[UUID]
    distance: float | None
    similarity: float | None
    rank: int
    sequence_no: int | None = None
    parent_id: UUID | None = None
    previous_chunk_id: UUID | None = None
    next_chunk_id: UUID | None = None
    suspected_incomplete: bool = False
    incomplete_reasons: list[str] = field(default_factory=list)
    is_procedural: bool = False
    image_asset_ids: list[UUID] = field(default_factory=list)
    vector_rank: int | None = None
    bm25_rank: int | None = None
    bm25_score: float | None = None
    rrf_score: float | None = None
    rerank_rank: int | None = None
    rerank_score: float | None = None

    def trace_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": str(self.chunk_id),
            "document_id": str(self.document_id),
            "rank": self.rank,
            "sequence_no": self.sequence_no,
            "parent_id": str(self.parent_id) if self.parent_id else None,
            "previous_chunk_id": (str(self.previous_chunk_id) if self.previous_chunk_id else None),
            "next_chunk_id": str(self.next_chunk_id) if self.next_chunk_id else None,
            "suspected_incomplete": self.suspected_incomplete,
            "incomplete_reasons": self.incomplete_reasons,
            "is_procedural": self.is_procedural,
            "distance": self.distance,
            "similarity": self.similarity,
            "chunk_type": self.chunk_type,
            "image_asset_ids": [str(item) for item in self.image_asset_ids],
            "vector_rank": self.vector_rank,
            "bm25_rank": self.bm25_rank,
            "bm25_score": self.bm25_score,
            "rrf_score": self.rrf_score,
            "rerank_rank": self.rerank_rank,
            "rerank_score": self.rerank_score,
        }


@dataclass(frozen=True, slots=True)
class DocumentCardCandidate:
    card_id: UUID
    document_id: UUID
    document: str
    document_type: str
    summary: str
    tasks: list[str]
    review_status: str
    rank: int
    distance: float | None = None
    similarity: float | None = None
    bm25_score: float | None = None

    def trace_dict(self) -> dict[str, Any]:
        return {
            "card_id": str(self.card_id),
            "document_id": str(self.document_id),
            "document": self.document,
            "document_type": self.document_type,
            "summary": self.summary,
            "tasks": self.tasks,
            "review_status": self.review_status,
            "rank": self.rank,
            "distance": self.distance,
            "similarity": self.similarity,
            "bm25_score": self.bm25_score,
        }


@dataclass(frozen=True, slots=True)
class RerankDocument:
    text: str
    image_bytes: bytes | None = None
    image_mime_type: str | None = None


@dataclass(frozen=True, slots=True)
class RerankItem:
    index: int
    score: float


@dataclass(frozen=True, slots=True)
class RerankOutcome:
    items: list[RerankItem]
    provider: str
    model_name: str
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PreparedRag:
    turn: Turn
    timings: dict[str, int]
    started_at: float
    input_guard_reason: str | None = None
    policy_question: str = ""
