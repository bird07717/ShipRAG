from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ElementType = Literal["TEXT", "TABLE", "IMAGE"]
ChunkType = Literal["TEXT", "TABLE", "IMAGE", "MIXED"]


@dataclass(slots=True)
class ParsedElement:
    element_type: ElementType
    content: str
    section_path: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)
    image_bytes: bytes | None = None
    image_mime_type: str | None = None


@dataclass(slots=True)
class ParsedDocument:
    elements: list[ParsedElement]
    mammoth_warnings: list[str]


@dataclass(slots=True)
class ChunkDraft:
    chunk_type: ChunkType
    content: str
    search_text: str
    section_path: list[str]
    element_indexes: list[int]
    token_count: int
    metadata: dict[str, Any] = field(default_factory=dict)
    image_bytes: bytes | None = None
    image_mime_type: str | None = None
    parent_index: int | None = None
    previous_chunk_index: int | None = None
    next_chunk_index: int | None = None
    suspected_incomplete: bool = False
    incomplete_reasons: list[str] = field(default_factory=list)
    is_procedural: bool = False


@dataclass(slots=True)
class ParentChunkDraft:
    parent_type: Literal["SECTION", "SECTION_WINDOW"]
    content: str
    section_path: list[str]
    element_indexes: list[int]
    child_indexes: list[int]
    token_count: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EmbeddingInput:
    text: str
    image_bytes: bytes | None = None
    image_mime_type: str | None = None


@dataclass(frozen=True, slots=True)
class ImageUnderstandingResult:
    text: str
    provider: str
    model_name: str
    metadata: dict[str, Any] = field(default_factory=dict)
