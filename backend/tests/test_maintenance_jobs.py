from __future__ import annotations

import base64
import os
from contextlib import asynccontextmanager
from typing import Any, ClassVar
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from app import worker as worker_module
from app.common.errors import ApiError
from app.core.config import Settings
from app.core.secrets import ModelSecretBox
from app.tasks import gc, key_rotation, log_retention, maintenance


def _secret() -> SecretStr:
    return SecretStr(base64.b64encode(os.urandom(32)).decode("ascii"))


class _RotationResult:
    def __init__(self, *, rows: list[dict[str, Any]] | None = None, scalar: int = 0) -> None:
        self.rows = rows or []
        self.scalar = scalar

    def mappings(self) -> _RotationResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self.rows

    def scalar_one(self) -> int:
        return self.scalar


class _RotationConnection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def execute(
        self, statement: Any, params: dict[str, Any] | None = None
    ) -> _RotationResult:
        sql = str(statement)
        self.calls.append((sql, params))
        if sql.startswith("SELECT id"):
            return _RotationResult(rows=self.rows)
        if sql.startswith("SELECT count"):
            return _RotationResult(scalar=2)
        return _RotationResult()


class _RotationDatabase:
    def __init__(self, connection: _RotationConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def begin(self) -> Any:
        yield self.connection


class _Resources:
    def __init__(self, database: Any = None) -> None:
        self.database = database
        self.minio = object()
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_key_rotation_reencrypts_previous_version_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_key = _secret()
    new_key = _secret()
    old_box = ModelSecretBox(old_key, version=1)
    ciphertext, nonce = old_box.encrypt("upstream-api-key")
    model_id = uuid4()
    connection = _RotationConnection(
        [
            {
                "id": str(model_id),
                "api_key_ciphertext": ciphertext,
                "api_key_nonce": nonce,
                "api_key_key_version": 1,
            }
        ]
    )
    resources = _Resources(_RotationDatabase(connection))
    monkeypatch.setattr(key_rotation.AppResources, "create", lambda _: resources)
    settings = Settings(
        _env_file=None,
        app_env="test",
        model_secret_key=new_key,
        model_secret_key_version=2,
        model_secret_key_previous=old_key,
        model_secret_key_previous_version=1,
    )

    result = await key_rotation._rotate(settings)

    assert result == {"rotated": 1, "remaining_on_old_version": 2}
    update_params = connection.calls[1][1]
    assert update_params is not None
    assert update_params["id"] == model_id
    assert update_params["key_version"] == 2
    assert (
        ModelSecretBox(new_key, version=2)
        .decrypt(update_params["ciphertext"], update_params["nonce"], 2)
        .get_secret_value()
        == "upstream-api-key"
    )
    assert resources.closed is True


@pytest.mark.asyncio
async def test_key_rotation_requires_both_keys() -> None:
    with pytest.raises(SystemExit, match="需要同时配置"):
        await key_rotation._rotate(Settings(_env_file=None, app_env="test"))


def test_key_rotation_sync_uses_supplied_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, app_env="test")

    async def fake_rotate(received: Settings) -> dict[str, Any]:
        assert received is settings
        return {"rotated": 0}

    monkeypatch.setattr(key_rotation, "_rotate", fake_rotate)
    assert key_rotation.run_key_rotation_sync(settings) == {"rotated": 0}


@pytest.mark.asyncio
async def test_log_retention_prunes_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = _Resources(object())

    class Repository:
        def __init__(self, database: Any) -> None:
            assert database is resources.database

        async def delete_expired(self, *, retention_days: int) -> int:
            assert retention_days == 17
            return 4

    monkeypatch.setattr(log_retention.AppResources, "create", lambda _: resources)
    monkeypatch.setattr(log_retention, "ObservabilityRepository", Repository)
    settings = Settings(_env_file=None, app_env="test", request_error_log_retention_days=17)

    assert await log_retention._run_prune(settings) == {"deleted_count": 4}
    assert resources.closed is True


