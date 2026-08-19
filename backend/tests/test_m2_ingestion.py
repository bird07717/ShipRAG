from __future__ import annotations

from io import BytesIO
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from fastapi import UploadFile
from pydantic import SecretStr

from app.core.config import Settings
from app.ingestion.chunker import build_chunks, build_parent_chunks
from app.ingestion.embedding import FakeEmbeddingProvider, SiliconFlowEmbeddingProvider
from app.ingestion.models import EmbeddingInput, ParsedElement
from app.ingestion.parser import normalize_text, parse_docx
from app.ingestion.repository import (
    BuildReference,
    DocumentCreateResult,
    DocumentDeleteResult,
    IngestionRepository,
)
from app.ingestion.validation import DocxLimits, DocxValidationError, validate_docx_package
from app.m0.fixtures import create_docx_fixture
from app.services.ingestion import IngestionService


def _limits() -> DocxLimits:
    return DocxLimits(
        max_bytes=10_000_000,
        max_entries=1_000,
        max_uncompressed_bytes=20_000_000,
        max_entry_bytes=10_000_000,
        max_compression_ratio=100,
    )


def test_docx_validation_and_parser_preserve_order(tmp_path) -> None:
    path = tmp_path / "m2.docx"
    create_docx_fixture(path)
    data = path.read_bytes()
    validate_docx_package(BytesIO(data), path.name, len(data), _limits())

    parsed = parse_docx(data)

    assert [element.element_type for element in parsed.elements] == [
        "TEXT",
        "TEXT",
        "TABLE",
        "TEXT",
        "IMAGE",
        "TEXT",
    ]
    assert parsed.elements[0].section_path == ["数据库配置"]
    assert "port" in parsed.elements[2].content
    assert parsed.elements[4].image_bytes
    assert parsed.elements[4].image_mime_type == "image/png"
    assert parsed.mammoth_warnings == []


@pytest.mark.parametrize(
    ("filename", "payload", "message"),
    [
        ("invalid.doc", b"x", "仅支持 .docx"),
        ("invalid.docx", b"not-a-zip", "有效的 DOCX ZIP"),
        ("empty.docx", b"", "文件为空"),
    ],
)
def test_docx_validation_rejects_invalid_uploads(
    filename: str, payload: bytes, message: str
) -> None:
    with pytest.raises(DocxValidationError, match=message):
        validate_docx_package(BytesIO(payload), filename, len(payload), _limits())


def test_chunk_builder_creates_table_and_mixed_chunks(tmp_path) -> None:
    path = tmp_path / "m2.docx"
    create_docx_fixture(path)
    parsed = parse_docx(path.read_bytes())

    chunks = build_chunks(parsed.elements, target_chars=100, max_chars=300)

    assert [chunk.chunk_type for chunk in chunks] == [
        "TEXT",
        "TABLE",
        "TEXT",
        "MIXED",
        "TEXT",
    ]
    mixed = next(chunk for chunk in chunks if chunk.chunk_type == "MIXED")
    assert mixed.element_indexes == [3, 4, 5]
    assert mixed.image_bytes == parsed.elements[4].image_bytes
    assert all(chunk.token_count > 0 for chunk in chunks)
    assert {index for chunk in chunks for index in chunk.element_indexes} == set(
        range(len(parsed.elements))
    )


def test_large_table_repeats_header_when_split() -> None:
    rows = ["| 参数 | 说明 |", "| --- | --- |"]
    rows.extend(f"| key-{index} | {'值' * 40} |" for index in range(20))
    element = ParsedElement("TABLE", "\n".join(rows), ["配置"])

    chunks = build_chunks([element], target_chars=100, max_chars=220)

    assert len(chunks) > 1
    assert all(chunk.content.count("| 参数 | 说明 |") == 1 for chunk in chunks)
    assert all(chunk.element_indexes == [0] for chunk in chunks)


def test_text_chunks_overlap_by_whole_paragraph_and_build_parent_graph() -> None:
    elements = [
        ParsedElement("TEXT", f"步骤{index}：{'配置说明' * 8}。", ["VAU 配置"])
        for index in range(1, 5)
    ]

    chunks = build_chunks(
        elements,
        target_chars=70,
        max_chars=100,
        overlap_paragraphs=1,
    )
    parents = build_parent_chunks(elements, chunks, max_chars=1_000)

    assert [chunk.element_indexes for chunk in chunks] == [[0, 1], [1, 2], [2, 3]]
    assert len(parents) == 1
    assert parents[0].element_indexes == [0, 1, 2, 3]
    assert parents[0].child_indexes == [0, 1, 2]
    assert chunks[0].previous_chunk_index is None
    assert chunks[0].next_chunk_index == 1
    assert chunks[1].previous_chunk_index == 0
    assert chunks[1].next_chunk_index == 2
    assert chunks[2].previous_chunk_index == 1
    assert chunks[2].next_chunk_index is None


def test_chunk_completeness_detector_marks_suspended_text_before_image() -> None:
    elements = [
        ParsedElement("TEXT", "具体配置步骤如下：", ["VAU 配置"]),
        ParsedElement(
            "IMAGE",
            "图片描述：配置界面",
            ["VAU 配置"],
            image_bytes=b"image",
            image_mime_type="image/png",
        ),
    ]

    chunks = build_chunks(elements, target_chars=100, max_chars=200)

    text_chunk = chunks[0]
    assert text_chunk.suspected_incomplete is True
    assert text_chunk.is_procedural is True
    assert text_chunk.incomplete_reasons == [
        "TRAILING_SUSPENDED_PHRASE",
        "FOLLOWED_BY_IMAGE",
        "REFERENCES_FOLLOWING_CONTENT",
    ]


