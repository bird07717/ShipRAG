from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


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
    DOC_DELIVERED = "DOC_DELIVERED"


PRODUCT_GENERAL_DISCLAIMER = (
    "该回答用于解释与本产品相关的通用原理，"
    "不代表当前产品型号的具体参数、功能、步骤、兼容性或官方操作要求。"
)

OUT_OF_SCOPE_MESSAGE = (
    "我只能回答与本产品、产品使用及相关原理有关的问题。请换一个与产品相关的问题。"
)

UNCONFIRMED_MESSAGE = (
    "当前知识库没有足够的可靠信息，无法确认该问题。请补充对应产品、型号或操作资料后重试。"
)

_MODE_PATTERN = re.compile(r"^\[MODE:(\w+)\]\s*", re.MULTILINE)
_IMG_PATTERN = re.compile(r"\[IMG:(S\d+)\]")
_CITATION_PATTERN = re.compile(r"\[S([1-9][0-9]*)\]")


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


def _parse_mode_tag(raw_answer: str) -> tuple[AnswerMode | None, str]:
    match = _MODE_PATTERN.match(raw_answer)
    if not match:
        return None, raw_answer
    try:
        return AnswerMode(match.group(1)), raw_answer[match.end() :]
    except ValueError:
        return None, raw_answer