def test_log_retention_sync_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, app_env="test")

    async def fake_prune(received: Settings) -> dict[str, Any]:
        assert received is settings
        return {"deleted_count": 1}

    monkeypatch.setattr(log_retention, "_run_prune", fake_prune)
    assert log_retention.run_request_error_log_retention_sync(settings) == {"deleted_count": 1}


@pytest.mark.asyncio
async def test_log_retention_disabled_does_not_open_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        log_retention.AppResources,
        "create",
        lambda _: pytest.fail("disabled retention must not open resources"),
    )
    settings = Settings(_env_file=None, app_env="test", request_error_log_retention_days=0)

    assert await log_retention._run_prune(settings) == {"deleted_count": 0}


class _LogRetentionRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[tuple[str, dict[str, Any]]] = []
        self.deleted: list[str] = []

    def set(self, key: str, value: str, **kwargs: Any) -> bool:
        self.set_calls.append((key, kwargs))
        if kwargs.get("nx") and key in self.values:
            return False
        self.values[key] = value
        return True

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.values.pop(key, None)


class _LogRetentionQueue:
    instances: ClassVar[list[_LogRetentionQueue]] = []

    def __init__(self, name: str, *, connection: Any) -> None:
        self.name = name
        self.connection = connection
        self.enqueued: list[tuple[Any, dict[str, Any]]] = []
        self.__class__.instances.append(self)

    def enqueue(self, function: Any, **kwargs: Any) -> None:
        self.enqueued.append((function, kwargs))


def test_log_retention_startup_sets_one_day_gate() -> None:
    connection = _LogRetentionRedis()

    log_retention.mark_request_error_log_retention_started(connection, 3_600)

    assert connection.set_calls == [(log_retention._DAILY_GATE_KEY, {"ex": 3_600})]


def test_log_retention_daily_gate_enqueues_one_off_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _LogRetentionQueue.instances = []
    connection = _LogRetentionRedis()
    monkeypatch.setattr(log_retention, "Queue", _LogRetentionQueue)

    assert log_retention.enqueue_due_request_error_log_retention(connection, 3_600)
    assert not log_retention.enqueue_due_request_error_log_retention(connection, 3_600)
    connection.values.pop(log_retention._DAILY_GATE_KEY)  # Simulate TTL expiry after downtime.
    assert log_retention.enqueue_due_request_error_log_retention(connection, 3_600)

    queue = _LogRetentionQueue.instances[0]
    assert queue.name == "maintenance"
    assert len(queue.enqueued) == 1
    assert len(_LogRetentionQueue.instances[1].enqueued) == 1
    function, kwargs = queue.enqueued[0]
    assert function is log_retention.process_request_error_log_retention
    assert kwargs["job_timeout"] == 600
    assert connection.set_calls[0][1] == {"nx": True, "ex": 3_600}


def test_log_retention_enqueue_failure_releases_daily_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _LogRetentionRedis()
    monkeypatch.setattr(log_retention, "Queue", _LogRetentionQueue)
    original_enqueue = _LogRetentionQueue.enqueue

    def fail(*_: Any, **_kwargs: Any) -> None:
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(_LogRetentionQueue, "enqueue", fail)
    with pytest.raises(RuntimeError, match="queue unavailable"):
        log_retention.enqueue_due_request_error_log_retention(connection, 60)
    assert connection.deleted == [log_retention._DAILY_GATE_KEY]
    monkeypatch.setattr(_LogRetentionQueue, "enqueue", original_enqueue)
    assert log_retention.enqueue_due_request_error_log_retention(connection, 60)


def test_log_retention_one_off_task_runs_without_rescheduling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, app_env="test")
    calls: list[Settings] = []
    monkeypatch.setattr(log_retention, "get_settings", lambda: settings)
    monkeypatch.setattr(
        log_retention,
        "run_request_error_log_retention_sync",
        lambda received: calls.append(received) or {"deleted_count": 3},
    )
    monkeypatch.setattr(
        log_retention,
        "Queue",
        lambda *_args, **_kwargs: pytest.fail("task must not schedule another job"),
    )

    assert log_retention.process_request_error_log_retention() == {"deleted_count": 3}
    assert log_retention.process_periodic_request_error_log_retention(60) == {"deleted_count": 3}
    assert calls == [settings, settings]


