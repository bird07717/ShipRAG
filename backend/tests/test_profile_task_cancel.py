from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from rq.job import JobStatus

from app.common.errors import ApiError
from app.core.config import Settings
from app.document_profiles import generator as generator_module
from app.document_profiles.generator import (
    DocumentProfileGenerator,
    GeneratedProfile,
)
from app.document_profiles.service import DocumentProfileService
from app.ingestion.pipeline import IndexBuildResult
from app.rag.llm import LlmError, LlmProvider
from app.tasks import ingestion as ingestion_tasks
from app.tasks import recovery
from app.tasks.repository import TaskRecordRepository

# ---------------------------------------------------------------------------
# Orphan reconciliation (recovery.reconcile_abandoned_maintenance_tasks)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_build_profile_task_is_linked_to_completed_rag_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    knowledge_id = uuid4()
    index_id = uuid4()
    build_task_id = uuid4()
    merged: dict[str, Any] = {}

    class _IngestionRepository:
        def __init__(self, *_: Any) -> None:
            pass

        async def get_index(self, requested_index_id: Any) -> dict[str, Any]:
            assert requested_index_id == index_id
            return {"kb_id": knowledge_id}

    class _ProfileService:
        def __init__(self, *_: Any) -> None:
            pass

        async def request_generation(self, requested_knowledge_id: Any) -> dict[str, Any]:
            assert requested_knowledge_id == knowledge_id
            return {
                "task_id": "document-profile-task",
                "task_type": "DOCUMENT_PROFILE_GENERATE",
            }

    class _TaskRecords:
        def __init__(self, *_: Any) -> None:
            pass

        async def merge_metadata(self, task_id: Any, metadata: dict[str, Any]) -> None:
            assert task_id == build_task_id
            merged.update(metadata)

    monkeypatch.setattr(ingestion_tasks, "IngestionRepository", _IngestionRepository)
    monkeypatch.setattr(ingestion_tasks, "DocumentProfileService", _ProfileService)
    monkeypatch.setattr(ingestion_tasks, "TaskRecordRepository", _TaskRecords)

    class _Resources:
        database = object()

    await ingestion_tasks._submit_post_build_profile(
        Settings(_env_file=None, app_env="test"),
        _Resources(),  # type: ignore[arg-type]
        IndexBuildResult(
            index_id=index_id,
            document_count=1,
            element_count=1,
            chunk_count=1,
        ),
        build_task_id,
    )

    assert merged == {
        "profile_task_id": "document-profile-task",
        "profile_task_type": "DOCUMENT_PROFILE_GENERATE",
    }


class _MaintenanceRecords:
    """In-memory TaskRecordRepository double for reconciliation tests."""

    def __init__(self, *, status: str, age_seconds: float) -> None:
        self.task_id = uuid4()
        self.kb_id = uuid4()
        self.status = status
        self.updated_at = datetime.now(UTC) - timedelta(seconds=age_seconds)
        self.failed: list[tuple[Any, str]] = []

    async def list_active_by_type(self, task_type: str) -> list[dict[str, Any]]:
        if task_type != "DOCUMENT_PROFILE_GENERATE":
            return []
        if self.status not in {"QUEUED", "RUNNING"}:
            return []
        return [
            {
                "id": self.task_id,
                "status": self.status,
                "kb_id": self.kb_id,
                "created_at": self.updated_at,
                "updated_at": self.updated_at,
            }
        ]

    async def fail(self, task_id: Any, error: Exception) -> None:
        self.failed.append((task_id, str(getattr(error, "code", type(error).__name__))))


async def _reconcile(
    records: _MaintenanceRecords,
    monkeypatch: pytest.MonkeyPatch,
    *,
    held: set[str] | None = None,
    job_status: JobStatus | None = None,
) -> int:
    monkeypatch.setattr(recovery, "_held_job_ids", lambda _: held or set())
    monkeypatch.setattr(recovery, "_job_status", lambda *_: job_status)
    return await recovery.reconcile_abandoned_maintenance_tasks(
        Settings(_env_file=None, app_env="test"),
        cast(Any, object()),
        records=cast(TaskRecordRepository, records),
    )


