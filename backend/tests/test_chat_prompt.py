"""Tests for DOC_QA / ROUTING prompt templates and build_context image hints."""

from __future__ import annotations

from uuid import uuid4

from app.rag.models import RetrievalCandidate
from app.rag.prompt import (
    CHUNK_QA_PROMPT_TEMPLATE,
    DOC_QA_PROMPT_TEMPLATE,
    ROUTING_PROMPT_TEMPLATE,
    build_chunk_qa_prompt_template,
    build_context,
    build_doc_qa_prompt_template,
    build_routing_prompt_template,
    validate_prompt_template,
)


def test_doc_qa_template_has_required_variables() -> None:
    validate_prompt_template(DOC_QA_PROMPT_TEMPLATE)


def test_chunk_qa_template_has_required_variables() -> None:
    validate_prompt_template(CHUNK_QA_PROMPT_TEMPLATE)


def test_build_chunk_qa_template_substitutes_placeholders() -> None:
    result = build_chunk_qa_prompt_template("测试产品", "故障排查")

    assert "测试产品" in result
    assert "故障排查" in result
    assert "__PRODUCT_NAME__" not in result
    assert "__SCOPE_DESCRIPTION__" not in result


def test_chunk_qa_template_contains_all_mode_tags_and_citation_rules() -> None:
    assert "[MODE:PRODUCT_KNOWLEDGE]" in CHUNK_QA_PROMPT_TEMPLATE
    assert "[MODE:PRODUCT_EXPLAINED]" in CHUNK_QA_PROMPT_TEMPLATE
    assert "[MODE:PRODUCT_GENERAL]" in CHUNK_QA_PROMPT_TEMPLATE
    assert "[MODE:UNCONFIRMED]" in CHUNK_QA_PROMPT_TEMPLATE
    assert "[MODE:OUT_OF_SCOPE]" in CHUNK_QA_PROMPT_TEMPLATE
    assert "[IMG:S1]" in CHUNK_QA_PROMPT_TEMPLATE
    assert "资料片段" in CHUNK_QA_PROMPT_TEMPLATE


def test_routing_template_has_required_variables() -> None:
    validate_prompt_template(ROUTING_PROMPT_TEMPLATE)


def test_build_doc_qa_template_substitutes_placeholders() -> None:
    result = build_doc_qa_prompt_template("测试产品", "安装、配置、维护")

    assert "测试产品" in result
    assert "安装、配置、维护" in result
    assert "__PRODUCT_NAME__" not in result
    assert "__SCOPE_DESCRIPTION__" not in result


def test_build_routing_template_substitutes_placeholders() -> None:
    result = build_routing_prompt_template("测试产品", "故障排查")

    assert "测试产品" in result
    assert "故障排查" in result
    assert "__PRODUCT_NAME__" not in result
    assert "__SCOPE_DESCRIPTION__" not in result


def test_doc_qa_template_contains_all_mode_tags() -> None:
    assert "[MODE:PRODUCT_KNOWLEDGE]" in DOC_QA_PROMPT_TEMPLATE
    assert "[MODE:PRODUCT_EXPLAINED]" in DOC_QA_PROMPT_TEMPLATE
    assert "[MODE:PRODUCT_GENERAL]" in DOC_QA_PROMPT_TEMPLATE
    assert "[MODE:UNCONFIRMED]" in DOC_QA_PROMPT_TEMPLATE
    assert "[MODE:OUT_OF_SCOPE]" in DOC_QA_PROMPT_TEMPLATE


def test_doc_qa_template_bounds_product_facts_and_allows_general_skills() -> None:
    rules = DOC_QA_PROMPT_TEMPLATE.split("# 回答模式")[0]
    assert "只依据文档内容回答" in rules
    assert "不要猜测或虚构" in rules
    assert "通用计算机技能" in rules
    assert "[MODE:PRODUCT_GENERAL]" in rules
    assert "先说明“当前文档未涉及”" in rules
    assert "不得把补充内容表述为文档口径" in rules


def test_chunk_qa_template_bounds_product_facts_and_allows_general_skills() -> None:
    rules = CHUNK_QA_PROMPT_TEMPLATE.split("# 回答模式")[0]
    assert "只依据资料片段回答" in rules
    assert "不要猜测或虚构" in rules
    assert "通用计算机技能" in rules
    assert "[MODE:PRODUCT_GENERAL]" in rules
    assert "先说明“当前文档未涉及”" in rules
    assert "通用部分不需要标注依据编号" in rules


def test_doc_qa_template_normalizes_markdown_output_format() -> None:
    assert "**前置条件**" in DOC_QA_PROMPT_TEMPLATE
    assert "有序列表" in DOC_QA_PROMPT_TEMPLATE
    assert "不使用 # 标题" in DOC_QA_PROMPT_TEMPLATE
    assert "单独成段" in DOC_QA_PROMPT_TEMPLATE
    assert "建议联系相关技术支持人员" in DOC_QA_PROMPT_TEMPLATE


