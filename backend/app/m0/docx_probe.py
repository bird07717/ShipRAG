from __future__ import annotations

import base64
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import mammoth

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


@dataclass(slots=True)
class DocxProbeReport:
    status: str
    heading_found: bool
    paragraph_found: bool
    table_found: bool
    mammoth_image_count: int
    media_files: list[str]
    ordered_blocks: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _image_converter(image: Any) -> dict[str, str]:
    with image.open() as image_bytes:
        encoded = base64.b64encode(image_bytes.read()).decode("ascii")
    return {"src": f"data:{image.content_type};base64,{encoded}"}


def _ordered_ooxml_blocks(archive: zipfile.ZipFile) -> list[str]:
    document_root = ElementTree.fromstring(archive.read("word/document.xml"))
    rels_root = ElementTree.fromstring(archive.read("word/_rels/document.xml.rels"))
    relationships = {
        relationship.attrib["Id"]: relationship.attrib["Target"]
        for relationship in rels_root.findall(f"{{{PACKAGE_REL_NS}}}Relationship")
    }

    blocks: list[str] = []
    body = document_root.find(f"{{{WORD_NS}}}body")
    if body is None:
        return blocks
    for child in body:
        texts = [node.text or "" for node in child.findall(f".//{{{WORD_NS}}}t")]
        text = "".join(texts).strip()
        blips = child.findall(f".//{{{DRAWING_NS}}}blip")
        if child.tag == f"{{{WORD_NS}}}tbl":
            blocks.append(f"TABLE:{text}")
        elif text:
            blocks.append(f"TEXT:{text}")
        for blip in blips:
            relationship_id = blip.attrib.get(f"{{{REL_NS}}}embed", "")
            target = relationships.get(relationship_id, "unknown")
            blocks.append(f"IMAGE:{target}")
    return blocks


def inspect_docx(path: Path) -> DocxProbeReport:
    with path.open("rb") as docx_file:
        result = mammoth.convert_to_html(
            docx_file,
            convert_image=mammoth.images.img_element(_image_converter),
        )

    with zipfile.ZipFile(path) as archive:
        media_files = sorted(name for name in archive.namelist() if name.startswith("word/media/"))
        blocks = _ordered_ooxml_blocks(archive)

    html = result.value
    heading_found = "<h1>数据库配置</h1>" in html
    paragraph_found = "数据库默认端口为3306" in html
    table_found = all(value in html for value in ("<table>", "参数", "port", "数据库端口"))
    image_count = html.count("<img ")
    passed = all(
        (
            heading_found,
            paragraph_found,
            table_found,
            image_count == 1,
            len(media_files) == 1,
            any(block.startswith("IMAGE:") for block in blocks),
        )
    )
    return DocxProbeReport(
        status="passed" if passed else "failed",
        heading_found=heading_found,
        paragraph_found=paragraph_found,
        table_found=table_found,
        mammoth_image_count=image_count,
        media_files=media_files,
        ordered_blocks=blocks,
        warnings=[message.message for message in result.messages],
    )
