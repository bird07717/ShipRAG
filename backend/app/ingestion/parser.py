from __future__ import annotations

import re
import unicodedata
import zipfile
from collections.abc import Iterator
from io import BytesIO
from pathlib import PurePosixPath
from xml.etree import ElementTree

import mammoth
from PIL import Image

from app.ingestion.models import ParsedDocument, ParsedElement

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
WORD_DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"

W = f"{{{WORD_NS}}}"
A = f"{{{DRAWING_NS}}}"
R = f"{{{REL_NS}}}"
WP = f"{{{WORD_DRAWING_NS}}}"
V = "{urn:schemas-microsoft-com:vml}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"


def _content_nodes(node: ElementTree.Element) -> Iterator[ElementTree.Element]:
    """Read one OOXML alternative, never both DrawingML and VML fallback."""
    yield node
    children = list(node)
    if node.tag == f"{MC}AlternateContent":
        supported = next(
            (
                child
                for child in children
                if child.tag == f"{MC}Choice"
                and any(n.tag in (f"{A}blip", f"{V}imagedata") for n in child.iter())
            ),
            None,
        )
        fallback = next((child for child in children if child.tag == f"{MC}Fallback"), None)
        chosen = supported if supported is not None else fallback
        children = [chosen] if chosen is not None else []
    for child in children:
        yield from _content_nodes(child)


class DocxParseError(ValueError):
    """The DOCX structure cannot be converted into ordered elements."""


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.replace("\u00a0", " ")
    return re.sub(r"[ \t\r\f\v]+", " ", normalized).strip()


def _relationships(archive: zipfile.ZipFile) -> dict[str, str]:
    path = "word/_rels/document.xml.rels"
    if path not in archive.namelist():
        return {}
    root = ElementTree.fromstring(archive.read(path))
    relationships: dict[str, str] = {}
    for relationship in root.findall(f"{{{PACKAGE_REL_NS}}}Relationship"):
        relationship_id = relationship.attrib.get("Id")
        target = relationship.attrib.get("Target")
        target_mode = relationship.attrib.get("TargetMode", "Internal")
        if relationship_id and target and target_mode == "Internal":
            resolved = PurePosixPath("word") / PurePosixPath(target)
            relationships[relationship_id] = str(resolved)
    return relationships


def _content_types(archive: zipfile.ZipFile) -> dict[str, str]:
    root = ElementTree.fromstring(archive.read("[Content_Types].xml"))
    defaults: dict[str, str] = {}
    for entry in root.findall(f"{{{CONTENT_TYPES_NS}}}Default"):
        extension = entry.attrib.get("Extension", "").lower()
        content_type = entry.attrib.get("ContentType", "")
        if extension and content_type:
            defaults[extension] = content_type
    return defaults


