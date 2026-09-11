from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import UploadFile
from pydantic import SecretStr, ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.schemas import IndexBuildRequest
from app.common.assets import reject_control_characters
from app.common.errors import ApiError
from app.common.text import emphasize_document_title_mentions, markdown_document_title
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


def test_index_build_contract_rejects_auto_activation() -> None:
    assert IndexBuildRequest().activate_on_success is False
    with pytest.raises(ValidationError):
        IndexBuildRequest(activate_on_success=True)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["evil\r\nX-Injected: yes.docx", "bad\x00name.docx"])
def test_stored_asset_names_reject_response_header_control_characters(value: str) -> None:
    with pytest.raises(ApiError) as raised:
        reject_control_characters(value, field_name="文件名")

    assert raised.value.code == "VALIDATION_ERROR"


def test_index_delete_blocker_error_names_live_references() -> None:
    with pytest.raises(ApiError) as raised:
        IngestionService._raise_index_delete_blocked(
            {
                "status": "DEPRECATED",
                "active_index_pointer_count": 0,
                "rag_active_pointer_count": 0,
                "support_active_pointer_count": 1,
                "support_active_release_count": 1,
                "open_release_count": 1,
                "active_task_count": 0,
                "frozen_conversation_count": 12,
            }
        )

    assert raised.value.code == "INDEX_REFERENCE_PROTECTED"
    assert raised.value.status_code == 409
    assert raised.value.details["support_active_pointer_count"] == 1
    assert "Support 在线索引" in raised.value.message
    assert "12 个冻结会话" in raised.value.message


def test_index_delete_blocker_error_explains_non_deletable_status() -> None:
    with pytest.raises(ApiError) as raised:
        IngestionService._raise_index_delete_blocked(
            {
                "status": "READY",
                "active_index_pointer_count": 0,
                "rag_active_pointer_count": 0,
                "support_active_pointer_count": 0,
                "support_active_release_count": 0,
                "open_release_count": 0,
                "active_task_count": 0,
                "frozen_conversation_count": 0,
            }
        )

    assert raised.value.code == "INDEX_NOT_DELETABLE"
    assert "当前状态为 READY" in raised.value.message


class _FakeFrozenIndexRepository:
    """Simulates a DEPRECATED index protected by one frozen conversation."""

    def __init__(self) -> None:
        self.frozen_conversations = 1
        self.release_calls: list[UUID] = []

    async def get_index(self, index_id: UUID) -> dict[str, Any]:
        return {"id": str(index_id), "status": "DEPRECATED"}

    async def release_frozen_conversation_references(self, index_id: UUID) -> int:
        self.release_calls.append(index_id)
        released = self.frozen_conversations
        self.frozen_conversations = 0
        return released

    async def mark_index_deleting(self, index_id: UUID) -> dict[str, Any] | None:
        if self.frozen_conversations:
            return None
        return {"id": str(index_id), "status": "DELETING"}

    async def get_index_deletion_blockers(self, index_id: UUID) -> dict[str, Any]:
        return {
            "status": "DEPRECATED",
            "active_index_pointer_count": 0,
            "rag_active_pointer_count": 0,
            "support_active_pointer_count": 0,
            "support_active_release_count": 0,
            "open_release_count": 0,
            "active_task_count": 0,
            "frozen_conversation_count": self.frozen_conversations,
        }

    async def list_image_assets_for_index(self, index_id: UUID) -> list[dict[str, str]]:
        return []

    async def delete_index(self, index_id: UUID) -> bool:
        return self.frozen_conversations == 0


@pytest.mark.asyncio
async def test_delete_index_force_releases_frozen_conversation() -> None:
    repository = _FakeFrozenIndexRepository()
    service = IngestionService(
        cast(IngestionRepository, repository),
        cast(Any, _FakeMinio()),
        Settings(_env_file=None, app_env="test", m2_embedding_provider="fake"),
    )
    index_id = uuid4()

    with pytest.raises(ApiError) as raised:
        await service.delete_index(index_id)

    assert raised.value.code == "INDEX_REFERENCE_PROTECTED"
    assert "1 个冻结会话" in raised.value.message
    assert repository.release_calls == []

    result = await service.delete_index(index_id, force=True)

    assert result["deleted_count"] == 1
    assert result["released_conversation_count"] == 1
    assert repository.release_calls == [index_id]


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


