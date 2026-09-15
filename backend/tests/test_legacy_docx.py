from __future__ import annotations

from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from docx import Document

from app.ingestion.legacy_text import restore_legacy_text
from app.ingestion.models import ImageUnderstandingResult
from app.ingestion.parser import A, DocxParseError, R, parse_docx
from app.m0.fixtures import make_text_image


def legacy_fixture(*, alternate=False, broken=False, floating=False):
    source = BytesIO()
    document = Document()
    document.add_picture(BytesIO(make_text_image()))
    document.save(source)
    source.seek(0)
    result = BytesIO()
    with ZipFile(source) as zin, ZipFile(result, "w") as zout:
        root = ET.fromstring(zin.read("word/document.xml"))
        blip = root.find(f".//{A}blip")
        rid = "missing" if broken else blip.attrib[f"{R}embed"]
        style = ' style="position:absolute;margin-top:74pt"' if floating else ""
        image = f'<w:r><w:pict><v:shape{style}><v:imagedata r:id="{rid}"/></v:shape></w:pict></w:r>'
        if alternate:
            image = (
                f'<mc:AlternateContent><mc:Choice Requires="v">{image}</mc:Choice>'
                f"<mc:Fallback>{image}</mc:Fallback></mc:AlternateContent>"
            )
        xml = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'xmlns:v="urn:schemas-microsoft-com:vml" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
            f"<w:body><w:p><w:r><w:t>1.</w:t></w:r>{image}<w:r><w:t>MU</w:t></w:r>"
            f"{image}</w:p></w:body></w:document>"
        )
        for entry in zin.infolist():
            zout.writestr(
                entry,
                xml.encode() if entry.filename == "word/document.xml" else zin.read(entry.filename),
            )
    return result.getvalue()


@pytest.mark.parametrize("alternate", [False, True])
def test_vml_preserves_occurrences_and_avoids_alternative_duplication(alternate):
    parsed = parse_docx(legacy_fixture(alternate=alternate))
    assert [e.element_type for e in parsed.elements] == ["TEXT", "IMAGE", "TEXT", "IMAGE"]
    assert all(
        e.metadata.get("image_markup") == "VML"
        for e in parsed.elements
        if e.element_type == "IMAGE"
    )
    assert "IMAGE_DOMINANT_DOCUMENT" in parsed.parser_warnings[-1]


def test_vml_broken_relationship_fails_instead_of_omitting_image():
    with pytest.raises(DocxParseError):
        parse_docx(legacy_fixture(broken=True))


class OCR:
    provider = "siliconflow"
    model_name = "test-ocr"

    async def analyze(self, data, mime):
        return ImageUnderstandingResult("打开", self.provider, self.model_name, {})


