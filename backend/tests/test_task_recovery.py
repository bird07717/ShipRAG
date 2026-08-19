from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest
from rq.job import JobStatus

from app.core.config import Settings
from app.ingestion.repository import BuildReference, IngestionRepository
from app.tasks import recovery


class _RecoveryRepository:
    def __init__(self) -> None:
        self.task_id = uuid4()
        self.index_id = uuid4()
        self.kb_id = uuid4()
        self.task_status = "RUNNING"
        self.index_status = "BUILDING"
        self.error_code: str | None = None
        self.followup: BuildReference | None = None
        self.followup_calls: list[Any] = []

    async def list_running_index_build_tasks(self) -> list[dict[str, Any]]:
        if self.task_status not in {"QUEUED", "RUNNING"} or self.index_status != "BUILDING":
            return []
        return [{"task_id": self.task_id, "index_id": self.index_id, "kb_id": self.kb_id}]

    async def mark_abandoned_index_build_failed(
        self, index_id: Any, task_id: Any, *, rq_status: str
    ) -> bool:
        assert index_id == self.index_id
        assert task_id == self.task_id
        assert rq_status == "failed"
        self.task_status = "FAILED"
        self.index_status = "FAILED"
        self.error_code = "ABANDONED_JOB"
        return True

    async def create_followup_build_if_needed(self, knowledge_id: Any) -> BuildReference | None:
        self.followup_calls.append(knowledge_id)
        return self.followup

    async def retry(self) -> bool:
        return self.index_status == "FAILED"


@pytest.mark.asyncio
async def test_worker_restart_reconciles_abandoned_build_and_allows_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _RecoveryRepository()

    def no_held_jobs(_: Any) -> set[str]:
        return set()

    def failed_job_status(_: Any, __: Any) -> JobStatus:
        return JobStatus.FAILED

    monkeypatch.setattr(recovery, "_held_job_ids", no_held_jobs)
    monkeypatch.setattr(recovery, "_job_status", failed_job_status)

    recovered = await recovery.reconcile_abandoned_index_builds(
        Settings(_env_file=None, app_env="test"),
        cast(Any, object()),
        repository=cast(IngestionRepository, repository),
    )

    assert recovered == 1
    assert repository.task_status == "FAILED"
    assert repository.index_status == "FAILED"
    assert repository.error_code == "ABANDONED_JOB"
    assert repository.followup_calls == [repository.kb_id]
    assert await repository.retry() is True


@pytest.mark.asyncio
async def test_worker_restart_also_reconciles_job_failed_before_task_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _RecoveryRepository()
    repository.task_status = "QUEUED"
    monkeypatch.setattr(recovery, "_held_job_ids", lambda _: set())
    monkeypatch.setattr(recovery, "_job_status", lambda *_: JobStatus.FAILED)

    recovered = await recovery.reconcile_abandoned_index_builds(
        Settings(_env_file=None, app_env="test"),
        cast(Any, object()),
        repository=cast(IngestionRepository, repository),
    )

    assert recovered == 1
    assert repository.task_status == "FAILED"
    assert repository.index_status == "FAILED"
    assert repository.followup_calls == [repository.kb_id]


@pytest.mark.asyncio
async def test_recovery_does_not_touch_job_held_by_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _RecoveryRepository()

    def held_job(_: Any) -> set[str]:
        return {str(repository.task_id)}

    monkeypatch.setattr(recovery, "_held_job_ids", held_job)

    recovered = await recovery.reconcile_abandoned_index_builds(
        Settings(_env_file=None, app_env="test"),
        cast(Any, object()),
        repository=cast(IngestionRepository, repository),
    )

    assert recovered == 0
    assert repository.task_status == "RUNNING"
    assert repository.index_status == "BUILDING"
    assert repository.followup_calls == []


@pytest.mark.asyncio
async def test_worker_restart_triggers_followup_build_when_rebuild_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _RecoveryRepository()
    followup = BuildReference(True, False, uuid4(), uuid4(), False)
    repository.followup = followup

    enqueued: list[tuple[Any, Any, Any]] = []

    def fake_enqueue(settings: Any, index_id: Any, task_id: Any) -> None:
        enqueued.append((settings, index_id, task_id))

    settings = Settings(_env_file=None, app_env="test")
    monkeypatch.setattr(recovery, "_held_job_ids", lambda _: set())
    monkeypatch.setattr(recovery, "_job_status", lambda *_: JobStatus.FAILED)
    monkeypatch.setattr(recovery, "enqueue_index_build", fake_enqueue)

    recovered = await recovery.reconcile_abandoned_index_builds(
        settings,
        cast(Any, object()),
        repository=cast(IngestionRepository, repository),
    )

    assert recovered == 1
    assert repository.followup_calls == [repository.kb_id]
    assert len(enqueued) == 1
    assert enqueued[0][1] == followup.index_id
    assert enqueued[0][2] == followup.task_id