def test_log_retention_one_off_failure_does_not_schedule_another_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, app_env="test")
    monkeypatch.setattr(log_retention, "get_settings", lambda: settings)

    def fail(_: Settings) -> dict[str, Any]:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(log_retention, "run_request_error_log_retention_sync", fail)
    monkeypatch.setattr(
        log_retention,
        "Queue",
        lambda *_args, **_kwargs: pytest.fail("task must not schedule another job"),
    )
    with pytest.raises(RuntimeError, match="database unavailable"):
        log_retention.process_request_error_log_retention()


def test_log_retention_one_off_task_disabled_does_not_prune(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, app_env="test", request_error_log_retention_days=0)
    monkeypatch.setattr(log_retention, "get_settings", lambda: settings)
    monkeypatch.setattr(log_retention.AppResources, "create", lambda _: pytest.fail("disabled"))

    assert log_retention.process_request_error_log_retention() == {"deleted_count": 0}


@pytest.mark.parametrize("retention_days", [0, 30])
def test_maintenance_worker_rechecks_daily_retention(
    monkeypatch: pytest.MonkeyPatch,
    retention_days: int,
) -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        request_error_log_retention_days=retention_days,
    )
    calls: list[str] = []
    worker = object.__new__(worker_module.MaintenanceWorker)
    worker.connection = object()
    monkeypatch.setattr(
        worker_module.Worker,
        "run_maintenance_tasks",
        lambda _: calls.append("base"),
    )
    monkeypatch.setattr(worker_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        worker_module,
        "enqueue_due_request_error_log_retention",
        lambda _: calls.append("enqueue") or True,
    )

    worker.run_maintenance_tasks()

    assert calls == (["base", "enqueue"] if retention_days else ["base"])


def test_maintenance_worker_retries_after_enqueue_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, app_env="test")
    worker = object.__new__(worker_module.MaintenanceWorker)
    worker.connection = object()
    attempts: list[int] = []
    monkeypatch.setattr(worker_module.Worker, "run_maintenance_tasks", lambda _: None)
    monkeypatch.setattr(worker_module, "get_settings", lambda: settings)

    def enqueue(_: Any) -> bool:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("temporary queue error")
        return True

    monkeypatch.setattr(worker_module, "enqueue_due_request_error_log_retention", enqueue)

    worker.run_maintenance_tasks()
    worker.run_maintenance_tasks()

    assert len(attempts) == 2


@pytest.mark.parametrize("retention_days", [0, 30])
def test_maintenance_worker_marks_log_retention_after_startup_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    retention_days: int,
) -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        rq_queues="maintenance",
        index_gc_enabled=False,
        request_error_log_retention_days=retention_days,
        assistant_request_retention_days=0,
        assistant_request_stale_seconds=0,
    )
    connection = object()
    calls: list[str] = []
    scheduler: list[bool] = []

    class FakeWorker:
        def __init__(self, queues: Any, *, connection: Any, name: str) -> None:
            assert queues == ["maintenance"]

        def clean_registries(self) -> None:
            pass

        def work(self, *, with_scheduler: bool) -> None:
            scheduler.append(with_scheduler)

    async def no_recovery(*_: Any) -> int:
        return 0

    monkeypatch.setattr(worker_module, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_module, "configure_logging", lambda _: None)
    monkeypatch.setattr(worker_module.Redis, "from_url", lambda _: connection)
    monkeypatch.setattr(worker_module, "Queue", lambda name, *, connection: name)
    monkeypatch.setattr(worker_module, "MaintenanceWorker", FakeWorker)
    monkeypatch.setattr(worker_module, "reconcile_abandoned_maintenance_tasks", no_recovery)
    monkeypatch.setattr(
        worker_module,
        "run_request_error_log_retention_sync",
        lambda _: calls.append("startup") or {"deleted_count": 0},
    )
    monkeypatch.setattr(
        worker_module,
        "mark_request_error_log_retention_started",
        lambda _: calls.append("mark"),
    )

    worker_module.main()

    assert calls == (["startup", "mark"] if retention_days else [])
    assert scheduler == [False]