@pytest.mark.asyncio
async def test_ocr_text_fragments_merge_before_storage_ids_are_assigned():
    parsed = parse_docx(legacy_fixture())
    for e in parsed.elements:
        if e.element_type == "IMAGE":
            e.metadata["legacy_text_candidate"] = True
    restored = await restore_legacy_text(parsed.elements, OCR(), concurrency=2)
    assert len(restored) == 1 and restored[0].content == "1.打开MU打开"
    assert restored[0].element_type == "TEXT" and restored[0].image_bytes is None
    assert restored[0].metadata["inline_sources"][0]["source_kind"] == "VML_OCR"
    assert restored[0].metadata["source_sequence_no"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fake", "disabled"])
async def test_legacy_text_requires_ocr_instead_of_silent_partial_build(mode):
    parsed = parse_docx(legacy_fixture())
    parsed.elements[1].metadata["legacy_text_candidate"] = True
    provider = OCR()
    provider.provider = mode
    with pytest.raises(DocxParseError, match="LEGACY_TEXT_OCR_REQUIRED"):
        await restore_legacy_text(parsed.elements, provider, concurrency=1)


@pytest.mark.asyncio
async def test_ocr_hallucinated_long_output_fails_before_becoming_body():
    parsed = parse_docx(legacy_fixture())
    parsed.elements[1].metadata["legacy_text_candidate"] = True

    class BadOCR(OCR):
        async def analyze(self, data, mime):
            return ImageUnderstandingResult("12345" * 1000, self.provider, self.model_name, {})

    with pytest.raises(DocxParseError, match="LEGACY_TEXT_OCR_UNRELIABLE"):
        await restore_legacy_text(parsed.elements, BadOCR(), concurrency=1)
    assert parsed.elements[1].element_type == "IMAGE"


@pytest.mark.asyncio
async def test_floating_text_fails_before_any_ocr_or_element_mutation():
    parsed = parse_docx(legacy_fixture(floating=True))
    parsed.elements[1].metadata["legacy_text_candidate"] = True

    class NoCallOCR(OCR):
        async def analyze(self, data, mime):
            pytest.fail("Floating fragments must not be OCRed in XML anchor order")

    with pytest.raises(DocxParseError, match="LEGACY_FLOATING_TEXT_LAYOUT"):
        await restore_legacy_text(parsed.elements, NoCallOCR(), concurrency=2)
    assert parsed.elements[1].element_type == "IMAGE"
    assert parsed.elements[1].metadata["legacy_floating_layout"] is True


@pytest.mark.asyncio
async def test_queued_reuse_falls_back_to_parse_when_revision_or_hash_no_longer_matches():
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from uuid import uuid4

    from app.ingestion.models import DOCX_MIME_TYPE, content_revision_for_mime
    from app.ingestion.pipeline import IndexPipeline

    result = Mock()
    result.scalar_one_or_none.return_value = None
    connection = SimpleNamespace(execute=AsyncMock(return_value=result))

    class Engine:
        @asynccontextmanager
        async def connect(self):
            yield connection

    pipeline = object.__new__(IndexPipeline)
    pipeline.engine = Engine()
    pipeline.model_sources = {}
    pipeline._update_task = AsyncMock()
    pipeline._update_document_status = AsyncMock()
    pipeline._process_document = AsyncMock()
    document = {
        "index_document_id": uuid4(),
        "document_id": uuid4(),
        "kb_id": uuid4(),
        "filename": "legacy.docx",
        "mime_type": DOCX_MIME_TYPE,
        "source_hash": "original-source-hash",
    }
    index, task, previous = {"id": uuid4()}, uuid4(), uuid4()
    await pipeline._reuse_document(index, document, previous, task, 1, 1)
    query, parameters = connection.execute.call_args.args
    assert "status = 'READY'" in str(query)
    assert "source_hash = :source_hash" in str(query)
    assert "metadata->>'content_revision' = :content_revision" in str(query)
    assert parameters["content_revision"] == content_revision_for_mime(DOCX_MIME_TYPE)
    assert parameters["source_hash"] == document["source_hash"]
    pipeline._process_document.assert_awaited_once_with(index, document, task, 1, 1)


@pytest.mark.asyncio
async def test_queued_reuse_uses_pdf_revision_for_pdf_documents():
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from uuid import uuid4

    from app.ingestion.models import (
        CONTENT_REVISION_DOCX,
        CONTENT_REVISION_PDF,
        PDF_MIME_TYPE,
    )
    from app.ingestion.pipeline import IndexPipeline

    result = Mock()
    result.scalar_one_or_none.return_value = None
    connection = SimpleNamespace(execute=AsyncMock(return_value=result))

    class Engine:
        @asynccontextmanager
        async def connect(self):
            yield connection

    pipeline = object.__new__(IndexPipeline)
    pipeline.engine = Engine()
    pipeline.model_sources = {}
    pipeline._update_task = AsyncMock()
    pipeline._update_document_status = AsyncMock()
    pipeline._process_document = AsyncMock()
    document = {
        "index_document_id": uuid4(),
        "document_id": uuid4(),
        "kb_id": uuid4(),
        "filename": "manual.pdf",
        "mime_type": PDF_MIME_TYPE,
        "source_hash": "pdf-source-hash",
    }
    index, task, previous = {"id": uuid4()}, uuid4(), uuid4()
    await pipeline._reuse_document(index, document, previous, task, 1, 1)
    query, parameters = connection.execute.call_args.args
    assert parameters["content_revision"] == CONTENT_REVISION_PDF
    assert parameters["content_revision"] != CONTENT_REVISION_DOCX
    pipeline._process_document.assert_awaited_once_with(index, document, task, 1, 1)