def test_parent_chunks_never_empty_when_image_chunk_absorbs_adjacent_text() -> None:
    long_ocr = "图片文字：" + "屏幕内容 " * 1_500
    elements = [
        ParsedElement("TEXT", "步骤说明。", []),
        ParsedElement(
            "IMAGE",
            long_ocr,
            [],
            image_bytes=b"img-1",
            image_mime_type="image/png",
        ),
        ParsedElement("TEXT", "确认状态。", []),
        ParsedElement(
            "IMAGE",
            long_ocr,
            [],
            image_bytes=b"img-2",
            image_mime_type="image/png",
        ),
        ParsedElement("TEXT", "完成。", []),
    ]

    chunks = build_chunks(elements, target_chars=100, max_chars=300, overlap_paragraphs=1)
    parents = build_parent_chunks(elements, chunks, max_chars=8_000)

    assert parents
    assert all(parent.content for parent in parents)
    assert all(parent.token_count > 0 for parent in parents)
    assert all(parent.element_indexes for parent in parents)
    for parent in parents:
        covered = {
            index
            for child_index in parent.child_indexes
            for index in chunks[child_index].element_indexes
        }
        assert covered <= set(parent.element_indexes)


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
        self.recovered: list[tuple[Any, Any]] = []

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

    async def recover_failed_enqueue(self, index_id: Any, task_id: Any) -> bool:
        self.recovered.append((index_id, task_id))
        return True

    async def mark_build_enqueue_failed(self, *_: Any) -> None:
        raise AssertionError("replayed enqueue should succeed in this test")


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


class _FakeRenameRepository:
    def __init__(self) -> None:
        self.call: tuple[UUID, str] | None = None

    async def rename_document(self, document_id: UUID, display_name: str) -> dict[str, Any]:
        self.call = (document_id, display_name)
        return {"id": str(document_id), "display_name": display_name}


class _FakeKnowledgeDeleteRepository:
    def __init__(self) -> None:
        self.knowledge_id = uuid4()
        self.cleaned: list[tuple[str, str]] = []
        self.finalized = False

    async def request_knowledge_base_deletion(self, knowledge_id: Any) -> dict[str, Any]:
        assert knowledge_id == self.knowledge_id
        return {
            "task_id": uuid4(),
            "task_type": "KNOWLEDGE_BASE_DELETE",
            "status": "QUEUED",
            "replayed": False,
        }

    async def list_knowledge_base_cleanup_objects(self, _task_id: Any) -> list[dict[str, str]]:
        return [
            {"minio_bucket": "rag-documents", "minio_object_key": "one.docx"},
            {"minio_bucket": "rag-images", "minio_object_key": "one.png"},
        ]

    async def mark_knowledge_base_cleanup_object_deleted(
        self, _task_id: Any, bucket: str, object_key: str
    ) -> None:
        self.cleaned.append((bucket, object_key))

    async def mark_knowledge_base_cleanup_object_failed(self, *_: Any) -> None:
        return None

    async def finalize_knowledge_base_deletion(self, knowledge_id: Any) -> bool:
        assert knowledge_id == self.knowledge_id
        self.finalized = True
        return True


class _FakeTaskRecords:
    def __init__(self) -> None:
        self.progress_updates: list[dict[str, Any]] = []

    async def progress(self, _task_id: Any, **kwargs: Any) -> None:
        self.progress_updates.append(kwargs)


class _RepositoryResult:
    def __init__(
        self,
        *,
        row: dict[str, Any] | None = None,
        rows: list[dict[str, Any]] | None = None,
        scalar: Any = None,
    ) -> None:
        self.row = row
        self.rows = rows or []
        self.scalar = scalar

    def mappings(self) -> _RepositoryResult:
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        return self.row

    def one(self) -> dict[str, Any]:
        assert self.row is not None
        return self.row

    def scalar_one_or_none(self) -> Any:
        return self.scalar

    def __iter__(self) -> Any:
        return iter(self.rows)


