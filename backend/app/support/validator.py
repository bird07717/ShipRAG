from __future__ import annotations

import re
from typing import Any

from app.common.errors import ApiError
from app.support.models import EvidenceType, FocusedDocument, RiskLevel, SupportDraft
from app.support.policy import GENERAL_DISCLAIMER, classify_risk

_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def _rejected_draft_summary(draft: SupportDraft) -> dict[str, Any]:
    """Bounded, content-free summary of the draft the model produced, so a
    reviewer can see what was attempted without a content dump in the trace."""
    return {
        "mode": draft.mode,
        "sections": [
            {
                "title": section.title,
                "type": section.type,
                "evidence_count": len(section.evidence_element_ids),
            }
            for section in draft.sections
        ],
        "image_ids": list(draft.image_ids),
        "workflow_update": draft.workflow_update.model_dump(mode="json"),
        "escalation_required": draft.escalation.required,
    }


def validate_draft(
    draft: SupportDraft,
    *,
    document: FocusedDocument,
    evidence_registry: dict[str, str],
    image_registry: dict[str, str],
    question: str,
) -> SupportDraft:
    # The prompt exposes stable, model-safe short IDs (E1, E2, ...), while
    # persistence uses the underlying element UUIDs.  Validate against the
    # public IDs first, then replace them before the response is persisted.
    valid_elements = set(evidence_registry)
    valid_images = set(image_registry)
    # Facts often exist only inside screenshots: image captions and OCR text
    # are document evidence too, so they must join the IP allow-list.
    text_parts: list[str] = []
    for block in document.blocks:
        text_parts.append(block.text)
        if block.type == "image":
            if block.caption:
                text_parts.append(block.caption)
            if block.ocr_text:
                text_parts.append(block.ocr_text)
    document_text = "\n".join(text_parts)
    for section in draft.sections:
        evidence = section.evidence_element_ids
        invalid = [item for item in evidence if item not in valid_elements]
        if invalid:
            raise ApiError(
                "INVALID_EVIDENCE",
                "模型引用了当前文档之外的证据",
                502,
                details={
                    "section_title": section.title,
                    "section_type": section.type,
                    "invalid_evidence_ids": invalid,
                    "rejected_draft": _rejected_draft_summary(draft),
                },
            )
        if section.type == EvidenceType.DOCUMENT_FACT and not evidence:
            raise ApiError(
                "MISSING_EVIDENCE",
                "文档事实缺少证据",
                502,
                details={
                    "section_title": section.title,
                    "rejected_draft": _rejected_draft_summary(draft),
                },
            )
        section.evidence_element_ids = [evidence_registry[item] for item in evidence]
        if section.type == EvidenceType.GENERAL_GUIDANCE and not section.disclaimer:
            section.disclaimer = GENERAL_DISCLAIMER
        inferred_risk = classify_risk(section.content)
        if inferred_risk in {RiskLevel.HIGH, RiskLevel.CRITICAL} and not evidence:
            draft.escalation.required = True
            draft.escalation.reason = "高风险操作没有当前文档依据"
            section.type = EvidenceType.WARNING
            section.content = "该操作属于高风险且当前文档没有明确依据，请暂停操作并联系技术支持。"
            section.risk_level = inferred_risk
        # Product-looking IPs must come from the current document; generic
        # loopback/private examples are intentionally not special-cased.
        for ip in _IP.findall(section.content):
            if ip not in document_text:
                raise ApiError(
                    "UNSUPPORTED_PRODUCT_PARAMETER",
                    "回答包含文档外的产品参数",
                    502,
                    details={
                        "section_title": section.title,
                        "parameter": ip,
                        "rejected_draft": _rejected_draft_summary(draft),
                    },
                )
    invalid_images = [image_id for image_id in draft.image_ids if image_id not in valid_images]
    if invalid_images:
        raise ApiError(
            "INVALID_IMAGE_REFERENCE",
            "模型引用了当前文档之外的图片",
            502,
            details={
                "invalid_image_ids": invalid_images,
                "rejected_draft": _rejected_draft_summary(draft),
            },
        )
    # Completion state is an explicit user fact, never a model inference.
    if draft.workflow_update.confirmed_completed_steps and not re.search(
        r"完成|已做|做完|完成了|成功", question
    ):
        draft.workflow_update.confirmed_completed_steps = []
    return draft