def test_chunk_qa_template_normalizes_markdown_output_format() -> None:
    assert "**前置条件**" in CHUNK_QA_PROMPT_TEMPLATE
    assert "有序列表" in CHUNK_QA_PROMPT_TEMPLATE
    assert "不使用 # 标题" in CHUNK_QA_PROMPT_TEMPLATE
    assert "单独成段" in CHUNK_QA_PROMPT_TEMPLATE
    assert "建议联系相关技术支持人员" in CHUNK_QA_PROMPT_TEMPLATE


def test_doc_qa_template_mandates_general_mode_for_generic_skills() -> None:
    assert "基于通用原理和排查经验给出可执行的做法" in DOC_QA_PROMPT_TEMPLATE
    assert "不得只回答“当前文档未涉及”" in DOC_QA_PROMPT_TEMPLATE
    assert "例 1" in DOC_QA_PROMPT_TEMPLATE
    assert "例 2" in DOC_QA_PROMPT_TEMPLATE
    assert "通讯灯闪烁过快" in DOC_QA_PROMPT_TEMPLATE
    assert "不得编造本产品的具体参数" in DOC_QA_PROMPT_TEMPLATE


def test_chunk_qa_template_mandates_general_mode_for_generic_skills() -> None:
    assert "基于通用原理和排查经验给出可执行的做法" in CHUNK_QA_PROMPT_TEMPLATE
    assert "不得只回答“当前文档未涉及”" in CHUNK_QA_PROMPT_TEMPLATE
    assert "例 1" in CHUNK_QA_PROMPT_TEMPLATE
    assert "例 2" in CHUNK_QA_PROMPT_TEMPLATE
    assert "通讯灯闪烁过快" in CHUNK_QA_PROMPT_TEMPLATE
    assert "不得编造本产品的具体参数" in CHUNK_QA_PROMPT_TEMPLATE


def test_doc_qa_template_enables_labeled_inference_for_explained_mode() -> None:
    assert "允许基于文档事实的有限推断" in DOC_QA_PROMPT_TEMPLATE
    assert "逆否" in DOC_QA_PROMPT_TEMPLATE
    assert "禁止引入文档之外假设的外推" in DOC_QA_PROMPT_TEMPLATE
    assert "按文档…推断" in DOC_QA_PROMPT_TEMPLATE
    assert "报警消除" in DOC_QA_PROMPT_TEMPLATE


def test_chunk_qa_template_enables_labeled_inference_for_explained_mode() -> None:
    assert "允许基于片段事实的有限推断" in CHUNK_QA_PROMPT_TEMPLATE
    assert "逆否" in CHUNK_QA_PROMPT_TEMPLATE
    assert "禁止引入片段之外假设的外推" in CHUNK_QA_PROMPT_TEMPLATE
    assert "按文档…推断" in CHUNK_QA_PROMPT_TEMPLATE
    assert "报警消除" in CHUNK_QA_PROMPT_TEMPLATE


def test_doc_qa_template_contains_image_instructions() -> None:
    assert "[IMG:S1]" in DOC_QA_PROMPT_TEMPLATE
    assert "文档内容" in DOC_QA_PROMPT_TEMPLATE


def test_routing_template_has_no_mode_tags() -> None:
    assert "[MODE:" not in ROUTING_PROMPT_TEMPLATE
    assert "文档信息" in ROUTING_PROMPT_TEMPLATE
    assert "候选文档" in ROUTING_PROMPT_TEMPLATE


def test_build_context_adds_image_hint_when_chunk_has_images() -> None:
    candidate = RetrievalCandidate(
        chunk_id=uuid4(),
        document_id=uuid4(),
        document="设备手册.docx",
        chunk_type="MIXED",
        content="按下RESET按钮。",
        token_count=10,
        section_path=["维护"],
        element_ids=[uuid4()],
        distance=0.1,
        similarity=0.9,
        rank=1,
        image_asset_ids=[uuid4(), uuid4()],
    )

    context, sources = build_context([candidate])

    assert "可用图片" in context
    assert "[IMG:S1]" in context
    assert "共2张" in context
    assert len(sources[0].image_asset_ids) == 2


def test_build_context_no_image_hint_when_no_images() -> None:
    candidate = RetrievalCandidate(
        chunk_id=uuid4(),
        document_id=uuid4(),
        document="设备手册.docx",
        chunk_type="TEXT",
        content="纯文本内容。",
        token_count=10,
        section_path=["配置"],
        element_ids=[uuid4()],
        distance=0.1,
        similarity=0.9,
        rank=1,
        image_asset_ids=[],
    )

    context, sources = build_context([candidate])

    assert "可用图片" not in context
    assert sources[0].image_asset_ids == []