@pytest.mark.asyncio
async def test_fake_embedding_is_deterministic_finite_and_multimodal() -> None:
    provider = FakeEmbeddingProvider(64)
    inputs = [
        EmbeddingInput("数据库端口3306"),
        EmbeddingInput("数据库端口3306", b"image", "image/png"),
    ]

    first = await provider.embed(inputs)
    second = await provider.embed(inputs)

    assert first == second
    assert len(first) == 2
    assert all(len(vector) == 64 for vector in first)
    assert first[0] != first[1]
    assert all(abs(sum(value * value for value in vector) - 1) < 1e-6 for vector in first)


def test_text_normalization_is_stable() -> None:
    assert normalize_text("\uff21\uff22\uff23\u00a0  3306") == "ABC 3306"


@pytest.mark.asyncio
async def test_siliconflow_embedding_batches_and_validates_multimodal_input() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        requests.append(payload)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": index, "embedding": [0.1] * payload["dimensions"]}
                    for index in range(len(payload["input"]))
                ]
            },
        )

    settings = Settings(
        _env_file=None,
        app_env="test",
        siliconflow_api_key=SecretStr("not-a-real-key"),
        m2_embedding_batch_size=2,
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url=settings.siliconflow_base_url, transport=transport
    ) as client:
        provider = SiliconFlowEmbeddingProvider(settings, client)
        vectors = await provider.embed(
            [
                EmbeddingInput("one"),
                EmbeddingInput("two", b"image", "image/png"),
                EmbeddingInput("three"),
            ]
        )
        await provider.aclose()

    assert len(requests) == 2
    assert isinstance(requests[0]["input"][1], dict)
    assert requests[0]["input"][1]["image"].startswith("data:image/png;base64,")
    assert len(vectors) == 3
    assert all(len(vector) == 1024 for vector in vectors)


class _FakeUploadRepository:
    def __init__(self, replay: dict[str, Any] | None = None) -> None:
        self.replay = replay
        self.created = False

    async def find_idempotent_response(
        self, operation: str, idempotency_key: str, request_hash: str
    ) -> dict[str, Any] | None:
        assert operation.startswith("upload_document:")
        assert idempotency_key
        assert len(request_hash) == 64
        return self.replay

    async def create_document(self, **kwargs: Any) -> DocumentCreateResult:
        self.created = True
        response = {
            "document": {"id": str(kwargs["document_id"])},
            "build_request": {
                "requested": False,
                "coalesced": False,
                "index_id": None,
                "task_id": None,
                "rebuild_required": False,
            },
        }
        return DocumentCreateResult(
            response=response,
            created=True,
            build=BuildReference(False, False, None, None, False),
        )


class _FakeDeleteRepository:
    def __init__(self) -> None:
        self.document_id = uuid4()

    async def delete_document(
        self, document_id: Any, *, request_build: bool
    ) -> DocumentDeleteResult:
        assert document_id == self.document_id
        assert request_build is False
        return DocumentDeleteResult(
            document_id=self.document_id,
            build=BuildReference(False, False, None, None, False),
        )


class _FakeMinio:
    def __init__(self) -> None:
        self.uploaded = b""

    def put_object(self, bucket: str, object_key: str, source: Any, length: int, **_: Any) -> None:
        assert bucket == "rag-documents"
        assert object_key.endswith("source.docx")
        self.uploaded = source.read(length)

    def remove_object(self, bucket: str, object_key: str) -> None:
        raise AssertionError(f"unexpected removal: {bucket}/{object_key}")


@pytest.mark.asyncio
async def test_upload_service_stores_valid_docx_without_build(tmp_path) -> None:
    path = tmp_path / "upload.docx"
    create_docx_fixture(path)
    repository = _FakeUploadRepository()
    minio = _FakeMinio()
    service = IngestionService(
        cast(IngestionRepository, repository),
        cast(Any, minio),
        Settings(_env_file=None, app_env="test", m2_embedding_provider="fake"),
    )
    upload = UploadFile(filename=path.name, file=BytesIO(path.read_bytes()))

    response = await service.upload_document(
        knowledge_id=uuid4(),
        upload=upload,
        display_name="上传测试",
        request_build=False,
        idempotency_key="upload-test",
    )

    assert repository.created
    assert minio.uploaded == path.read_bytes()
    assert response["build_request"]["requested"] is False


@pytest.mark.asyncio
async def test_delete_service_returns_soft_delete_result_without_build() -> None:
    repository = _FakeDeleteRepository()
    service = IngestionService(
        cast(IngestionRepository, repository),
        cast(Any, _FakeMinio()),
        Settings(_env_file=None, app_env="test"),
    )

    response = await service.delete_document(repository.document_id, request_build=False)

    assert response == {
        "document_id": repository.document_id,
        "deleted": True,
        "build_request": {
            "requested": False,
            "coalesced": False,
            "index_id": None,
            "task_id": None,
            "rebuild_required": False,
        },
    }
