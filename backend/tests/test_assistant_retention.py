from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, ClassVar
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.tasks import assistant_retention


class _DeleteResult:
    def __init__(self, rowcount: int | None) -> None:
        self.rowcount = rowcount


class _Connection:
    def __init__(self, rowcount: int | None = 0) -> None:
        self.rowcount = rowcount
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, parameters: dict[str, Any]) -> _DeleteResult:
        self.calls.append((str(statement), parameters))
        return _DeleteResult(self.rowcount)


class _Database:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def begin(self) -> Any:
        yield self.connection


class _Resources:
    def __init__(self, database: _Database) -> None:
        self.database = database
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _RedisConnection:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.closed = False

    def set(self, key: str, value: str, **kwargs: Any) -> bool:
        self.set_calls.append((key, value, kwargs))
        if kwargs.get("nx") and key in self.values:
            return False
        self.values[key] = value
        return True

    def close(self) -> None:
        self.closed = True


class _Queue:
    instances: ClassVar[list[_Queue]] = []

    def __init__(self, name: str, *, connection: Any) -> None:
        self.name = name
        self.connection = connection
        self.enqueued: list[tuple[Any, Any, dict[str, Any]]] = []
        self.__class__.instances.append(self)

    def enqueue_in(self, delay: Any, function: Any, **kwargs: Any) -> None:
        self.enqueued.append((delay, function, kwargs))


@pytest.mark.asyncio
async def test_prune_deletes_only_expired_terminal_orphans_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection(rowcount=4)
    resources = _Resources(_Database(connection))
    monkeypatch.setattr(assistant_retention.AppResources, "create", lambda _: resources)
    settings = Settings(_env_file=None, app_env="test", assistant_request_retention_days=17)

    assert await assistant_retention._run_prune(settings) == {"deleted_count": 4}

    sql, parameters = connection.calls[0]
    assert "conversation_id IS NULL" in sql
    assert "status IN ('COMPLETED', 'FAILED', 'CANCELLED')" in sql
    assert "status = 'RUNNING'" not in sql
    assert "updated_at <" in sql
    assert parameters == {"retention_days": 17}
    assert resources.closed is True


@pytest.mark.asyncio
async def test_prune_disabled_does_not_create_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        assistant_retention.AppResources,
        "create",
        lambda _: pytest.fail("disabled retention must not open resources"),
    )

    result = await assistant_retention._run_prune(
        Settings(_env_file=None, app_env="test", assistant_request_retention_days=0)
    )

    assert result == {"deleted_count": 0}


def test_sync_wrapper_uses_supplied_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, app_env="test")

    async def fake_prune(received: Settings) -> dict[str, Any]:
        assert received is settings
        return {"deleted_count": 2}

    monkeypatch.setattr(assistant_retention, "_run_prune", fake_prune)
    assert assistant_retention.run_assistant_request_retention_sync(settings) == {
        "deleted_count": 2
    }


def test_periodic_registration_uses_single_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    _Queue.instances = []
    connection = _RedisConnection()
    monkeypatch.setattr(assistant_retention, "Queue", _Queue)

    assert assistant_retention.ensure_periodic_assistant_request_retention(connection, 3_600)
    assert not assistant_retention.ensure_periodic_assistant_request_retention(connection, 3_600)

    assert len(_Queue.instances[0].enqueued) == 1
    delay, function, kwargs = _Queue.instances[0].enqueued[0]
    assert delay.total_seconds() == 3_600
    assert function is assistant_retention.process_periodic_assistant_request_retention
    assert kwargs["interval"] == 3_600
    assert connection.set_calls[0][2] == {"nx": True, "ex": 7_200}


def test_reschedule_restores_sentinel_and_closes_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    _Queue.instances = []
    connection = _RedisConnection()
    monkeypatch.setattr(assistant_retention.Redis, "from_url", lambda _: connection)
    monkeypatch.setattr(assistant_retention, "Queue", _Queue)
    settings = Settings(_env_file=None, app_env="test")

    assistant_retention._reschedule_periodic(settings, 60)

    assert _Queue.instances[0].enqueued[0][2]["interval"] == 60
    assert connection.set_calls[-1][2] == {"ex": 120}
    assert connection.closed is True


def test_reschedule_disabled_does_not_open_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        assistant_retention.Redis,
        "from_url",
        lambda _: pytest.fail("disabled retention must not connect to Redis"),
    )
    settings = Settings(_env_file=None, app_env="test", assistant_request_retention_days=0)

    assistant_retention._reschedule_periodic(settings, 60)


