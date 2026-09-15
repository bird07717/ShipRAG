from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import pdfplumber
from pdfminer.pdftypes import PDFStream
from PIL import Image

from app.ingestion.models import ParsedDocument, ParsedElement
from app.ingestion.parser import normalize_text

PDF_PARSER_NAME = "pdfplumber-pdf"

# Header/footer detection: content inside the top/bottom bands is only removed
# when it repeats across pages (logos, running titles, page numbers). One-off
# content near a page edge (first-page titles, section headings, photos placed
# high on the page) is always kept.
_HEADER_ZONE_RATIO = 0.10
_FOOTER_ZONE_RATIO = 0.10
_REPETITION_MIN_PAGES = 3

# Lines merge into one paragraph while the vertical gap stays below this
# multiple of the previous line's height.
_PARAGRAPH_GAP_FACTOR = 1.6
# Font sizes at least this much larger than the body size are heading
# candidates; the three largest distinct sizes map to heading levels 1-3.
_MIN_HEADING_SIZE_RATIO = 1.15
_MAX_HEADING_LEVELS = 3
# Images whose decoded raster is smaller than this on either edge are treated
# as decorative fragments and skipped with a warning, not delivered.
_MIN_IMAGE_EDGE = 16

_PAGE_NUMBER_PATTERN = re.compile(
    r"^(?:第\s*[0-9#]+\s*页|page\s*[0-9#]+|[0-9#]+"
    r"(?:\s*[-\u2013\u2014/]\s*[0-9#]+)?|[ivxlcdm#]+)$",
    re.IGNORECASE,
)


class PdfParseError(ValueError):
    """The PDF cannot be converted into ordered elements."""


@dataclass(slots=True)
class _PdfLine:
    text: str
    size: float
    top: float
    bottom: float
    x0: float
    page_number: int
    zone: str


@dataclass(slots=True)
class _PdfImage:
    digest: str
    data: bytes
    mime_type: str
    width: int
    height: int
    top: float
    x0: float
    page_number: int
    zone: str


def _pdf_name(value: Any) -> str:
    """Normalise pdfminer PSLiteral/PSName/str colour and filter names."""
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    return str(value).strip("/'")


def _colorspace_mode(colorspace: Any) -> str:
    """Map a PDF colour space onto a PIL raw mode."""
    entry = colorspace
    if isinstance(entry, list | tuple) and entry:
        entry = entry[0]
    name = _pdf_name(entry)
    if name in {"DeviceRGB", "RGB", "CalRGB", "Lab"}:
        return "RGB"
    if name in {"DeviceGray", "G", "CalGray"}:
        return "L"
    if name in {"DeviceCMYK", "CMYK"}:
        return "CMYK"
    if name == "ICCBased":
        raise PdfParseError("PDF_IMAGE_DECODE_FAILED: ICCBased 颜色分量无法确定")
    raise PdfParseError(f"PDF_IMAGE_DECODE_FAILED: 不支持的图片颜色空间 {name or '未知'}")


def _decode_raster(data: bytes, mode: str, width: int, height: int, bits: int) -> Image.Image:
    if width <= 0 or height <= 0:
        raise PdfParseError("PDF_IMAGE_DECODE_FAILED: 图片像素尺寸非法")
    if bits == 8:
        if len(data) < width * height * len(mode):
            raise PdfParseError("PDF_IMAGE_DECODE_FAILED: 图片像素数据不完整")
        return Image.frombytes(mode, (width, height), data)
    if bits == 1 and mode == "L":
        return Image.frombytes("1", (width, height), data)
    raise PdfParseError(f"PDF_IMAGE_DECODE_FAILED: 不支持的图片位深 {bits}")


def _composite_soft_mask(base: Image.Image, smask: PDFStream) -> Image.Image:
    try:
        mask_data = smask.get_data()
        raw_width = smask.get_any(("W", "Width"))
        raw_height = smask.get_any(("H", "Height"))
        raw_bits = smask.get_any(("BPC", "BitsPerComponent"))
        mask_width = int(raw_width) if raw_width is not None else base.width
        mask_height = int(raw_height) if raw_height is not None else base.height
        mask_bits = int(raw_bits) if raw_bits is not None else 8
        mask: Image.Image = _decode_raster(mask_data, "L", mask_width, mask_height, mask_bits)
    except PdfParseError:
        raise
    except Exception as exc:
        raise PdfParseError("PDF_IMAGE_DECODE_FAILED: 透明蒙版无法解码") from exc
    if mask.size != base.size:
        mask = mask.resize(base.size)
    base_image = base if base.mode == "RGB" else base.convert("RGB")
    flattened = Image.new("RGB", base_image.size, (255, 255, 255))
    flattened.paste(base_image, (0, 0), mask=mask)
    return flattened