def _extract_citations(
    answer: str, source_registry: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    valid: list[dict[str, Any]] = []
    seen: set[str] = set()
    invalid: list[str] = []
    for match in _CITATION_PATTERN.finditer(answer):
        sid = f"S{match.group(1)}"
        if sid not in source_registry:
            if sid not in invalid:
                invalid.append(sid)
            continue
        if sid not in seen:
            seen.add(sid)
            valid.append(source_registry[sid])
    return valid, invalid


def _strip_citations(text: str) -> str:
    cleaned = _CITATION_PATTERN.sub("", text)
    cleaned = re.sub(r"\s+(?=[。，、；：！？])", "", cleaned)
    return cleaned.strip()


def _move_trailing_punctuation(answer: str) -> str:
    punct = "。，、；：！？.,;:!?"
    return re.sub(
        r"(\[IMG:S\d+\])([" + punct + r"]+)",
        r"",
        answer,
    )


def _build_content_blocks(answer: str, valid_sources: list[dict[str, Any]]) -> list[ContentBlock]:
    source_map = {s["source_id"]: s for s in valid_sources}
    answer = _move_trailing_punctuation(answer)
    answer = _strip_citations(answer)
    blocks: list[ContentBlock] = []
    last_end = 0

    for match in _IMG_PATTERN.finditer(answer):
        text_before = answer[last_end : match.start()].rstrip()
        if text_before:
            blocks.append(ContentBlock(type="text", text=text_before))

        source_id = match.group(1)
        source = source_map.get(source_id)
        if source and source.get("image_asset_ids"):
            for asset_id in source["image_asset_ids"]:
                blocks.append(
                    ContentBlock(
                        type="image",
                        source_id=source_id,
                        image_asset_id=str(asset_id),
                    )
                )
        last_end = match.end()

    remaining = answer[last_end:].rstrip()
    if remaining:
        blocks.append(ContentBlock(type="text", text=remaining))

    if not blocks:
        blocks.append(ContentBlock(type="text", text=answer))
    return blocks


def build_assist_result(answer: str) -> ChatResult:
    """Build a plain ANSWERED result for routing-phase LLM replies.

    Routing answers (clarification, catalog guidance, switch offers) carry no
    citations and no mode tag; they are surfaced verbatim.
    """
    answer = answer.strip()
    return ChatResult(
        response_type=ResponseType.ANSWERED,
        answer_mode=None,
        answer=answer,
        content=[ContentBlock(type="text", text=answer)],
        disclaimer=None,
        valid_sources=[],
        citation_result={
            "citation_missing": False,
            "valid_source_ids": [],
            "invalid_source_ids": [],
            "registered_source_count": 0,
            "routing_assist": True,
        },
    )


def build_delivery_result(
    *, summary: str, blocks: list[ContentBlock], document: dict[str, Any]
) -> ChatResult:
    """Build the DOC_DELIVERED result for zero-LLM document delivery."""
    return ChatResult(
        response_type=ResponseType.DOC_DELIVERED,
        answer_mode=None,
        answer=summary,
        content=blocks or [ContentBlock(type="text", text=summary)],
        disclaimer=None,
        valid_sources=[document],
        citation_result={
            "citation_missing": False,
            "valid_source_ids": [document["source_id"]],
            "invalid_source_ids": [],
            "registered_source_count": 1,
            "document_level": True,
        },
    )


def post_validate(
    raw_answer: str,
    sources: list[dict[str, Any]],
    has_evidence: bool,
    *,
    require_citations: bool = True,
) -> ChatResult:
    source_registry = {s["source_id"]: s for s in sources}

    mode, answer = _parse_mode_tag(raw_answer)
    if mode is None:
        mode = AnswerMode.UNCONFIRMED

    valid_sources, invalid_ids = _extract_citations(answer, source_registry)
    cited_ids = [s["source_id"] for s in valid_sources]

    if not require_citations:
        # Document-level mode: every registered source backs the answer, so
        # [IMG:Sn] markers resolve even when the model cites nothing.
        seen = {s["source_id"] for s in valid_sources}
        for source in sources:
            if source["source_id"] not in seen:
                seen.add(source["source_id"])
                valid_sources.append(source)

    if not has_evidence and mode in (AnswerMode.PRODUCT_KNOWLEDGE, AnswerMode.PRODUCT_EXPLAINED):
        mode = AnswerMode.UNCONFIRMED

    if (
        require_citations
        and mode in (AnswerMode.PRODUCT_KNOWLEDGE, AnswerMode.PRODUCT_EXPLAINED)
        and not valid_sources
    ):
        mode = AnswerMode.UNCONFIRMED

    if mode == AnswerMode.PRODUCT_GENERAL and valid_sources:
        answer = _CITATION_PATTERN.sub("", answer)
        valid_sources = []

    disclaimer: str | None = None
    if mode == AnswerMode.OUT_OF_SCOPE:
        answer = OUT_OF_SCOPE_MESSAGE
        valid_sources = []
    elif mode == AnswerMode.UNCONFIRMED:
        if not has_evidence:
            answer = UNCONFIRMED_MESSAGE
            valid_sources = []
    elif mode == AnswerMode.PRODUCT_GENERAL:
        disclaimer = PRODUCT_GENERAL_DISCLAIMER

    if mode in (AnswerMode.OUT_OF_SCOPE, AnswerMode.UNCONFIRMED) and not valid_sources:
        content = [ContentBlock(type="text", text=answer)]
    elif mode == AnswerMode.PRODUCT_GENERAL:
        content = [ContentBlock(type="text", text=answer)]
    else:
        content = _build_content_blocks(answer, valid_sources)

    response_type = {
        AnswerMode.OUT_OF_SCOPE: ResponseType.OUT_OF_SCOPE,
        AnswerMode.UNCONFIRMED: ResponseType.UNCONFIRMED,
    }.get(mode, ResponseType.ANSWERED)

    citation_result = {
        "citation_missing": (
            len(cited_ids) == 0 and response_type == ResponseType.ANSWERED and require_citations
        ),
        "valid_source_ids": cited_ids,
        "invalid_source_ids": invalid_ids,
        "registered_source_count": len(sources),
    }

    return ChatResult(
        response_type=response_type,
        answer_mode=mode if response_type == ResponseType.ANSWERED else None,
        answer=answer,
        content=content,
        disclaimer=disclaimer,
        valid_sources=valid_sources,
        citation_result=citation_result,
    )