@pytest.mark.asyncio
async def test_running_task_with_missing_job_is_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _MaintenanceRecords(status="RUNNING", age_seconds=60)

    recovered = await _reconcile(records, monkeypatch, job_status=None)

    assert recovered == 1
    assert records.failed[0][0] == records.task_id
    assert records.failed[0][1] == "ORPHANED_TASK"


@pytest.mark.asyncio
async def test_task_held_by_worker_is_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    records = _MaintenanceRecords(status="RUNNING", age_seconds=60)

    recovered = await _reconcile(records, monkeypatch, held={str(records.task_id)}, job_status=None)

    assert recovered == 0
    assert records.failed == []


@pytest.mark.asyncio
async def test_failed_rq_job_is_reconciled(monkeypatch: pytest.MonkeyPatch) -> None:
    records = _MaintenanceRecords(status="RUNNING", age_seconds=60)

    recovered = await _reconcile(records, monkeypatch, job_status=JobStatus.FAILED)

    assert recovered == 1
    assert records.failed[0][1] == "ORPHANED_TASK"


@pytest.mark.asyncio
async def test_young_queued_task_without_job_gets_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _MaintenanceRecords(status="QUEUED", age_seconds=30)

    recovered = await _reconcile(records, monkeypatch, job_status=None)

    assert recovered == 0
    assert records.failed == []


@pytest.mark.asyncio
async def test_old_queued_task_without_job_is_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _MaintenanceRecords(status="QUEUED", age_seconds=3_600)

    recovered = await _reconcile(records, monkeypatch, job_status=None)

    assert recovered == 1
    assert records.failed[0][1] == "ORPHANED_TASK"


@pytest.mark.asyncio
async def test_healthy_queued_job_is_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    records = _MaintenanceRecords(status="QUEUED", age_seconds=3_600)

    recovered = await _reconcile(records, monkeypatch, job_status=JobStatus.QUEUED)

    assert recovered == 0
    assert records.failed == []


@pytest.mark.asyncio
async def test_stale_heartbeat_with_healthy_job_is_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _MaintenanceRecords(status="RUNNING", age_seconds=3_600)

    recovered = await _reconcile(records, monkeypatch, job_status=JobStatus.STARTED)

    assert recovered == 1
    assert records.failed[0][1] == "TASK_HEARTBEAT_TIMEOUT"


@pytest.mark.asyncio
async def test_fresh_heartbeat_with_healthy_job_is_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _MaintenanceRecords(status="RUNNING", age_seconds=60)

    recovered = await _reconcile(records, monkeypatch, job_status=JobStatus.STARTED)

    assert recovered == 0
    assert records.failed == []


# ---------------------------------------------------------------------------
# Generator retry behaviour
# ---------------------------------------------------------------------------

_PROFILE = GeneratedProfile(problem_code="X_UPGRADE", canonical_question="如何升级？")


class _ScriptedProvider:
    """Raises scripted LlmErrors, then returns schema-appropriate results."""

    def __init__(self, errors: list[LlmError]) -> None:
        self.errors = list(errors)
        self.calls = 0
        self.usage: dict[str, Any] = {}

    async def complete_structured(
        self,
        prompt: str,
        *,
        system: str,
        schema: type,
        max_tokens: int,
        temperature: float,
        timeout_seconds: float | None = None,
        thinking: bool = False,
        thinking_budget: int | None = None,
    ) -> Any:
        del prompt, system, max_tokens, temperature, timeout_seconds, thinking, thinking_budget
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        assert schema is GeneratedProfile
        return _PROFILE

    async def aclose(self) -> None:
        return None


def _document() -> dict[str, Any]:
    return {
        "document_id": str(uuid4()),
        "kb_id": str(uuid4()),
        "display_name": "设备升级指南",
        "filename": "upgrade.docx",
        "content": "升级前请备份数据。错误码 E-100 表示固件过旧。",
    }


