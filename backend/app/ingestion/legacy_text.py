"""Recover VML inline text fragments before element IDs/assets are assigned."""

from __future__ import annotations

import asyncio
import hashlib
import re
from io import BytesIO

from PIL import Image

from app.ingestion.image_understanding import ImageUnderstandingError, ImageUnderstandingProvider
from app.ingestion.models import ParsedElement
from app.ingestion.parser import DocxParseError, normalize_text


def prepare_fragment(data: bytes) -> tuple[bytes, int]:
    """Pad/upscale tiny words for page-oriented OCR without inventing pixels."""
    with Image.open(BytesIO(data)) as original:
        width, height = original.size
        character_limit = max(32, min(256, round(width / max(height, 1) * 12)))
        scale = min(4.0, 896 / max(width, 1), 160 / max(height, 1))
        resized = original.convert("RGB").resize(
            (max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.LANCZOS
        )
    canvas = Image.new("RGB", (max(512, resized.width + 64), 512), "white")
    canvas.paste(
        resized, ((canvas.width - resized.width) // 2, (canvas.height - resized.height) // 2)
    )
    output = BytesIO()
    canvas.save(output, format="PNG")
    return output.getvalue(), character_limit


async def restore_legacy_text(
    elements: list[ParsedElement], provider: ImageUnderstandingProvider, *, concurrency: int
) -> list[ParsedElement]:
    if any(
        element.metadata.get("legacy_text_candidate")
        and element.metadata.get("legacy_floating_layout")
        for element in elements
    ):
        # XML anchor order is not page reading order. Never concatenate
        # absolute-positioned words into potentially incorrect instructions.
        raise DocxParseError(
            "LEGACY_FLOATING_TEXT_LAYOUT: 无法可靠还原浮动文字图片的阅读顺序，"
            "请转换为可编辑正文的 DOCX 并核对步骤后重建"
        )
    semaphore = asyncio.Semaphore(concurrency)

    async def recover(element: ParsedElement) -> None:
        if not element.metadata.get("legacy_text_candidate") or not element.image_bytes:
            return
        if provider.provider in {"fake", "disabled"}:
            raise DocxParseError("LEGACY_TEXT_OCR_REQUIRED: 旧式文字图片需要真实 OCR，不能静默降级")
        async with semaphore:
            image, character_limit = await asyncio.to_thread(prepare_fragment, element.image_bytes)
            try:
                result = await provider.analyze(image, "image/png")
            except ImageUnderstandingError as exc:
                raise DocxParseError(
                    f"LEGACY_TEXT_OCR_FAILED: {element.metadata.get('source_target', 'image')} "
                    f"({exc.code})"
                ) from exc
        raw_text = re.sub(
            r"^```(?:markdown|text)?\s*\n(.*?)\n```$", r"\1", result.text.strip(), flags=re.DOTALL
        )
        content = normalize_text(raw_text)
        if not content or content in {"无文字", "无文本", "未识别到文字"}:
            raise DocxParseError("LEGACY_TEXT_OCR_EMPTY: 旧式文字图片识别为空，请检查源文档")
        if len(content) > character_limit:
            raise DocxParseError("LEGACY_TEXT_OCR_UNRELIABLE: 文字碎片 OCR 输出异常过长，停止构建")
        element.metadata.update(
            {
                "source_kind": "VML_OCR",
                "ocr_status": "READY",
                "ocr_provider": result.provider,
                "ocr_model": result.model_name,
                "original_image_sha256": hashlib.sha256(element.image_bytes).hexdigest(),
            }
        )
        element.element_type = "TEXT"
        element.content = content
        # The original bytes remain in the source DOCX. No image object has
        # been uploaded yet, so conversion cannot orphan storage objects.
        element.image_bytes = None
        element.image_mime_type = None

    tasks = [asyncio.create_task(recover(element)) for element in elements]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    merged: list[ParsedElement] = []
    for element in elements:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and previous.element_type == element.element_type == "TEXT"
            and "paragraph_id" in element.metadata
            and previous.metadata.get("paragraph_id") == element.metadata["paragraph_id"]
        ):
            previous.content += element.content
            previous.metadata.setdefault("inline_sources", []).append(dict(element.metadata))
        else:
            merged.append(element)
    for sequence_no, element in enumerate(merged, 1):
        element.metadata["source_sequence_no"] = sequence_no
    return merged
