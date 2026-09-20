from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.rag.presentation import clean_model_text


class AnswerMode(str, Enum):
    PRODUCT_KNOWLEDGE = "PRODUCT_KNOWLEDGE"
    PRODUCT_EXPLAINED = "PRODUCT_EXPLAINED"
    PRODUCT_GENERAL = "PRODUCT_GENERAL"
    UNCONFIRMED = "UNCONFIRMED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class ResponseType(str, Enum):
    ANSWERED = "ANSWERED"
    UNCONFIRMED = "UNCONFIRMED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    # Kept for historical message/Trace decoding. RAG V2 never emits it.
    DOC_DELIVERED = "DOC_DELIVERED"


class TraceDiagnosticReason(str, Enum):
    """Internal-only deterministic reasons for non-standard RAG outcomes."""

    LOW_INFORMATION = "LOW_INFORMATION"
    SCOPE_CONFLICT = "SCOPE_CONFLICT"
    NEEDS_KEY_CONDITION = "NEEDS_KEY_CONDITION"


TECHNICAL_DISCLAIMER = "本回答仅供参考，请结合现场情况和正式资料核验，必要时联系海兰信专业人员。"
OUT_OF_SCOPE_MESSAGE = (
    "我只能回答与本产品、产品使用及相关原理有关的问题。请换一个与产品相关的问题。"
)


@dataclass(frozen=True, slots=True)
class ContentBlock:
    type: str
    text: str | None = None
    source_id: str | None = None
    image_asset_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"type": self.type}
        if self.text is not None:
            result["text"] = self.text
        if self.source_id is not None:
            result["source_id"] = self.source_id
        if self.image_asset_id is not None:
            result["image_asset_id"] = self.image_asset_id
        return result


@dataclass(frozen=True, slots=True)
class ChatResult:
    response_type: ResponseType
    answer_mode: AnswerMode | None
    answer: str
    content: list[ContentBlock]
    disclaimer: str | None
    valid_sources: list[dict[str, Any]]
    citation_result: dict[str, Any]


def build_plain_answer(answer: str) -> ChatResult:
    answer = clean_model_text(answer)
    return ChatResult(
        response_type=ResponseType.ANSWERED,
        answer_mode=None,
        answer=answer,
        content=[ContentBlock(type="text", text=answer)],
        disclaimer=TECHNICAL_DISCLAIMER,
        valid_sources=[],
        citation_result={"status": "NOT_REQUIRED", "pipeline": "V2"},
    )
