from __future__ import annotations

import io
import zlib
from typing import Any
from uuid import uuid4

import pytest
from fastapi import UploadFile
from PIL import Image

from app.common.errors import ApiError
from app.core.config import Settings
from app.ingestion.models import (
    CONTENT_REVISION_DOCX,
    CONTENT_REVISION_PDF,
    DOCX_MIME_TYPE,
    PDF_MIME_TYPE,
    content_revision_for_mime,
)
from app.ingestion.parser_pdf import PdfParseError, parse_pdf
from app.ingestion.repository import BuildReference, DocumentCreateResult
from app.ingestion.validation import (
    PdfLimits,
    PdfValidationError,
    validate_pdf_document,
)
from app.services.ingestion import IngestionService

PageSpec = dict[str, Any]


def _jpeg_bytes(size: int = 32, color: tuple[int, int, int] = (200, 40, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buffer, format="JPEG")
    return buffer.getvalue()


def _raw_rgb_bytes(size: int = 32, color: tuple[int, int, int] = (10, 120, 220)) -> bytes:
    return bytes(list(color)) * (size * size)


def build_pdf(pages: list[PageSpec]) -> bytes:
    """Build a minimal valid PDF with text lines and raster XObjects.

    Lines carry ``text``/``y``/``size`` (PDF text space, y from bottom).
    Images carry ``kind`` (``jpeg``/``raw``), ``x``/``y``/``w``/``h`` in the
    same space. All pages share one MediaBox of 595x842 (A4).
    """
    objects: dict[int, bytes] = {}

    def add(number: int, body: bytes) -> None:
        objects[number] = body

    def stream_object(extra: bytes, payload: bytes) -> bytes:
        return (
            b"<< "
            + extra
            + b" /Length "
            + str(len(payload)).encode()
            + b" >>\nstream\n"
            + payload
            + b"\nendstream"
        )

    jpeg = _jpeg_bytes()
    add(
        10,
        stream_object(
            b"/Type /XObject /Subtype /Image /Width 32 /Height 32 /ColorSpace /DeviceRGB"
            b" /BitsPerComponent 8 /Filter /DCTDecode",
            jpeg,
        ),
    )
    raw = _raw_rgb_bytes()
    add(
        11,
        stream_object(
            b"/Type /XObject /Subtype /Image /Width 32 /Height 32 /ColorSpace /DeviceRGB"
            b" /BitsPerComponent 8 /Filter /FlateDecode",
            zlib.compress(raw),
        ),
    )
    # ICCBased colour space with an explicit RGB Alternate and N=3, the form
    # real Word/exported PDFs use; the parser must decode samples as RGB.
    icc_raw = bytes([90, 40, 160]) * (32 * 32)
    add(
        12,
        stream_object(
            b"/N 3 /Alternate /DeviceRGB",
            icc_raw,
        ),
    )
    add(
        13,
        stream_object(
            b"/Type /XObject /Subtype /Image /Width 32 /Height 32"
            b" /ColorSpace [/ICCBased 12 0 R] /BitsPerComponent 8 /Filter /FlateDecode",
            zlib.compress(icc_raw),
        ),
    )
    add(3, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    page_ids = list(range(100, 100 + len(pages)))
    add(
        2,
        b"<< /Type /Pages /Kids ["
        + b" ".join(f"{pid} 0 R".encode() for pid in page_ids)
        + b"] /Count "
        + str(len(pages)).encode()
        + b" >>",
    )
    add(1, b"<< /Type /Catalog /Pages 2 0 R >>")

    for page_index, page in enumerate(pages):
        ops: list[bytes] = [b"BT"]
        for line in page.get("lines", []):
            ops.append(
                f"/F1 {line.get('size', 12):.1f} Tf 1 0 0 1 {line.get('x', 72):.1f} "
                f"{line['y']:.1f} Tm ({line['text']}) Tj".encode()
            )
        ops.append(b"ET")
        xobjects: list[bytes] = []
        for image in page.get("images", []):
            reference = {"jpeg": 10, "raw": 11, "icc": 13}[image["kind"]]
            xobjects.append(f"/Im{reference} {reference} 0 R".encode())
            ops.append(
                f"q {image['w']} 0 0 {image['h']} {image['x']} {image['y']} cm "
                f"/Im{reference} Do Q".encode()
            )
        resources = b"/Font << /F1 3 0 R >>"
        if xobjects:
            resources += b" /XObject << " + b" ".join(xobjects) + b" >>"
        content = b"\n".join(ops)
        add(900 + page_index, stream_object(b"", content))
        add(
            100 + page_index,
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << "
            + resources
            + b" >> /Contents "
            + f"{900 + page_index} 0 R".encode()
            + b" >>",
        )

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = out.tell()
        out.write(f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n")
    max_object = max(objects)
    xref_position = out.tell()
    out.write(f"xref\n0 {max_object + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for number in range(1, max_object + 1):
        if number in offsets:
            out.write(f"{offsets[number]:010d} 00000 n \n".encode())
        else:
            out.write(b"0000000000 65535 f \n")
    out.write(
        f"trailer\n<< /Size {max_object + 1} /Root 1 0 R >>\nstartxref\n"
        f"{xref_position}\n%%EOF\n".encode()
    )
    return out.getvalue()


def _manual_page() -> PageSpec:
    return {
        "lines": [
            {"text": "Company Manual V2", "y": 800, "size": 10},
            {"text": "VAU600 Maintenance Guide", "y": 740, "size": 24},
            {"text": "Chapter 1 Overview", "y": 680, "size": 16},
            {"text": "This device is used for ship power distribution.", "y": 650, "size": 12},
            {"text": "It supports 24V DC input.", "y": 636, "size": 12},
        ],
        "images": [{"kind": "jpeg", "x": 100, "y": 380, "w": 200, "h": 150}],
    }


def test_parse_pdf_extracts_ordered_elements_with_sections() -> None:
    pages = [
        _manual_page(),
        {
            "lines": [
                {"text": "Company Manual V2", "y": 800, "size": 10},
                {"text": "Section 1.1 Panel", "y": 700, "size": 14},
                {"text": "The rear panel hosts terminals.", "y": 660, "size": 12},
            ],
            "images": [
                {"kind": "jpeg", "x": 100, "y": 500, "w": 120, "h": 90},
                {"kind": "raw", "x": 300, "y": 200, "w": 100, "h": 80},
            ],
        },
        {
            "lines": [
                {"text": "Company Manual V2", "y": 800, "size": 10},
                {"text": "Chapter 2 Installation", "y": 700, "size": 16},
                {"text": "Mount the unit vertically.", "y": 660, "size": 12},
            ],
            "images": [],
        },
    ]
    parsed = parse_pdf(build_pdf(pages), max_pages=50)

    sequence = [(element.element_type, element.content) for element in parsed.elements]
    assert sequence == [
        ("TEXT", "VAU600 Maintenance Guide"),
        ("TEXT", "Chapter 1 Overview"),
        (
            "TEXT",
            "This device is used for ship power distribution. It supports 24V DC input.",
        ),
        ("IMAGE", ""),
        ("TEXT", "Section 1.1 Panel"),
        ("TEXT", "The rear panel hosts terminals."),
        ("IMAGE", ""),
        ("IMAGE", ""),
        ("TEXT", "Chapter 2 Installation"),
        ("TEXT", "Mount the unit vertically."),
    ]

    by_content = {element.content: element for element in parsed.elements}
    assert by_content["VAU600 Maintenance Guide"].section_path == ["VAU600 Maintenance Guide"]
    assert by_content["VAU600 Maintenance Guide"].metadata["heading_level"] == 1
    assert by_content["Chapter 1 Overview"].metadata["heading_level"] == 2
    assert by_content["Chapter 1 Overview"].section_path == [
        "VAU600 Maintenance Guide",
        "Chapter 1 Overview",
    ]
    assert by_content["Section 1.1 Panel"].section_path == [
        "VAU600 Maintenance Guide",
        "Chapter 1 Overview",
        "Section 1.1 Panel",
    ]
    assert by_content["Mount the unit vertically."].section_path == [
        "VAU600 Maintenance Guide",
        "Chapter 2 Installation",
    ]

    images = [element for element in parsed.elements if element.element_type == "IMAGE"]
    assert images[0].image_mime_type == "image/jpeg"
    assert images[0].image_bytes == _jpeg_bytes()
    assert images[2].image_mime_type == "image/png"
    assert images[2].image_bytes.startswith(b"\x89PNG")
    assert images[2].metadata["page_number"] == 2
    for index, element in enumerate(parsed.elements, start=1):
        assert element.metadata["source_sequence_no"] == index


def test_parse_pdf_removes_repeated_headers_footers_and_page_numbers() -> None:
    pages: list[PageSpec] = []
    for page_number in range(1, 4):
        pages.append(
            {
                "lines": [
                    {"text": "Company Manual V2", "y": 800, "size": 10},
                    {"text": f"Body content number {page_number}", "y": 700, "size": 12},
                    {"text": f"- {page_number} -", "y": 40, "size": 9},
                ],
                "images": [],
            }
        )
    parsed = parse_pdf(build_pdf(pages), max_pages=50)
    texts = [element.content for element in parsed.elements if element.element_type == "TEXT"]
    assert texts == ["Body content number 1", "Body content number 2", "Body content number 3"]
    assert any("PDF_HEADER_FOOTER_REMOVED" in warning for warning in parsed.parser_warnings)


def test_parse_pdf_keeps_header_text_on_single_page_document() -> None:
    # One page means nothing can repeat; keep the band content instead of
    # guessing it away.
    parsed = parse_pdf(
        build_pdf(
            [
                {
                    "lines": [
                        {"text": "Company Manual V2", "y": 800, "size": 10},
                        {"text": "Only body paragraph.", "y": 700, "size": 12},
                    ],
                    "images": [],
                }
            ]
        ),
        max_pages=50,
    )
    texts = [element.content for element in parsed.elements if element.element_type == "TEXT"]
    assert texts == ["Company Manual V2", "Only body paragraph."]


def test_parse_pdf_keeps_one_off_lines_inside_header_band() -> None:
    pages: list[PageSpec] = []
    for page_number in range(1, 4):
        lines: list[dict[str, Any]] = [
            {"text": "Company Manual V2", "y": 800, "size": 10},
            {"text": f"Body {page_number}", "y": 700, "size": 12},
        ]
        if page_number == 2:
            lines.insert(1, {"text": "Important warning near the top", "y": 790, "size": 11})
        pages.append({"lines": lines, "images": []})
    parsed = parse_pdf(build_pdf(pages), max_pages=50)
    texts = [element.content for element in parsed.elements if element.element_type == "TEXT"]
    assert "Important warning near the top" in texts
    assert "Company Manual V2" not in texts


def test_parse_pdf_decodes_iccbased_raster_via_alternate_space() -> None:
    # Real Word/exported PDFs wrap RGB samples in an ICCBased space; the
    # parser must resolve N=3 / Alternate=DeviceRGB instead of failing.
    pages = [
        {
            "lines": [{"text": "Body text with an ICC photo.", "y": 700, "size": 12}],
            "images": [{"kind": "icc", "x": 100, "y": 300, "w": 200, "h": 160}],
        }
    ]
    parsed = parse_pdf(build_pdf(pages), max_pages=50)
    images = [element for element in parsed.elements if element.element_type == "IMAGE"]
    assert len(images) == 1
    assert images[0].image_mime_type == "image/png"
    assert images[0].image_bytes.startswith(b"\x89PNG")
    assert images[0].metadata["image_width"] == 32
    assert images[0].metadata["image_height"] == 32


def test_parse_pdf_removes_repeated_logo_but_keeps_body_photo() -> None:
    pages: list[PageSpec] = []
    for page_number in range(1, 4):
        pages.append(
            {
                "lines": [{"text": f"Device photo on page {page_number}", "y": 700, "size": 12}],
                "images": [
                    {"kind": "jpeg", "x": 20, "y": 800, "w": 60, "h": 24},
                    {"kind": "jpeg", "x": 100, "y": 400, "w": 200, "h": 140},
                ],
            }
        )
    parsed = parse_pdf(build_pdf(pages), max_pages=50)
    images = [element for element in parsed.elements if element.element_type == "IMAGE"]
    assert len(images) == 3
    for element in images:
        assert element.metadata["page_number"] in {1, 2, 3}
        assert element.image_bytes == _jpeg_bytes()
    assert any("PDF_HEADER_FOOTER_REMOVED" in warning for warning in parsed.parser_warnings)


def test_parse_pdf_rejects_invalid_file() -> None:
    with pytest.raises(PdfParseError, match="PDF 打开失败"):
        parse_pdf(b"definitely not a pdf", max_pages=10)


def test_parse_pdf_rejects_page_overflow() -> None:
    pages = [
        {"lines": [{"text": "one", "y": 700, "size": 12}], "images": []},
        {"lines": [{"text": "two", "y": 700, "size": 12}], "images": []},
    ]
    with pytest.raises(PdfParseError, match="页数超过限制"):
        parse_pdf(build_pdf(pages), max_pages=1)


def test_parse_pdf_rejects_document_without_content() -> None:
    # Valid PDF structure, but not a single text char or drawable image.
    pdf_bytes = build_pdf([{"lines": [], "images": []}])
    with pytest.raises(PdfParseError, match="未解析出可入库内容"):
        parse_pdf(pdf_bytes, max_pages=10)


def test_content_revision_is_per_format() -> None:
    assert content_revision_for_mime(DOCX_MIME_TYPE) == CONTENT_REVISION_DOCX == "ooxml-vml-1"
    assert content_revision_for_mime(PDF_MIME_TYPE) == CONTENT_REVISION_PDF == "pdf-1"
    assert content_revision_for_mime(None) == CONTENT_REVISION_DOCX
    assert content_revision_for_mime("application/x-unknown") == CONTENT_REVISION_DOCX


def test_validate_pdf_document_accepts_fixture() -> None:
    payload = build_pdf([_manual_page()])
    validate_pdf_document(
        io.BytesIO(payload), "manual.pdf", len(payload), PdfLimits(1_000_000, 100)
    )


@pytest.mark.parametrize(
    ("filename", "payload", "size", "max_pages", "message"),
    [
        ("manual.docx", b"%PDF-1.4", 8, 100, "仅支持 .pdf 文件"),
        ("manual.pdf", b"", 0, 100, "PDF 文件为空"),
        ("manual.pdf", b"x" * 10, 10, 100, "文件不是有效的 PDF"),
        ("manual.pdf", b"%PDF-1.4 broken", 16, 100, "文件不是有效的 PDF"),
    ],
)
def test_validate_pdf_document_rejects_bad_files(
    filename: str,
    payload: bytes,
    size: int,
    max_pages: int,
    message: str,
) -> None:
    with pytest.raises(PdfValidationError, match=message):
        validate_pdf_document(io.BytesIO(payload), filename, size, PdfLimits(1_000_000, max_pages))


def test_validate_pdf_document_rejects_oversize() -> None:
    payload = build_pdf([_manual_page()])
    with pytest.raises(PdfValidationError, match="超过大小限制"):
        validate_pdf_document(io.BytesIO(payload), "manual.pdf", len(payload), PdfLimits(10, 100))


def test_validate_pdf_document_rejects_page_overflow() -> None:
    pages = [
        {"lines": [{"text": "one", "y": 700, "size": 12}], "images": []},
        {"lines": [{"text": "two", "y": 700, "size": 12}], "images": []},
    ]
    payload = build_pdf(pages)
    with pytest.raises(PdfValidationError, match="页数超过限制"):
        validate_pdf_document(
            io.BytesIO(payload), "manual.pdf", len(payload), PdfLimits(1_000_000, 1)
        )


class _RecordingPdfMinio:
    def __init__(self) -> None:
        self.uploads: list[dict[str, Any]] = []

    def put_object(
        self, bucket: str, object_key: str, source: Any, length: int, **kwargs: Any
    ) -> None:
        self.uploads.append(
            {
                "bucket": bucket,
                "object_key": object_key,
                "length": length,
                "content_type": kwargs["content_type"],
                "data": source.read(),
            }
        )
        source.seek(0)

    def remove_object(self, bucket: str, object_key: str) -> None:
        raise AssertionError(f"unexpected removal: {bucket}/{object_key}")


class _RecordingUploadRepository:
    def __init__(self) -> None:
        self.create_kwargs: dict[str, Any] | None = None

    async def find_idempotent_response(
        self, operation: str, idempotency_key: str, request_hash: str
    ) -> dict[str, Any] | None:
        return None

    async def create_document(self, **kwargs: Any) -> DocumentCreateResult:
        self.create_kwargs = kwargs
        return DocumentCreateResult(
            response={
                "document": {"id": str(kwargs["document_id"])},
                "build_request": {
                    "requested": False,
                    "coalesced": False,
                    "index_id": None,
                    "task_id": None,
                    "rebuild_required": False,
                },
            },
            created=True,
            build=BuildReference(False, False, None, None, False),
        )


def _service(repository: Any, minio: Any) -> IngestionService:
    return IngestionService(
        repository,
        minio,
        Settings(_env_file=None, app_env="test", m2_embedding_provider="fake"),
    )


@pytest.mark.asyncio
async def test_upload_service_stores_pdf_with_pdf_metadata() -> None:
    payload = build_pdf([_manual_page()])
    repository = _RecordingUploadRepository()
    minio = _RecordingPdfMinio()
    upload = UploadFile(filename="manual.pdf", file=io.BytesIO(payload))
    try:
        response = await _service(repository, minio).upload_document(
            knowledge_id=uuid4(),
            upload=upload,
            display_name=None,
            request_build=False,
            idempotency_key="pdf-upload-test",
        )
    finally:
        await upload.close()

    assert response["build_request"]["requested"] is False
    assert repository.create_kwargs is not None
    assert repository.create_kwargs["mime_type"] == PDF_MIME_TYPE
    assert repository.create_kwargs["filename"] == "manual.pdf"
    assert repository.create_kwargs["display_name"] == "manual"
    assert len(minio.uploads) == 1
    upload_record = minio.uploads[0]
    assert upload_record["object_key"].endswith("/source.pdf")
    assert upload_record["content_type"] == PDF_MIME_TYPE
    assert upload_record["length"] == len(payload)
    assert upload_record["data"] == payload


@pytest.mark.asyncio
async def test_upload_service_rejects_unsupported_extension() -> None:
    upload = UploadFile(filename="manual.txt", file=io.BytesIO(b"plain text"))
    try:
        with pytest.raises(ApiError) as error:
            await _service(_RecordingUploadRepository(), _RecordingPdfMinio()).upload_document(
                knowledge_id=uuid4(),
                upload=upload,
                display_name=None,
                request_build=False,
                idempotency_key="txt-upload-test",
            )
    finally:
        await upload.close()
    assert error.value.code == "VALIDATION_ERROR"
    assert "仅支持 .docx 或 .pdf 文件" in error.value.message


@pytest.mark.asyncio
async def test_upload_service_rejects_non_pdf_bytes_named_pdf() -> None:
    upload = UploadFile(filename="fake.pdf", file=io.BytesIO(b"%PDF-1.4 not really a pdf"))
    try:
        with pytest.raises(ApiError) as error:
            await _service(_RecordingUploadRepository(), _RecordingPdfMinio()).upload_document(
                knowledge_id=uuid4(),
                upload=upload,
                display_name=None,
                request_build=False,
                idempotency_key="fake-pdf-test",
            )
    finally:
        await upload.close()
    assert error.value.code == "VALIDATION_ERROR"
    assert "文件不是有效的 PDF" in error.value.message