class _Records:
    def __init__(
        self,
        task: dict[str, Any] | None,
        *,
        started: bool = True,
        current: dict[str, Any] | None = None,
    ) -> None:
        self.task = task
        self.started = started
        self.current = current
        self.get_calls = 0
        self.failed: Exception | None = None
        self.succeeded: dict[str, Any] | None = None
        self.stage = ""

    async def get(self, task_id: UUID) -> dict[str, Any] | None:
        self.get_calls += 1
        return self.task if self.get_calls == 1 else self.current

    async def start(self, task_id: UUID, *, stage: str) -> bool:
        self.stage = stage
        return self.started

    async def fail(self, task_id: UUID, error: Exception) -> None:
        self.failed = error

    async def succeed(self, task_id: UUID, *, metadata: dict[str, Any]) -> None:
        self.succeeded = metadata


def _install_maintenance_dependencies(
    monkeypatch: pytest.MonkeyPatch, records: _Records
) -> _Resources:
    settings = Settings(_env_file=None, app_env="test")
    resources = _Resources(object())
    monkeypatch.setattr(maintenance, "get_settings", lambda: settings)
    monkeypatch.setattr(maintenance.AppResources, "create", lambda _: resources)
    monkeypatch.setattr(maintenance, "TaskRecordRepository", lambda _: records)
    return resources


@pytest.mark.asyncio
async def test_maintenance_rejects_missing_task_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = _install_maintenance_dependencies(monkeypatch, _Records(None))

    with pytest.raises(ApiError, match="后台任务不存在"):
        await maintenance._run_maintenance_task(uuid4())
    assert resources.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["SUCCEEDED", "CANCELLED"])
