from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import IO


class DocxValidationError(ValueError):
    """The uploaded file is not a safe DOCX package."""


@dataclass(frozen=True, slots=True)
class DocxLimits:
    max_bytes: int
    max_entries: int
    max_uncompressed_bytes: int
    max_entry_bytes: int
    max_compression_ratio: float


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
