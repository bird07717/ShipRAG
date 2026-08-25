from __future__ import annotations

from typing import Any

from app.support.models import FocusedDocument


def _original_image_caption(content: str) -> str | None:
    """Extract only author-provided DOCX alt text from an enriched image element."""
    value = content.strip()
    if not value or value == "文档内图片":
        return None
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    for line in lines:
        if line.startswith("图片替代文本："):
            return line.removeprefix("图片替代文本：").strip() or None
    if any(line.startswith(("图片描述：", "图片文字：")) for line in lines):
        return None
    # Compatibility with indexes created before multimodal enrichment, where
    # document_element.content contained the original alt text directly.
    return value


def deliver_document(
    document: FocusedDocument, *, include_images: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return original elements in document order; no LLM or chunk reconstruction.

    Image captions expose only the original DOCX alt text — the ingestion-time
    vision description is retrieval enrichment, not document content, and
    showing it under delivered images reads as fabricated commentary.
    """
    content: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    for block in document.blocks:
        if block.type == "image":
            if not include_images or block.image_asset_id is None:
                continue
            image = {
                "type": "image",
                "sequence_no": block.sequence_no,
                "image_asset_id": str(block.image_asset_id),
                "caption": _original_image_caption(block.text),
                "mime_type": block.mime_type,
                "width": block.width,
                "height": block.height,
                "url": f"/api/v1/image-assets/{block.image_asset_id}/content",
            }
            content.append(image)
            images.append({key: value for key, value in image.items() if key != "type"})
        else:
            content.append(
                {
                    "type": "table" if block.type == "table" else "text",
                    "sequence_no": block.sequence_no,
                    "text": block.text,
                    "source_id": str(block.element_id),
                }
            )
    return content, images