def _heading_level(paragraph: ElementTree.Element) -> int | None:
    style = paragraph.find(f"./{W}pPr/{W}pStyle")
    if style is None:
        return None
    value = style.attrib.get(f"{W}val", "")
    match = re.search(r"(?:heading|标题)\s*([1-9])", value, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _image_alt_text(paragraph: ElementTree.Element, relationship_id: str) -> str:
    _ = relationship_id
    for properties in paragraph.findall(f".//{WP}docPr"):
        value = properties.attrib.get("descr") or properties.attrib.get("title")
        if value:
            return normalize_text(value)
    return ""


def _image_element(
    archive: zipfile.ZipFile,
    relationship_id: str,
    relationships: dict[str, str],
    content_types: dict[str, str],
    section_path: list[str],
    paragraph: ElementTree.Element,
) -> ParsedElement:
    target = relationships.get(relationship_id)
    if target is None or target not in archive.namelist():
        raise DocxParseError(f"图片关系无法解析: {relationship_id}")
    extension = PurePosixPath(target).suffix.lower().lstrip(".")
    mime_type = content_types.get(extension, f"image/{extension or 'unknown'}")
    if not mime_type.startswith("image/"):
        raise DocxParseError(f"图片 MIME 不合法: {mime_type}")
    alt_text = _image_alt_text(paragraph, relationship_id)
    return ParsedElement(
        element_type="IMAGE",
        content=alt_text,
        section_path=list(section_path),
        metadata={"relationship_id": relationship_id, "source_target": target},
        image_bytes=archive.read(target),
        image_mime_type=mime_type,
    )


def _paragraph_elements(
    archive: zipfile.ZipFile,
    paragraph: ElementTree.Element,
    relationships: dict[str, str],
    content_types: dict[str, str],
    section_path: list[str],
) -> list[ParsedElement]:
    elements: list[ParsedElement] = []
    text_parts: list[str] = []
    floating_images = {
        id(image)
        for shape in _content_nodes(paragraph)
        if shape.tag == f"{V}shape"
        and re.search(r"position\s*:\s*absolute", shape.attrib.get("style", ""), re.I)
        for image in shape.iter(f"{V}imagedata")
    }

    def flush_text() -> None:
        text = normalize_text("".join(text_parts))
        text_parts.clear()
        if text:
            elements.append(
                ParsedElement(
                    element_type="TEXT",
                    content=text,
                    section_path=list(section_path),
                    metadata={"paragraph_style": "body"},
                )
            )

    for node in _content_nodes(paragraph):
        if node.tag == f"{W}t" and node.text:
            text_parts.append(node.text)
        elif node.tag == f"{W}tab":
            text_parts.append("\t")
        elif node.tag == f"{W}br":
            text_parts.append("\n")
        elif node.tag in (f"{A}blip", f"{V}imagedata"):
            relationship_id = node.attrib.get(f"{R}embed") or node.attrib.get(f"{R}id")
            if relationship_id:
                flush_text()
                image = _image_element(
                    archive,
                    relationship_id,
                    relationships,
                    content_types,
                    section_path,
                    paragraph,
                )
                if node.tag == f"{V}imagedata":
                    image.metadata["image_markup"] = "VML"
                    image.metadata["legacy_floating_layout"] = id(node) in floating_images
                    try:
                        with Image.open(BytesIO(image.image_bytes or b"")) as bitmap:
                            # Only a candidate for OCR, never proof of text.
                            image.metadata["legacy_text_candidate"] = bitmap.height <= 80
                    except (OSError, ValueError):
                        image.metadata["legacy_text_candidate"] = False
                elements.append(image)
    flush_text()
    return elements


def _table_markdown(table: ElementTree.Element) -> str:
    rows: list[list[str]] = []
    for row in table.findall(f"./{W}tr"):
        cells: list[str] = []
        for cell in row.findall(f"./{W}tc"):
            text = normalize_text("".join(node.text or "" for node in cell.findall(f".//{W}t")))
            cells.append(text.replace("|", "\\|"))
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized_rows = [row + [""] * (width - len(row)) for row in rows]
    header = normalized_rows[0]
    lines = [f"| {' | '.join(header)} |", f"| {' | '.join(['---'] * width)} |"]
    lines.extend(f"| {' | '.join(row)} |" for row in normalized_rows[1:])
    return "\n".join(lines)


def parse_docx(data: bytes) -> ParsedDocument:
    try:
        mammoth_result = mammoth.convert_to_html(BytesIO(data))
        mammoth_warnings = [message.message for message in mammoth_result.messages]
        with zipfile.ZipFile(BytesIO(data)) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
            relationships = _relationships(archive)
            content_types = _content_types(archive)
            body = root.find(f"{W}body")
            if body is None:
                raise DocxParseError("DOCX 缺少正文")

            elements: list[ParsedElement] = []
            headings: list[str] = []
            for paragraph_id, child in enumerate(body):
                if child.tag == f"{W}p":
                    paragraph_elements = _paragraph_elements(
                        archive,
                        child,
                        relationships,
                        content_types,
                        headings,
                    )
                    for element in paragraph_elements:
                        element.metadata["paragraph_id"] = paragraph_id
                    heading_level = _heading_level(child)
                    if heading_level is not None:
                        heading_text = " ".join(
                            element.content
                            for element in paragraph_elements
                            if element.element_type == "TEXT"
                        ).strip()
                        if heading_text:
                            headings = headings[: heading_level - 1]
                            headings.append(heading_text)
                            for element in paragraph_elements:
                                element.section_path = list(headings)
                                element.metadata["heading_level"] = heading_level
                    elements.extend(paragraph_elements)
                elif child.tag == f"{W}tbl":
                    if any(n.tag == f"{V}imagedata" for n in _content_nodes(child)):
                        # Legacy text-image cells must retain XML reading order;
                        # do not move all images behind a text-only table.
                        for cell_no, cell in enumerate(child.findall(f".//{W}tc")):
                            for p_no, paragraph in enumerate(cell.findall(f"./{W}p")):
                                cell_elements = _paragraph_elements(
                                    archive, paragraph, relationships, content_types, headings
                                )
                                for element in cell_elements:
                                    element.metadata["paragraph_id"] = (
                                        f"{paragraph_id}:{cell_no}:{p_no}"
                                    )
                                elements.extend(cell_elements)
                        mammoth_warnings.append(
                            "LEGACY_IMAGE_TABLE_LINEARIZED: 旧式图片表格按单元格阅读顺序提取"
                        )
                        continue
                    markdown = _table_markdown(child)
                    if markdown:
                        elements.append(
                            ParsedElement(
                                element_type="TABLE",
                                content=markdown,
                                section_path=list(headings),
                                metadata={"format": "markdown"},
                            )
                        )
                    for blip in child.findall(f".//{A}blip"):
                        relationship_id = blip.attrib.get(f"{R}embed")
                        if relationship_id:
                            elements.append(
                                _image_element(
                                    archive,
                                    relationship_id,
                                    relationships,
                                    content_types,
                                    headings,
                                    child,
                                )
                            )
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise DocxParseError("DOCX OOXML 解析失败") from exc

    if not elements:
        raise DocxParseError("DOCX 未解析出可入库内容")
    referenced = sum(node.tag in (f"{A}blip", f"{V}imagedata") for node in _content_nodes(root))
    extracted = sum(element.element_type == "IMAGE" for element in elements)
    if referenced > extracted:
        raise DocxParseError("IMAGE_EXTRACTION_INCOMPLETE: 文档图片未完整提取")
    text_length = sum(len(e.content) for e in elements if e.element_type != "IMAGE")
    fragments = [e for e in elements if e.metadata.get("legacy_text_candidate")]
    # Small logos/icons are not automatically promoted into body text. Only
    # the sparse-body, many-fragment legacy format enters text restoration.
    if len(fragments) < 10 or text_length >= 80:
        for element in fragments:
            element.metadata["legacy_text_candidate"] = False
    elif any(e.metadata.get("legacy_floating_layout") for e in fragments):
        mammoth_warnings.append(
            "LEGACY_FLOATING_TEXT_LAYOUT: 文字图片按页面绝对坐标排版，需转换为可编辑正文后重建"
        )
    if extracted and text_length < 80:
        mammoth_warnings.append("IMAGE_DOMINANT_DOCUMENT: 正文稀疏，需核验图片文字及阅读顺序")
    for sequence_no, element in enumerate(elements, start=1):
        element.metadata["source_sequence_no"] = sequence_no
    return ParsedDocument(elements=elements, parser_warnings=mammoth_warnings)