@pytest.mark.asyncio
async def test_generator_retries_transient_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(DocumentProfileGenerator, "_RETRY_BACKOFF_SECONDS", (0.0, 0.0))
    settings = Settings(_env_file=None, app_env="test")
    generator = DocumentProfileGenerator(settings)
    provider = _ScriptedProvider([LlmError("UPSTREAM_TIMEOUT", "超时", retryable=True)])

    profile, source, error, quality, _notes = await generator.generate(
        _document(), cast(LlmProvider, provider)
    )

    assert source == "LLM"
    assert error is None
    assert quality == "PASS"
    assert profile.canonical_question == "如何升级？"
    # One draft call + one retry.
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_generator_falls_back_when_retries_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(DocumentProfileGenerator, "_RETRY_BACKOFF_SECONDS", (0.0, 0.0))
    settings = Settings(_env_file=None, app_env="test", profile_llm_retry_attempts=2)
    generator = DocumentProfileGenerator(settings)
    provider = _ScriptedProvider(
        [LlmError("UPSTREAM_TIMEOUT", "超时", retryable=True) for _ in range(5)]
    )

    profile, source, error, quality, notes = await generator.generate(
        _document(), cast(LlmProvider, provider)
    )

    assert source == "FALLBACK"
    assert error == "UPSTREAM_TIMEOUT"
    assert quality == "FALLBACK"
    assert provider.calls == 2
    assert notes


@pytest.mark.asyncio
async def test_generator_does_not_retry_non_retryable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(DocumentProfileGenerator, "_RETRY_BACKOFF_SECONDS", (0.0, 0.0))
    settings = Settings(_env_file=None, app_env="test")
    generator = DocumentProfileGenerator(settings)
    provider = _ScriptedProvider([LlmError("STRUCTURED_OUTPUT_INVALID", "格式", retryable=False)])

    _profile, source, error, _quality, _notes = await generator.generate(
        _document(), cast(LlmProvider, provider)
    )

    assert source == "FALLBACK"
    assert error == "STRUCTURED_OUTPUT_INVALID"
    assert provider.calls == 1


def test_generator_uses_dedicated_profile_timeout() -> None:
    settings = Settings(_env_file=None, app_env="test", profile_llm_timeout_seconds=42.0)
    assert settings.profile_llm_timeout_seconds == 42.0
    assert generator_module.DocumentProfileGenerator(settings).settings is settings


# ---------------------------------------------------------------------------
# execute_generation_task: concurrency, isolation and cooperative cancel
# ---------------------------------------------------------------------------


class _NoLlmSupportRepository:
    def __init__(self, engine: Any, settings: Any) -> None:
        del engine, settings

    async def get_llm(self) -> Any:
        raise ApiError("MODEL_NOT_CONFIGURED", "LLM 模型未配置", 503)


class _GenerationRepository:
    def __init__(
        self,
        documents: list[dict[str, Any]],
        *,
        failing_document_ids: set[str] | None = None,
        on_upsert: Any = None,
    ) -> None:
        self.documents = documents
        self.failing_document_ids = failing_document_ids or set()
        self.on_upsert = on_upsert
        self.engine = object()
        self.upserts: list[str] = []

    async def generation_documents(
        self, knowledge_id: Any, document_id: Any = None
    ) -> list[dict[str, Any]]:
        del knowledge_id, document_id
        return self.documents

    async def upsert_pending(
        self,
        *,
        knowledge_id: Any,
        document_id: Any,
        payload: dict[str, Any],
        source: str,
        confidence: float,
        state: str,
        error_code: str | None = None,
        error_message: str | None = None,
        content_hash: str | None = None,
    ) -> dict[str, Any]:
        del payload, source, confidence, state, error_code, error_message, content_hash
        document_key = str(document_id)
        if document_key in self.failing_document_ids:
            raise ApiError("DOCUMENT_NOT_FOUND", "文档不存在", 404)
        self.upserts.append(document_key)
        if self.on_upsert is not None:
            self.on_upsert()
        return {}