async def test_maintenance_replays_terminal_task(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    task_id = uuid4()
    records = _Records({"status": status, "task_type": "PROFILE_REFRESH"})
    resources = _install_maintenance_dependencies(monkeypatch, records)

    assert await maintenance._run_maintenance_task(task_id) == {
        "task_id": str(task_id),
        "status": status,
    }
    assert resources.closed is True


@pytest.mark.asyncio
async def test_maintenance_returns_current_status_when_start_loses_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = uuid4()
    records = _Records(
        {"status": "QUEUED", "task_type": "PROFILE_REFRESH"},
        started=False,
        current={"status": "RUNNING"},
    )
    _install_maintenance_dependencies(monkeypatch, records)

    assert await maintenance._run_maintenance_task(task_id) == {
        "task_id": str(task_id),
        "status": "RUNNING",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task_type", "target", "class_name", "expected_stage"),
    [
        (
            "KNOWLEDGE_BASE_DELETE",
            "app.services.ingestion",
            "IngestionService",
            "DELETING_STORAGE",
        ),
        ("PROFILE_REFRESH", "app.releases.service", "ReleaseService", "GENERATING"),
        (
            "DOCUMENT_PROFILE_GENERATE",
            "app.document_profiles.service",
            "DocumentProfileService",
            "GENERATING",
        ),
    ],
)
async def test_maintenance_dispatches_supported_task_types(
    monkeypatch: pytest.MonkeyPatch,
    task_type: str,
    target: str,
    class_name: str,
    expected_stage: str,
) -> None:
    task_id = uuid4()
    task = {"status": "QUEUED", "task_type": task_type}
    records = _Records(task)
    resources = _install_maintenance_dependencies(monkeypatch, records)

    class Service:
        def __init__(self, *args: Any) -> None:
            assert args

        async def execute_knowledge_base_deletion(
            self, received: dict[str, Any], received_records: _Records
        ) -> dict[str, Any]:
            assert received is task and received_records is records
            return {"deleted": 3}

        async def execute_profile_task(
            self, received: dict[str, Any], received_records: _Records
        ) -> dict[str, Any]:
            assert received is task and received_records is records
            return {"generated": 3}

        async def execute_generation_task(
            self, received: dict[str, Any], received_records: _Records
        ) -> dict[str, Any]:
            assert received is task and received_records is records
            return {"generated": 3}

    module = __import__(target, fromlist=[class_name])
    monkeypatch.setattr(module, class_name, Service)

    result = await maintenance._run_maintenance_task(task_id)

    assert result["task_id"] == str(task_id)
    assert records.stage == expected_stage
    assert records.succeeded is not None
    assert resources.closed is True


@pytest.mark.asyncio
async def test_maintenance_marks_unsupported_task_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = uuid4()
    records = _Records({"status": "QUEUED", "task_type": "UNKNOWN"})
    resources = _install_maintenance_dependencies(monkeypatch, records)

    with pytest.raises(ApiError, match="不支持的后台任务类型"):
        await maintenance._run_maintenance_task(task_id)
    assert isinstance(records.failed, ApiError)
    assert resources.closed is True


def test_enqueue_maintenance_task_closes_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    task_id = uuid4()
    calls: dict[str, Any] = {}

    class Connection:
        def close(self) -> None:
            calls["closed"] = True

    connection = Connection()

    class Queue:
        def __init__(self, name: str, *, connection: Any) -> None:
            calls["queue"] = (name, connection)

        def enqueue(self, function: Any, *args: Any, **kwargs: Any) -> None:
            calls["enqueue"] = (function, args, kwargs)

    def from_url(value: str) -> Connection:
        calls["url"] = value
        return connection

    monkeypatch.setattr(maintenance.Redis, "from_url", from_url)
    monkeypatch.setattr(maintenance, "Queue", Queue)
    settings = Settings(_env_file=None, app_env="test")

    maintenance.enqueue_maintenance_task(settings, task_id)

    assert calls["queue"] == ("maintenance", connection)
    function, args, kwargs = calls["enqueue"]
    assert function is maintenance.process_maintenance_task
    assert args == (str(task_id),)
    assert kwargs["job_id"] == str(task_id)
    assert calls["closed"] is True


def test_process_maintenance_task_sync_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    task_id = uuid4()

    async def fake_run(received: UUID) -> dict[str, Any]:
        assert received == task_id
        return {"task_id": str(received), "status": "SUCCEEDED"}

    monkeypatch.setattr(maintenance, "_run_maintenance_task", fake_run)
    assert maintenance.process_maintenance_task(str(task_id))["status"] == "SUCCEEDED"


@pytest.mark.asyncio
async def test_index_gc_runs_service_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = _Resources(object())
    result = {"deleted_count": 1, "deleted_index_ids": ["idx-old"]}

    class Service:
        def __init__(self, repository: Any, minio: Any, settings: Settings) -> None:
            assert repository is not None
            assert minio is resources.minio

        async def collect_garbage(self) -> dict[str, Any]:
            return result

    monkeypatch.setattr(gc.AppResources, "create", lambda _: resources)
    monkeypatch.setattr(gc, "IngestionRepository", lambda *_: object())
    monkeypatch.setattr(gc, "IngestionService", Service)

    assert await gc._run_gc(Settings(_env_file=None, app_env="test")) is result
    assert resources.closed is True


def test_enqueue_index_gc_and_sync_entrypoints_close_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, Any] = {}

    class Connection:
        def close(self) -> None:
            calls["closed"] = True

    connection = Connection()

    class Queue:
        def __init__(self, name: str, *, connection: Any) -> None:
            calls["queue"] = (name, connection)

        def enqueue(self, function: Any, **kwargs: Any) -> None:
            calls["enqueue"] = (function, kwargs)

    monkeypatch.setattr(gc.Redis, "from_url", lambda _: connection)
    monkeypatch.setattr(gc, "Queue", Queue)
    settings = Settings(_env_file=None, app_env="test")
    gc.enqueue_index_gc(settings)
    assert calls["queue"] == ("maintenance", connection)
    assert calls["enqueue"][0] is gc.process_gc_task
    assert calls["closed"] is True

    async def fake_run(received: Settings) -> dict[str, Any]:
        assert received is settings
        return {"deleted_count": 0}

    monkeypatch.setattr(gc, "_run_gc", fake_run)
    monkeypatch.setattr(gc, "get_settings", lambda: settings)
    assert gc.run_index_gc_sync(settings) == {"deleted_count": 0}
    assert gc.process_gc_task() == {"deleted_count": 0}
