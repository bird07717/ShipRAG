from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest
from rq.job import JobStatus

from app.core.config import Settings
from app.ingestion.repository import IngestionRepository
from app.tasks import recovery


class _RecoveryRepository:
    def __init__(self) -> None:
        self.task_id = uuid4()
        self.index_id = uuid4()
        self.kb_id = uuid4()
        self.task_status = "RUNNING"
        self.index_status = "BUILDING"
        self.error_code: str | None = None

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
    # ADR-0023：中断构建只标记 FAILED，不再自动排 followup；重建由
    # 管理员通过 rebuild_required 徽标提示后手动触发。
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


@pytest.mark.asyncio
async def test_worker_restart_does_not_auto_requeue_abandoned_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0023：中断构建只标记 FAILED，不自动排任何重建任务。

    rebuild_required 徽标是唯一提示；重建由管理员手动触发。
    """
    repository = _RecoveryRepository()

    enqueued: list[tuple[Any, Any]] = []

    def fail_if_enqueued(*args: Any, **kwargs: Any) -> None:
        enqueued.append((args, kwargs))

    settings = Settings(_env_file=None, app_env="test")
    monkeypatch.setattr(recovery, "_held_job_ids", lambda _: set())
    monkeypatch.setattr(recovery, "_job_status", lambda *_: JobStatus.FAILED)
    monkeypatch.setattr("app.tasks.ingestion.enqueue_index_build", fail_if_enqueued)

    recovered = await recovery.reconcile_abandoned_index_builds(
        settings,
        cast(Any, object()),
        repository=cast(IngestionRepository, repository),
    )

    assert recovered == 1
    assert repository.index_status == "FAILED"
    assert enqueued == []
