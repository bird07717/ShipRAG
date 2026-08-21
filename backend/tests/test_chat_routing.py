"""Tests for routing post_validate and rich content assembly."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.rag.routing import (
    OUT_OF_SCOPE_MESSAGE,
    PRODUCT_GENERAL_DISCLAIMER,
    UNCONFIRMED_MESSAGE,
    AnswerMode,
    ContentBlock,
    ResponseType,
    post_validate,
)


def _sources(
    count: int = 1,
    with_images: bool = False,
    image_count: int = 1,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for i in range(1, count + 1):
        images: list[str] = []
        if with_images:
            images = [str(uuid4()) for _ in range(image_count)]
        result.append(
            {
                "source_id": f"S{i}",
                "document_id": str(uuid4()),
                "document": f"doc{i}.docx",
                "section_path": [f"section{i}"],
                "page": None,
                "element_ids": [str(uuid4())],
                "chunk_id": str(uuid4()),
                "image_asset_ids": images,
            }
        )
    return result


# --- mode parsing ---


def test_product_knowledge_with_citations_and_images() -> None:
    sources = _sources(1, with_images=True, image_count=2)
    raw = "[MODE:PRODUCT_KNOWLEDGE]\n设备端口是3306。[S1][IMG:S1]"
    result = post_validate(raw, sources, has_evidence=True)

    assert result.response_type == ResponseType.ANSWERED
    assert result.answer_mode == AnswerMode.PRODUCT_KNOWLEDGE
    assert result.disclaimer is None
    assert len(result.valid_sources) == 1
    # text block + 2 image blocks
    blocks = result.content
    assert blocks[0].type == "text"
    assert "3306" in blocks[0].text
    assert blocks[1].type == "image"
    assert blocks[1].source_id == "S1"
    assert blocks[2].type == "image"
    assert blocks[2].source_id == "S1"


def test_product_explained_with_citations() -> None:
    sources = _sources(1)
    raw = "[MODE:PRODUCT_EXPLAINED]\n这个参数表示数据库端口。[S1]"
    result = post_validate(raw, sources, has_evidence=True)

    assert result.response_type == ResponseType.ANSWERED
    assert result.answer_mode == AnswerMode.PRODUCT_EXPLAINED
    assert len(result.valid_sources) == 1


def test_product_general_strips_citations_and_adds_disclaimer() -> None:
    sources = _sources(1)
    raw = "[MODE:PRODUCT_GENERAL]\nDHCP是动态主机配置协议。[S1]"
    result = post_validate(raw, sources, has_evidence=True)

    assert result.response_type == ResponseType.ANSWERED
    assert result.answer_mode == AnswerMode.PRODUCT_GENERAL
    assert result.disclaimer == PRODUCT_GENERAL_DISCLAIMER
    assert result.valid_sources == []
    assert "[S1]" not in result.answer
    # Only text blocks, no images
    assert all(b.type == "text" for b in result.content)


def test_out_of_scope_returns_fixed_message() -> None:
    raw = "[MODE:OUT_OF_SCOPE]\n今天天气怎么样"
    result = post_validate(raw, _sources(0), has_evidence=False)

    assert result.response_type == ResponseType.OUT_OF_SCOPE
    assert result.answer_mode is None
    assert result.answer == OUT_OF_SCOPE_MESSAGE
    assert result.valid_sources == []
    assert result.valid_sources == []


def test_unconfirmed_no_evidence_returns_fixed_message() -> None:
    raw = "[MODE:UNCONFIRMED]\nsome text"
    result = post_validate(raw, _sources(0), has_evidence=False)

    assert result.response_type == ResponseType.UNCONFIRMED
    assert result.answer_mode is None
    assert result.answer == UNCONFIRMED_MESSAGE
    assert result.valid_sources == []


# --- hard rules ---


def test_no_evidence_with_product_knowledge_downgrades_to_unconfirmed() -> None:
    raw = "[MODE:PRODUCT_KNOWLEDGE]\n设备端口是3306。[S1]"
    result = post_validate(raw, _sources(1), has_evidence=False)

    assert result.response_type == ResponseType.UNCONFIRMED
    assert result.answer_mode is None


def test_no_citations_with_product_knowledge_downgrades_to_unconfirmed() -> None:
    raw = "[MODE:PRODUCT_KNOWLEDGE]\n设备端口是3306。"
    result = post_validate(raw, _sources(1), has_evidence=True)

    assert result.response_type == ResponseType.UNCONFIRMED


def test_invalid_citation_recorded() -> None:
    raw = "[MODE:PRODUCT_KNOWLEDGE]\n答案来自[S99]和[S1]。"
    result = post_validate(raw, _sources(1), has_evidence=True)

    assert result.response_type == ResponseType.ANSWERED
    assert "S99" in result.citation_result["invalid_source_ids"]
    assert "S1" in result.citation_result["valid_source_ids"]


# --- content block assembly ---


def test_multiple_images_split_text() -> None:
    sources = _sources(1, with_images=True, image_count=2)
    raw = "[MODE:PRODUCT_KNOWLEDGE]\n步骤一。[S1][IMG:S1]\n步骤二。[S1][IMG:S1]"
    result = post_validate(raw, sources, has_evidence=True)

    types = [b.type for b in result.content]
    assert types == ["text", "image", "image", "text", "image", "image"]


def test_image_from_different_sources() -> None:
    sources = _sources(2, with_images=True, image_count=1)
    raw = "[MODE:PRODUCT_KNOWLEDGE]\n前半部分[S1][IMG:S1]后半部分[S2][IMG:S2]"
    result = post_validate(raw, sources, has_evidence=True)

    image_blocks = [b for b in result.content if b.type == "image"]
    assert len(image_blocks) == 2
    assert image_blocks[0].source_id == "S1"
    assert image_blocks[1].source_id == "S2"


def test_no_mode_tag_defaults_to_unconfirmed() -> None:
    raw = "some answer without mode tag"
    result = post_validate(raw, _sources(1), has_evidence=True)

    assert result.response_type == ResponseType.UNCONFIRMED


def test_unknown_mode_tag_defaults_to_unconfirmed() -> None:
    raw = "[MODE:UNKNOWN_MODE]\nanswer"
    result = post_validate(raw, _sources(1), has_evidence=True)

    assert result.response_type == ResponseType.UNCONFIRMED


def test_empty_answer_produces_single_text_block() -> None:
    raw = "[MODE:UNCONFIRMED]\n"
    result = post_validate(raw, _sources(0), has_evidence=False)

    assert len(result.content) == 1
    assert result.content[0].type == "text"


def test_citation_result_structure() -> None:
    sources = _sources(2)
    raw = "[MODE:PRODUCT_KNOWLEDGE]\n引用[S1]和[S2]。"
    result = post_validate(raw, sources, has_evidence=True)

    assert result.citation_result["registered_source_count"] == 2
    assert result.citation_result["citation_missing"] is False
    assert set(result.citation_result["valid_source_ids"]) == {"S1", "S2"}
    assert result.citation_result["invalid_source_ids"] == []


def test_content_block_to_dict() -> None:
    block = ContentBlock(type="text", text="hello")
    assert block.to_dict() == {"type": "text", "text": "hello"}

    block2 = ContentBlock(type="image", source_id="S1", image_asset_id="uuid-1")
    assert block2.to_dict() == {
        "type": "image",
        "source_id": "S1",
        "image_asset_id": "uuid-1",
    }