def _encode_png(raster: Image.Image) -> bytes:
    buffer = BytesIO()
    raster.save(buffer, format="PNG")
    return buffer.getvalue()


def _decode_pdf_image(image: dict[str, Any]) -> tuple[bytes, str, int, int]:
    """Decode one pdfplumber image entry into storable bytes + MIME + size."""
    stream = image.get("stream")
    if not isinstance(stream, PDFStream):
        raise PdfParseError("PDF_IMAGE_DECODE_FAILED: 图片对象不是可解码流")
    try:
        filters = [_pdf_name(name) for name, _ in stream.get_filters()]
        data = stream.get_data()
    except Exception as exc:
        raise PdfParseError("PDF_IMAGE_DECODE_FAILED: 图片流无法读取") from exc

    width, height = image.get("srcsize") or (image.get("width"), image.get("height"))
    try:
        width, height = int(width), int(height)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise PdfParseError("PDF_IMAGE_DECODE_FAILED: 图片尺寸无法解析") from exc

    def with_soft_mask(raster: Image.Image) -> Image.Image:
        smask = stream.get_any(("SMask",))
        if smask is None:
            return raster
        if not isinstance(smask, PDFStream):
            raise PdfParseError("PDF_IMAGE_DECODE_FAILED: 透明蒙版无法解码")
        return _composite_soft_mask(raster, smask)

    if "DCTDecode" in filters:
        if not data.startswith(b"\xff\xd8"):
            raise PdfParseError("PDF_IMAGE_DECODE_FAILED: DCT 图片不是有效 JPEG")
        smask = stream.get_any(("SMask",))
        if smask is None:
            return data, "image/jpeg", width, height
        try:
            with Image.open(BytesIO(data)) as opened:
                composited = with_soft_mask(opened.convert("RGB"))
        except PdfParseError:
            raise
        except Exception as exc:
            raise PdfParseError("PDF_IMAGE_DECODE_FAILED: DCT 图片无法解码") from exc
        return _encode_png(composited), "image/png", composited.width, composited.height
    if "JPXDecode" in filters:
        try:
            with Image.open(BytesIO(data)) as opened:
                composited = with_soft_mask(opened.convert("RGB"))
        except PdfParseError:
            raise
        except Exception as exc:
            raise PdfParseError("PDF_IMAGE_DECODE_FAILED: JPEG2000 图片无法解码") from exc
        return _encode_png(composited), "image/png", composited.width, composited.height

    supported = {"FlateDecode", "ASCIIHexDecode", "ASCII85Decode", "RunLengthDecode"}
    unsupported = [name for name in filters if name not in supported]
    if unsupported:
        raise PdfParseError(f"PDF_IMAGE_DECODE_FAILED: 不支持的图片编码 {','.join(unsupported)}")
    bits = int(image.get("bits") or 8)
    mode = _colorspace_mode(image.get("colorspace"))
    try:
        raster: Image.Image = _decode_raster(data, mode, width, height, bits)
    except PdfParseError:
        raise
    except Exception as exc:
        raise PdfParseError("PDF_IMAGE_DECODE_FAILED: 图片像素数据无法重组") from exc
    raster = with_soft_mask(raster)
    if raster.mode == "CMYK":
        raster = raster.convert("RGB")
    return _encode_png(raster), "image/png", raster.width, raster.height


def _zone(top: float, bottom: float, page_height: float) -> str:
    if top < page_height * _HEADER_ZONE_RATIO:
        return "header"
    if bottom > page_height * (1.0 - _FOOTER_ZONE_RATIO):
        return "footer"
    return "body"


def _normalise_running_text(text: str) -> str:
    digits_normalised = re.sub(r"[0-9]+", "#", text)
    return re.sub(r"\s+", "", digits_normalised)


def _page_number_like(text: str) -> bool:
    stripped = re.sub(r"\s+", " ", text).strip()
    return bool(stripped) and bool(_PAGE_NUMBER_PATTERN.match(stripped))


def _repetition_threshold(page_count: int) -> int:
    # A single-page document has nothing that can repeat across pages.
    if page_count < 2:
        return page_count + 1
    return min(_REPETITION_MIN_PAGES, page_count)


