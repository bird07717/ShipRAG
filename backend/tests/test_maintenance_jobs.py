from __future__ import annotations

import base64
import os
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

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
