from __future__ import annotations

import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import IO

import pdfplumber


class DocxValidationError(ValueError):
    """The uploaded file is not a safe DOCX package."""


class PdfValidationError(ValueError):
    """The uploaded file is not a safe PDF document."""


@dataclass(frozen=True, slots=True)
class DocxLimits:
    max_bytes: int
    max_entries: int
    max_uncompressed_bytes: int
    max_entry_bytes: int
    max_compression_ratio: float


@dataclass(frozen=True, slots=True)
class PdfLimits:
    max_bytes: int
    max_pages: int


def validate_pdf_document(
    file_object: IO[bytes], filename: str, size: int, limits: PdfLimits
) -> None:
    if not filename.lower().endswith(".pdf"):
        raise PdfValidationError("仅支持 .pdf 文件")
    if size <= 0:
        raise PdfValidationError("PDF 文件为空")
    if size > limits.max_bytes:
        raise PdfValidationError("PDF 文件超过大小限制")

    file_object.seek(0)
    # The PDF spec allows junk before the header; scan the first KiB for it.
    head = file_object.read(1024)
    file_object.seek(0)
    if b"%PDF-" not in head:
        raise PdfValidationError("文件不是有效的 PDF")

    payload = file_object.read()
    file_object.seek(0)
    try:
        with pdfplumber.open(BytesIO(payload)) as pdf:
            # Refuse every encrypted document, including ones that an empty
            # password could decrypt: silent decryption would make the build
            # depend on library behaviour we do not audit.
            if getattr(pdf.doc, "encryption", None) is not None:
                raise PdfValidationError("不支持加密 PDF 文件")
            if len(pdf.pages) > limits.max_pages:
                raise PdfValidationError("PDF 页数超过限制")
    except PdfValidationError:
        raise
    except Exception as exc:
        raise PdfValidationError("文件不是有效的 PDF") from exc
    finally:
        file_object.seek(0)


def validate_docx_package(
    file_object: IO[bytes], filename: str, size: int, limits: DocxLimits
) -> None:
    if not filename.lower().endswith(".docx"):
        raise DocxValidationError("仅支持 .docx 文件")
    if size <= 0:
        raise DocxValidationError("DOCX 文件为空")
    if size > limits.max_bytes:
        raise DocxValidationError("DOCX 文件超过大小限制")

    file_object.seek(0)
    try:
        with zipfile.ZipFile(file_object) as archive:
            entries = archive.infolist()
            if len(entries) > limits.max_entries:
                raise DocxValidationError("DOCX 压缩条目数量超过限制")
            names = {entry.filename for entry in entries}
            required = {"[Content_Types].xml", "word/document.xml"}
            if not required.issubset(names):
                raise DocxValidationError("文件不是有效的 DOCX 包")
            if any(
                name.lower().endswith("vbaproject.bin") or "encryptedpackage" in name.lower()
                for name in names
            ):
                raise DocxValidationError("不支持宏或加密 Word 文件")

            total_uncompressed = 0
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if path.is_absolute() or ".." in path.parts:
                    raise DocxValidationError("DOCX 包含不安全路径")
                if entry.file_size > limits.max_entry_bytes:
                    raise DocxValidationError("DOCX 单个压缩条目超过限制")
                total_uncompressed += entry.file_size
                compressed = max(entry.compress_size, 1)
                if entry.file_size / compressed > limits.max_compression_ratio:
                    raise DocxValidationError("DOCX 压缩比超过安全限制")
            if total_uncompressed > limits.max_uncompressed_bytes:
                raise DocxValidationError("DOCX 解压后大小超过限制")
    except zipfile.BadZipFile as exc:
        raise DocxValidationError("文件不是有效的 DOCX ZIP") from exc
    finally:
        file_object.seek(0)