def _heading_levels(lines: list[_PdfLine]) -> dict[float, int]:
    """Map distinct above-body font sizes onto heading levels 1-3."""
    if not lines:
        return {}
    size_weights: Counter[float] = Counter()
    for line in lines:
        size_weights[round(line.size, 1)] += len(line.text)
    if not size_weights:
        return {}
    body_size = size_weights.most_common(1)[0][0]
    heading_sizes = sorted(
        (size for size in size_weights if size >= body_size * _MIN_HEADING_SIZE_RATIO),
        reverse=True,
    )[:_MAX_HEADING_LEVELS]
    return {size: level for level, size in enumerate(heading_sizes, start=1)}


def parse_pdf(data: bytes, *, max_pages: int) -> ParsedDocument:
    """Parse a text-and-photo PDF into the shared ordered element contract.

    Headers/footers (repeated logos and running text) are removed before
    anything else; the remaining text lines and raster images are emitted in
    page reading order. Undecodable images fail the whole document instead of
    silently publishing incomplete content.
    """
    warnings: list[str] = []
    try:
        pdf = pdfplumber.open(BytesIO(data))
    except Exception as exc:
        raise PdfParseError("PDF 打开失败: 文件损坏或加密") from exc
    with pdf:
        if getattr(pdf.doc, "encryption", None) is not None:
            raise PdfParseError("PDF 加密，无法解析")
        page_count = len(pdf.pages)
        if page_count == 0:
            raise PdfParseError("PDF 没有页面")
        if page_count > max_pages:
            raise PdfParseError("PDF 页数超过限制")
        try:
            all_lines, page_images, skipped_count = _collect_page_content(pdf)
        except PdfParseError:
            raise
        except Exception as exc:
            raise PdfParseError("PDF 页面内容提取失败") from exc

    removed_line_ids, removed_image_digests, zone_warnings = _remove_running_content(
        all_lines, page_images, page_count
    )
    warnings.extend(zone_warnings)

    elements: list[ParsedElement] = []
    headings: list[str] = []
    levels = _heading_levels(all_lines)
    kept_images = [
        image
        for image in page_images
        if not (image.zone != "body" and image.digest in removed_image_digests)
    ]

    ordered: list[tuple[int, float, float, str, Any]] = []
    for ordered_line in all_lines:
        if id(ordered_line) in removed_line_ids:
            continue
        ordered.append(
            (ordered_line.page_number, ordered_line.top, ordered_line.x0, "line", ordered_line)
        )
    for ordered_image in kept_images:
        ordered.append(
            (ordered_image.page_number, ordered_image.top, ordered_image.x0, "image", ordered_image)
        )
    ordered.sort(key=lambda item: (item[0], item[1], item[2], item[3]))

    paragraph: list[_PdfLine] = []

    def flush_paragraph() -> None:
        if not paragraph:
            return
        text = normalize_text(" ".join(part.text for part in paragraph))
        if text:
            elements.append(
                ParsedElement(
                    element_type="TEXT",
                    content=text,
                    section_path=list(headings),
                    metadata={
                        "page_number": paragraph[0].page_number,
                        "paragraph_style": "body",
                    },
                )
            )
        paragraph.clear()

    for _, _, _, kind, payload in ordered:
        if kind == "image":
            flush_paragraph()
            image: _PdfImage = payload
            elements.append(
                ParsedElement(
                    element_type="IMAGE",
                    content="",
                    section_path=list(headings),
                    metadata={
                        "page_number": image.page_number,
                        "image_width": image.width,
                        "image_height": image.height,
                    },
                    image_bytes=image.data,
                    image_mime_type=image.mime_type,
                )
            )
            continue
        line: _PdfLine = payload
        level = levels.get(round(line.size, 1))
        if level is not None:
            flush_paragraph()
            heading_text = normalize_text(line.text)
            if heading_text:
                headings = headings[: level - 1]
                headings.append(heading_text)
                elements.append(
                    ParsedElement(
                        element_type="TEXT",
                        content=heading_text,
                        section_path=list(headings),
                        metadata={
                            "page_number": line.page_number,
                            "heading_level": level,
                            "paragraph_style": "heading",
                        },
                    )
                )
                continue
        if paragraph:
            previous = paragraph[-1]
            line_height = max(previous.bottom - previous.top, 1.0)
            gap = line.top - previous.bottom
            same_style = (
                previous.page_number == line.page_number
                and levels.get(round(previous.size, 1)) is None
                and levels.get(round(line.size, 1)) is None
            )
            # Page-local coordinates make cross-page gaps meaningless; a
            # paragraph never merges across a page boundary.
            if not same_style or gap > _PARAGRAPH_GAP_FACTOR * line_height:
                flush_paragraph()
        paragraph.append(line)
    flush_paragraph()

    if not elements:
        raise PdfParseError("PDF 未解析出可入库内容")

    image_element_count = sum(element.element_type == "IMAGE" for element in elements)
    text_length = sum(len(e.content) for e in elements if e.element_type != "IMAGE")
    if skipped_count:
        warnings.append(f"PDF_IMAGES_SKIPPED: 跳过 {skipped_count} 个装饰性小图片")
    if image_element_count and text_length < 80:
        warnings.append("IMAGE_DOMINANT_DOCUMENT: 正文稀疏，需核验图片文字及阅读顺序")
    for sequence_no, element in enumerate(elements, start=1):
        element.metadata["source_sequence_no"] = sequence_no
    return ParsedDocument(elements=elements, parser_warnings=warnings)


