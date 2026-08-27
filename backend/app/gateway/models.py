from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class ChatEngine(StrEnum):
    SUPPORT = "SUPPORT"
    RAG = "RAG"


class ExecutionPolicy(StrEnum):
    RAG_ONLY = "RAG_ONLY"
    SUPPORT_WITH_RAG_FALLBACK = "SUPPORT_WITH_RAG_FALLBACK"


class ConversationMode(StrEnum):
    """Routing state for a conversation; it is deliberately not an engine lock."""

    SUPPORT_DISCOVERY = "SUPPORT_DISCOVERY"
    FOCUSED_SUPPORT = "FOCUSED_SUPPORT"
    RAG_ONLY = "RAG_ONLY"


@dataclass(frozen=True, slots=True)
class ResolvedPolicy:
    knowledge_id: UUID
    policy: ExecutionPolicy
    config_version: int
    fallback_conditions: tuple[str, ...]
    conversation_mode: ConversationMode | None = None

    @property
    def primary_engine(self) -> ChatEngine:
        if self.policy == ExecutionPolicy.RAG_ONLY or (
            self.conversation_mode == ConversationMode.RAG_ONLY
        ):
            return ChatEngine.RAG
        return ChatEngine.SUPPORT

    def allows_fallback(self, response_type: str) -> bool:
        return (
            self.policy == ExecutionPolicy.SUPPORT_WITH_RAG_FALLBACK
            and self.conversation_mode != ConversationMode.FOCUSED_SUPPORT
            and response_type in self.fallback_conditions
        )
