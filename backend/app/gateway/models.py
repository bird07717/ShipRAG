from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class ChatEngine(StrEnum):
    SUPPORT = "SUPPORT"
    RAG = "RAG"


class ExecutionPolicy(StrEnum):
    SUPPORT_ONLY = "SUPPORT_ONLY"
    RAG_ONLY = "RAG_ONLY"
    SUPPORT_WITH_RAG_FALLBACK = "SUPPORT_WITH_RAG_FALLBACK"


@dataclass(frozen=True, slots=True)
class ResolvedPolicy:
    knowledge_id: UUID
    policy: ExecutionPolicy
    config_version: int
    fallback_conditions: tuple[str, ...]
    frozen_engine: ChatEngine | None = None

    @property
    def primary_engine(self) -> ChatEngine:
        if self.frozen_engine is not None:
            return self.frozen_engine
        if self.policy == ExecutionPolicy.RAG_ONLY:
            return ChatEngine.RAG
        return ChatEngine.SUPPORT

    def allows_fallback(self, response_type: str) -> bool:
        return (
            self.frozen_engine is None
            and self.policy == ExecutionPolicy.SUPPORT_WITH_RAG_FALLBACK
            and response_type in self.fallback_conditions
        )