class _TaskRecords:
    def __init__(self, status: str = "RUNNING") -> None:
        self.task_status = status
        self.progress_calls: list[int] = []

    async def status(self, task_id: Any) -> str:
        del task_id
        return self.task_status

    async def progress(
        self,
        task_id: Any,
        *,
        stage: str,
        progress: int,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        del task_id, stage, metadata
        self.progress_calls.append(progress)


def _documents(count: int) -> list[dict[str, Any]]:
    return [
        {
            "document_id": str(uuid4()),
            "kb_id": str(uuid4()),
            "display_name": f"文档 {index}",
            "filename": f"doc-{index}.docx",
            "content": "升级步骤与错误码 E-10x 说明。",
        }
        for index in range(count)
    ]


def _task(knowledge_id: Any) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "metadata": {"knowledge_id": str(knowledge_id)},
        "kb_id": str(knowledge_id),
        "document_id": None,
    }


@pytest.mark.asyncio
async def test_generation_task_processes_all_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.document_profiles.service.SupportRepository", _NoLlmSupportRepository)
    settings = Settings(_env_file=None, app_env="test", profile_generation_concurrency=3)
    documents = _documents(6)
    repository = _GenerationRepository(documents)
    service = DocumentProfileService(cast(Any, repository), settings)

    result = await service.execute_generation_task(_task(documents[0]["kb_id"]), _TaskRecords())

    assert result["document_count"] == 6
    assert result["generated_count"] == 6
    assert result["cancelled"] is False
    assert result["failed_documents"] == []
    assert len(repository.upserts) == 6
    assert result["provider_error"] == "MODEL_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_generation_task_isolates_single_document_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.document_profiles.service.SupportRepository", _NoLlmSupportRepository)
    settings = Settings(_env_file=None, app_env="test", profile_generation_concurrency=2)
    documents = _documents(4)
    failing = {documents[2]["document_id"]}
    repository = _GenerationRepository(documents, failing_document_ids=failing)
    service = DocumentProfileService(cast(Any, repository), settings)

    result = await service.execute_generation_task(_task(documents[0]["kb_id"]), _TaskRecords())

    assert result["generated_count"] == 3
    assert result["cancelled"] is False
    assert len(result["failed_documents"]) == 1
    assert result["failed_documents"][0]["document_id"] in failing
    assert result["failed_documents"][0]["error_code"] == "DOCUMENT_NOT_FOUND"
    assert len(repository.upserts) == 3


@pytest.mark.asyncio
async def test_generation_task_stops_when_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.document_profiles.service.SupportRepository", _NoLlmSupportRepository)
    settings = Settings(_env_file=None, app_env="test", profile_generation_concurrency=1)
    documents = _documents(4)
    records = _TaskRecords()

    def _cancel_after_first_upsert() -> None:
        records.task_status = "CANCELLED"

    repository = _GenerationRepository(documents, on_upsert=_cancel_after_first_upsert)
    service = DocumentProfileService(cast(Any, repository), settings)

    result = await service.execute_generation_task(_task(documents[0]["kb_id"]), records)

    assert result["cancelled"] is True
    assert result["generated_count"] == 1
    assert len(repository.upserts) == 1


@pytest.mark.asyncio
async def test_generation_task_skips_everything_when_cancelled_before_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.document_profiles.service.SupportRepository", _NoLlmSupportRepository)
    settings = Settings(_env_file=None, app_env="test", profile_generation_concurrency=2)
    documents = _documents(3)
    repository = _GenerationRepository(documents)
    service = DocumentProfileService(cast(Any, repository), settings)

    result = await service.execute_generation_task(
        _task(documents[0]["kb_id"]), _TaskRecords(status="CANCELLED")
    )

    assert result["cancelled"] is True
    assert result["generated_count"] == 0
    assert repository.upserts == []


@pytest.mark.asyncio
async def test_generation_task_handles_empty_document_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.document_profiles.service.SupportRepository", _NoLlmSupportRepository)
    settings = Settings(_env_file=None, app_env="test")
    repository = _GenerationRepository([])
    service = DocumentProfileService(cast(Any, repository), settings)

    result = await service.execute_generation_task(_task(uuid4()), _TaskRecords())

    assert result["document_count"] == 0
    assert result["generated_count"] == 0
    assert result["cancelled"] is False