def test_periodic_task_runs_and_reschedules(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, app_env="test")
    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(assistant_retention, "get_settings", lambda: settings)
    monkeypatch.setattr(
        assistant_retention,
        "run_assistant_request_retention_sync",
        lambda received: calls.append(("run", received)) or {"deleted_count": 3},
    )
    monkeypatch.setattr(
        assistant_retention,
        "_reschedule_periodic",
        lambda received, interval: calls.append(("reschedule", (received, interval))),
    )

    assert assistant_retention.process_periodic_assistant_request_retention(60) == {
        "deleted_count": 3
    }
    assert calls == [("run", settings), ("reschedule", (settings, 60))]


def test_periodic_task_disabled_does_not_run_or_reschedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, app_env="test", assistant_request_retention_days=0)
    monkeypatch.setattr(assistant_retention, "get_settings", lambda: settings)
    monkeypatch.setattr(
        assistant_retention,
        "run_assistant_request_retention_sync",
        lambda _: pytest.fail("disabled retention must not run"),
    )
    monkeypatch.setattr(
        assistant_retention,
        "_reschedule_periodic",
        lambda *_: pytest.fail("disabled retention must not reschedule"),
    )

    assert assistant_retention.process_periodic_assistant_request_retention(60) == {
        "deleted_count": 0
    }


def test_periodic_task_reschedules_after_prune_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, app_env="test")
    rescheduled: list[int] = []
    monkeypatch.setattr(assistant_retention, "get_settings", lambda: settings)

    def fail(_: Settings) -> dict[str, Any]:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(assistant_retention, "run_assistant_request_retention_sync", fail)
    monkeypatch.setattr(
        assistant_retention,
        "_reschedule_periodic",
        lambda _, interval: rescheduled.append(interval),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        assistant_retention.process_periodic_assistant_request_retention(60)
    assert rescheduled == [60]


@pytest.mark.asyncio
async def test_prune_real_postgres_preserves_running_recent_and_linked_requests() -> None:
    database_url = os.getenv("SUPPORT_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SUPPORT_TEST_DATABASE_URL is not configured")

    engine = create_async_engine(database_url, pool_pre_ping=True)
    knowledge_id = uuid4()
    conversation_id = uuid4()
    expired_orphan_id = uuid4()
    recent_orphan_id = uuid4()
    running_orphan_id = uuid4()
    linked_terminal_id = uuid4()
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            await connection.execute(
                text("INSERT INTO knowledge_base (id, name) VALUES (:id, :name)"),
                {"id": knowledge_id, "name": f"assistant-retention-{knowledge_id}"},
            )
            await connection.execute(
                text("INSERT INTO conversation (id, knowledge_id) VALUES (:id, :knowledge_id)"),
                {"id": conversation_id, "knowledge_id": knowledge_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO assistant_request (
                        id, external_user_id, knowledge_id, idempotency_key,
                        request_hash, conversation_id, status, updated_at
                    ) VALUES
                        (:expired_id, 'retention-user', :knowledge_id, 'expired',
                         'expired-hash', NULL, 'FAILED', now() - interval '2 days'),
                        (:recent_id, 'retention-user', :knowledge_id, 'recent',
                         'recent-hash', NULL, 'FAILED', now()),
                        (:running_id, 'retention-user', :knowledge_id, 'running',
                         'running-hash', NULL, 'RUNNING', now() - interval '2 days'),
                        (:linked_id, 'retention-user', :knowledge_id, 'linked',
                         'linked-hash', :conversation_id, 'COMPLETED',
                         now() - interval '2 days')
                    """
                ),
                {
                    "expired_id": expired_orphan_id,
                    "recent_id": recent_orphan_id,
                    "running_id": running_orphan_id,
                    "linked_id": linked_terminal_id,
                    "knowledge_id": knowledge_id,
                    "conversation_id": conversation_id,
                },
            )

            eligible_before = (
                await connection.execute(
                    text(
                        """
                        SELECT count(*) FROM assistant_request
                        WHERE conversation_id IS NULL
                          AND status IN ('COMPLETED', 'FAILED', 'CANCELLED')
                          AND updated_at < now() - interval '1 day'
                        """
                    )
                )
            ).scalar_one()

            deleted_count = await assistant_retention.delete_expired_assistant_requests(
                connection,
                retention_days=1,
            )
            remaining = set(
                (
                    await connection.execute(
                        text("SELECT id FROM assistant_request WHERE knowledge_id = :knowledge_id"),
                        {"knowledge_id": knowledge_id},
                    )
                ).scalars()
            )
            # The integration database can contain unrelated expired rows.
            # This transaction rolls every deletion back, so compare against
            # the complete eligible snapshot instead of assuming a clean DB.
            assert deleted_count == eligible_before
            assert remaining == {recent_orphan_id, running_orphan_id, linked_terminal_id}
            await transaction.rollback()
    finally:
        await engine.dispose()