def _collect_page_content(
    pdf: Any,
) -> tuple[list[_PdfLine], list[_PdfImage], int]:
    all_lines: list[_PdfLine] = []
    page_images: list[_PdfImage] = []
    skipped_count = 0
    for page_number, page in enumerate(pdf.pages, start=1):
        page_height = float(page.height)
        for raw_line in page.extract_text_lines():
            text = normalize_text(raw_line["text"])
            if not text:
                continue
            sizes = {round(char["size"], 1) for char in raw_line["chars"]}
            all_lines.append(
                _PdfLine(
                    text=text,
                    size=max(sizes) if sizes else 0.0,
                    top=float(raw_line["top"]),
                    bottom=float(raw_line["bottom"]),
                    x0=float(raw_line["x0"]),
                    page_number=page_number,
                    zone=_zone(float(raw_line["top"]), float(raw_line["bottom"]), page_height),
                )
            )
        for image in page.images:
            if image.get("imagemask"):
                skipped_count += 1
                continue
            width, height_px = image.get("srcsize") or (0, 0)
            try:
                width, height_px = int(width), int(height_px)
            except (TypeError, ValueError):
                width = height_px = 0
            if width < _MIN_IMAGE_EDGE or height_px < _MIN_IMAGE_EDGE:
                skipped_count += 1
                continue
            data, mime_type, decoded_width, decoded_height = _decode_pdf_image(image)
            page_images.append(
                _PdfImage(
                    digest=hashlib.sha256(data).hexdigest(),
                    data=data,
                    mime_type=mime_type,
                    width=decoded_width,
                    height=decoded_height,
                    top=float(image["top"]),
                    x0=float(image["x0"]),
                    page_number=page_number,
                    zone=_zone(float(image["top"]), float(image["bottom"]), page_height),
                )
            )
    return all_lines, page_images, skipped_count


def _remove_running_content(
    lines: list[_PdfLine], images: list[_PdfImage], page_count: int
) -> tuple[set[int], set[str], list[str]]:
    """Remove cross-page repeated header/footer text and logo images.

    Returns (removed line ids, removed image digests, warnings). Text removal
    is zone-scoped and repetition-gated; page-number-like text in the bands is
    always removed. An image digest is only removed for its band occurrences:
    if the same picture also appears in body flow, that occurrence survives.
    """
    threshold = _repetition_threshold(page_count)
    removed_lines: set[int] = set()
    removed_image_digests: set[str] = set()
    warnings: list[str] = []

    text_pages: defaultdict[tuple[str, str], set[int]] = defaultdict(set)
    for line in lines:
        if line.zone == "body":
            continue
        if _page_number_like(line.text):
            removed_lines.add(id(line))
            continue
        text_pages[(line.zone, _normalise_running_text(line.text))].add(line.page_number)
    repeated_text_keys = {key for key, pages in text_pages.items() if len(pages) >= threshold}
    for line in lines:
        if line.zone == "body" or _page_number_like(line.text):
            continue
        if (line.zone, _normalise_running_text(line.text)) in repeated_text_keys:
            removed_lines.add(id(line))

    image_pages: defaultdict[tuple[str, str], set[int]] = defaultdict(set)
    for image in images:
        if image.zone != "body":
            image_pages[(image.zone, image.digest)].add(image.page_number)
    for (zone, digest), pages in image_pages.items():
        _ = zone
        if len(pages) >= threshold:
            removed_image_digests.add(digest)

    text_removed = len(removed_lines)
    images_removed = sum(
        1 for image in images if image.zone != "body" and image.digest in removed_image_digests
    )
    if text_removed or images_removed:
        warnings.append(
            f"PDF_HEADER_FOOTER_REMOVED: 剔除重复文本 {text_removed} 行、图片 {images_removed} 处"
        )
    return removed_lines, removed_image_digests, warnings