class _RepositoryConnection:
    def __init__(self, results: list[_RepositoryResult]) -> None:
        self.results = list(results)
        self.executed: list[str] = []

    async def __aenter__(self) -> _RepositoryConnection:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def execute(self, statement: Any, _parameters: dict[str, Any]) -> _RepositoryResult:
        self.executed.append(str(statement))
        return self.results.pop(0) if self.results else _RepositoryResult()


class _RepositoryEngine:
    def __init__(self, results: list[_RepositoryResult]) -> None:
        self.connection = _RepositoryConnection(results)

    def begin(self) -> _RepositoryConnection:
        return self.connection


class _FakeMinio:
    def __init__(self) -> None:
        self.uploaded = b""

    def put_object(self, bucket: str, object_key: str, source: Any, length: int, **_: Any) -> None:
        assert bucket == "rag-documents"
        assert object_key.endswith("source.docx")
        self.uploaded = source.read(length)

    def remove_object(self, bucket: str, object_key: str) -> None:
        raise AssertionError(f"unexpected removal: {bucket}/{object_key}")


class _RecordingMinio:
    def __init__(self, *, fail_object_key: str | None = None) -> None:
        self.fail_object_key = fail_object_key
        self.removed: list[tuple[str, str]] = []

    def remove_object(self, bucket: str, object_key: str) -> None:
        self.removed.append((bucket, object_key))
        if object_key == self.fail_object_key:
            raise RuntimeError("storage unavailable")


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

    try:
        response = await service.upload_document(
            knowledge_id=uuid4(),
            upload=upload,
            display_name="上传测试",
            request_build=False,
            idempotency_key="upload-test",
        )
    finally:
        await upload.close()

    assert repository.created
    assert minio.uploaded == path.read_bytes()
    assert response["build_request"]["requested"] is False


@pytest.mark.asyncio
async def test_upload_idempotency_replay_retries_failed_queue_enqueue(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "upload.docx"
    create_docx_fixture(path)
    index_id = uuid4()
    task_id = uuid4()
    replay = {
        "document": {"id": str(uuid4())},
        "build_request": {
            "requested": True,
            "coalesced": False,
            "index_id": str(index_id),
            "task_id": str(task_id),
            "rebuild_required": False,
        },
    }
    repository = _FakeUploadRepository(replay)
    minio = _FakeMinio()
    enqueued: list[tuple[Any, Any]] = []

    def fake_enqueue(_: Any, queued_index_id: Any, queued_task_id: Any) -> None:
        enqueued.append((queued_index_id, queued_task_id))

    monkeypatch.setattr("app.services.ingestion.enqueue_index_build", fake_enqueue)
    service = IngestionService(
        cast(IngestionRepository, repository),
        cast(Any, minio),
        Settings(_env_file=None, app_env="test", m2_embedding_provider="fake"),
    )

    response = await service.upload_document(
        knowledge_id=uuid4(),
        upload=UploadFile(filename=path.name, file=BytesIO(path.read_bytes())),
        display_name="上传测试",
        request_build=True,
        idempotency_key="same-upload-key",
    )

    assert response == replay
    assert not repository.created
    assert not minio.uploaded
    assert repository.recovered == [(index_id, task_id)]
    assert enqueued == [(index_id, task_id)]


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


@pytest.mark.asyncio
async def test_rename_document_normalizes_name_without_rebuilding() -> None:
    repository = _FakeRenameRepository()
    service = IngestionService(
        cast(IngestionRepository, repository),
        cast(Any, _FakeMinio()),
        Settings(_env_file=None, app_env="test"),
    )
    document_id = uuid4()

    response = await service.rename_document(document_id, "  升级操作手册  ")

    assert repository.call == (document_id, "升级操作手册")
    assert response == {"id": str(document_id), "display_name": "升级操作手册"}


@pytest.mark.asyncio
async def test_rename_document_rejects_blank_or_control_characters() -> None:
    repository = _FakeRenameRepository()
    service = IngestionService(
        cast(IngestionRepository, repository),
        cast(Any, _FakeMinio()),
        Settings(_env_file=None, app_env="test"),
    )

    for name in ("   ", "bad\nname"):
        with pytest.raises(ApiError) as raised:
            await service.rename_document(uuid4(), name)
        assert raised.value.code == "VALIDATION_ERROR"
    assert repository.call is None


def test_markdown_document_title_is_bold_and_escapes_untrusted_markup() -> None:
    assert markdown_document_title("升级手册") == "**《升级手册》**"
    assert markdown_document_title("a*b_[c]") == r"**《a\*b\_\[c\]》**"
    assert (
        emphasize_document_title_mentions(
            "请查看《升级手册》，已有 **《升级手册》** 不重复处理。", ["升级手册"]
        )
        == "请查看 **《升级手册》**，已有 **《升级手册》** 不重复处理。"
    )


@pytest.mark.asyncio
async def test_knowledge_base_delete_worker_removes_storage_before_database_purge() -> None:
    repository = _FakeKnowledgeDeleteRepository()
    minio = _RecordingMinio()
    service = IngestionService(
        cast(IngestionRepository, repository),
        cast(Any, minio),
        Settings(_env_file=None, app_env="test"),
    )

    task_id = uuid4()
    records = _FakeTaskRecords()
    response = await service.execute_knowledge_base_deletion(
        {
            "id": task_id,
            "metadata": {"knowledge_id": str(repository.knowledge_id), "cleanup_total": 2},
        },
        cast(Any, records),
    )

    assert response == {
        "knowledge_id": str(repository.knowledge_id),
        "deleted": True,
        "cleanup_completed": 2,
        "cleanup_total": 2,
    }
    assert minio.removed == [
        ("rag-documents", "one.docx"),
        ("rag-images", "one.png"),
    ]
    assert repository.cleaned == minio.removed
    assert repository.finalized is True


@pytest.mark.asyncio
async def test_knowledge_base_delete_worker_keeps_database_when_storage_cleanup_fails() -> None:
    repository = _FakeKnowledgeDeleteRepository()
    minio = _RecordingMinio(fail_object_key="one.png")
    service = IngestionService(
        cast(IngestionRepository, repository),
        cast(Any, minio),
        Settings(_env_file=None, app_env="test"),
    )

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await service.execute_knowledge_base_deletion(
            {
                "id": uuid4(),
                "metadata": {"knowledge_id": str(repository.knowledge_id), "cleanup_total": 2},
            },
            cast(Any, _FakeTaskRecords()),
        )

    assert repository.finalized is False


@pytest.mark.asyncio
async def test_knowledge_base_deletion_request_creates_durable_cleanup_work() -> None:
    knowledge_id = uuid4()
    engine = _RepositoryEngine(
        [
            _RepositoryResult(
                row={
                    "id": knowledge_id,
                    "name": "测试库",
                    "status": "ENABLED",
                    "has_building_index": False,
                }
            ),
            _RepositoryResult(),
            _RepositoryResult(
                rows=[
                    {"minio_bucket": "rag-documents", "minio_object_key": "one.docx"},
                    {"minio_bucket": "rag-images", "minio_object_key": "one.png"},
                ]
            ),
        ]
    )
    repository = IngestionRepository(
        cast(AsyncEngine, engine), Settings(_env_file=None, app_env="test")
    )

    result = await repository.request_knowledge_base_deletion(knowledge_id)

    assert result["task_type"] == "KNOWLEDGE_BASE_DELETE"
    assert result["status"] == "QUEUED"
    sql = engine.connection.executed
    assert "INSERT INTO task_record" in sql[3]
    assert "INSERT INTO storage_cleanup_object" in sql[4]
    assert "status = 'DELETING'" in sql[5]


@pytest.mark.asyncio
async def test_delete_knowledge_base_repository_rejects_an_active_build() -> None:
    knowledge_id = uuid4()
    engine = _RepositoryEngine(
        [
            _RepositoryResult(
                row={
                    "id": knowledge_id,
                    "name": "测试库",
                    "status": "ENABLED",
                    "has_building_index": True,
                }
            )
        ]
    )
    repository = IngestionRepository(
        cast(AsyncEngine, engine), Settings(_env_file=None, app_env="test")
    )

    with pytest.raises(ApiError) as raised:
        await repository.request_knowledge_base_deletion(knowledge_id)

    assert raised.value.code == "KNOWLEDGE_BASE_BUILD_IN_PROGRESS"
    assert raised.value.status_code == 409
    assert len(engine.connection.executed) == 1


@pytest.mark.asyncio
async def test_rename_document_updates_only_display_metadata() -> None:
    knowledge_id = uuid4()
    document_id = uuid4()
    now = datetime.now(UTC)
    engine = _RepositoryEngine(
        [
            _RepositoryResult(scalar=knowledge_id),
            _RepositoryResult(scalar="ENABLED"),
            _RepositoryResult(scalar=document_id),
            _RepositoryResult(scalar=None),
            _RepositoryResult(
                row={
                    "id": document_id,
                    "kb_id": knowledge_id,
                    "filename": "source.docx",
                    "display_name": "新名称",
                    "file_hash": "a" * 64,
                    "file_size": 123,
                    "status": "STORED",
                    "created_at": now,
                    "updated_at": now,
                }
            ),
        ]
    )
    repository = IngestionRepository(
        cast(AsyncEngine, engine), Settings(_env_file=None, app_env="test")
    )

    response = await repository.rename_document(document_id, "新名称")

    assert response["display_name"] == "新名称"
    sql = "\n".join(engine.connection.executed)
    assert "SET display_name = :display_name" in sql
    assert "knowledge_index" not in sql
    assert "document_chunk" not in sql


@pytest.mark.asyncio
async def test_rename_document_rejects_duplicate_name_in_knowledge_base() -> None:
    knowledge_id = uuid4()
    document_id = uuid4()
    engine = _RepositoryEngine(
        [
            _RepositoryResult(scalar=knowledge_id),
            _RepositoryResult(scalar="ENABLED"),
            _RepositoryResult(scalar=document_id),
            _RepositoryResult(scalar=uuid4()),
        ]
    )
    repository = IngestionRepository(
        cast(AsyncEngine, engine), Settings(_env_file=None, app_env="test")
    )

    with pytest.raises(ApiError) as raised:
        await repository.rename_document(document_id, "已有名称")

    assert raised.value.code == "DOCUMENT_NAME_CONFLICT"
    assert all("UPDATE document_source" not in sql for sql in engine.connection.executed)


def test_image_chunk_search_text_excludes_caption() -> None:
    # Vision captions are low-trust matching signals: they inflated document
    # routing toward components the body text never covers (2026-08-31 trace:
    # "压力释放器" matched a "压力传感器" caption). Captions must stay in
    # `content` (generation context) but disappear from `search_text` (BM25).
    elements = [
        ParsedElement("TEXT", "打开FFC胶囊，检查接触点是否正常。", ["故障处理"]),
        ParsedElement(
            "IMAGE",
            "图片描述：黑色圆柱形压力传感器特写",
            ["故障处理"],
        ),
        ParsedElement("TEXT", "清洁完成后重新安装。", ["故障处理"]),
    ]

    chunks = build_chunks(elements, target_chars=100, max_chars=300)

    mixed = next(chunk for chunk in chunks if chunk.chunk_type == "MIXED")
    assert "压力传感器" in mixed.content
    assert "图片：" in mixed.content
    assert "压力传感器" not in mixed.search_text
    assert "图片：" not in mixed.search_text
    assert "打开FFC胶囊" in mixed.search_text
    assert "清洁完成后重新安装" in mixed.search_text

    image_only = [
        ParsedElement(
            "IMAGE",
            "图片描述：接口细节图",
            ["附件"],
        )
    ]
    image_chunks = build_chunks(image_only, target_chars=100, max_chars=300)
    assert image_chunks[0].chunk_type == "IMAGE"
    assert "接口细节图" in image_chunks[0].content
    assert "接口细节图" not in image_chunks[0].search_text
