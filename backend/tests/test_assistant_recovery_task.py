"""Automatic stale-RUNNING Assistant request recovery (maintenance worker)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.assistant.recovery import AssistantRequestRecoveryRepository
from app.core.config import Settings
from app.tasks import assistant_recovery


class _Resources:
    def __init__(self, database: object) -> None:
        self.database = database
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _RecoveryRepository:
    def __init__(self, request_count: int = 0) -> None:
        self.request_count = request_count
        self.reconcile_calls: list[dict[str, Any]] = []

    async def reconcile(self, *, stale_before: datetime) -> dict[str, int]:
        self.reconcile_calls.append({"stale_before": stale_before})
        return {
            "request_count": self.request_count,
            "message_count": self.request_count,
            "trace_count": self.request_count,
        }


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
async def test_recovery_pass_reconciles_with_configured_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _RecoveryRepository(request_count=2)
    resources = _Resources(object())
    monkeypatch.setattr(assistant_recovery.AppResources, "create", lambda _: resources)
    monkeypatch.setattr(
        assistant_recovery, "AssistantRequestRecoveryRepository", lambda _: repository
    )
    settings = Settings(_env_file=None, app_env="test", assistant_request_stale_seconds=600)
    started = datetime.now(UTC)

    assert await assistant_recovery._run_recovery(settings) == {
        "request_count": 2,
        "message_count": 2,
        "trace_count": 2,
    }

    (call,) = repository.reconcile_calls
    assert call["stale_before"].tzinfo is not None
    assert (
        started - timedelta(seconds=605)
        <= call["stale_before"]
        <= (started - timedelta(seconds=595))
    )
    assert resources.closed is True


@pytest.mark.asyncio
async def test_recovery_disabled_does_not_create_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        assistant_recovery.AppResources,
        "create",
        lambda _: pytest.fail("disabled recovery must not open resources"),
    )

    result = await assistant_recovery._run_recovery(
        Settings(_env_file=None, app_env="test", assistant_request_stale_seconds=0)
    )

    assert result == {"request_count": 0, "message_count": 0, "trace_count": 0}


def test_sync_wrapper_uses_supplied_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, app_env="test")

    async def fake_recovery(received: Settings) -> dict[str, int]:
        assert received is settings
        return {"request_count": 1, "message_count": 1, "trace_count": 1}

    monkeypatch.setattr(assistant_recovery, "_run_recovery", fake_recovery)
    assert assistant_recovery.run_stale_assistant_request_recovery_sync(settings) == {
        "request_count": 1,
        "message_count": 1,
        "trace_count": 1,
    }


def test_periodic_registration_uses_single_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    _Queue.instances = []
    connection = _RedisConnection()
    monkeypatch.setattr(assistant_recovery, "Queue", _Queue)

    assert assistant_recovery.ensure_periodic_assistant_request_recovery(connection, 900)
    assert not assistant_recovery.ensure_periodic_assistant_request_recovery(connection, 900)

    # The second call constructs the queue but the sentinel blocks a
    # duplicate chain: exactly one delayed job is ever enqueued.
    enqueued = [job for queue in _Queue.instances for job in queue.enqueued]
    assert len(enqueued) == 1
    assert all(queue.name == "maintenance" for queue in _Queue.instances)
    delay, function, kwargs = enqueued[0]
    assert delay.total_seconds() == 900
    assert function is assistant_recovery.process_periodic_assistant_request_recovery
    assert kwargs["interval"] == 900
    assert connection.set_calls[0][2] == {"nx": True, "ex": 1_800}


def test_reschedule_restores_sentinel_and_closes_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    _Queue.instances = []
    connection = _RedisConnection()
    monkeypatch.setattr(assistant_recovery.Redis, "from_url", lambda _: connection)
    monkeypatch.setattr(assistant_recovery, "Queue", _Queue)
    settings = Settings(_env_file=None, app_env="test")

    assistant_recovery._reschedule_periodic(settings, 60)

    assert _Queue.instances[0].enqueued[0][2]["interval"] == 60
    assert connection.set_calls[-1][2] == {"ex": 120}
    assert connection.closed is True


def test_reschedule_disabled_does_not_open_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        assistant_recovery.Redis,
        "from_url",
        lambda _: pytest.fail("disabled recovery must not connect to Redis"),
    )
    settings = Settings(_env_file=None, app_env="test", assistant_request_stale_seconds=0)

    assistant_recovery._reschedule_periodic(settings, 60)


def test_periodic_task_runs_and_reschedules(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, app_env="test")
    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(assistant_recovery, "get_settings", lambda: settings)
    monkeypatch.setattr(
        assistant_recovery,
        "run_stale_assistant_request_recovery_sync",
        lambda received: calls.append(("run", received))
        or {"request_count": 3, "message_count": 3, "trace_count": 3},
    )
    monkeypatch.setattr(
        assistant_recovery,
        "_reschedule_periodic",
        lambda received, interval: calls.append(("reschedule", (received, interval))),
    )

    assert assistant_recovery.process_periodic_assistant_request_recovery(60) == {
        "request_count": 3,
        "message_count": 3,
        "trace_count": 3,
    }
    assert calls == [("run", settings), ("reschedule", (settings, 60))]


def test_periodic_task_disabled_does_not_run_or_reschedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, app_env="test", assistant_request_stale_seconds=0)
    monkeypatch.setattr(assistant_recovery, "get_settings", lambda: settings)
    monkeypatch.setattr(
        assistant_recovery,
        "run_stale_assistant_request_recovery_sync",
        lambda _: pytest.fail("disabled recovery must not run"),
    )
    monkeypatch.setattr(
        assistant_recovery,
        "_reschedule_periodic",
        lambda *_: pytest.fail("disabled recovery must not reschedule"),
    )

    assert assistant_recovery.process_periodic_assistant_request_recovery(60) == {
        "request_count": 0,
        "message_count": 0,
        "trace_count": 0,
    }


def test_periodic_task_reschedules_after_recovery_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, app_env="test")
    rescheduled: list[int] = []
    monkeypatch.setattr(assistant_recovery, "get_settings", lambda: settings)

    def fail(_: Settings) -> dict[str, int]:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(assistant_recovery, "run_stale_assistant_request_recovery_sync", fail)
    monkeypatch.setattr(
        assistant_recovery,
        "_reschedule_periodic",
        lambda _, interval: rescheduled.append(interval),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        assistant_recovery.process_periodic_assistant_request_recovery(60)
    assert rescheduled == [60]


@pytest.mark.asyncio
async def test_recovery_reconcile_real_postgres_fails_only_stale_rows() -> None:
    """End-to-end check of the exact statement the worker will run: a stale
    RUNNING request plus its linked message/trace fail atomically, while a
    fresh RUNNING request and terminal rows stay untouched."""
    database_url = os.getenv("SUPPORT_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SUPPORT_TEST_DATABASE_URL is not configured")

    engine = create_async_engine(database_url, pool_pre_ping=True)
    knowledge_id = uuid4()
    conversation_id = uuid4()
    stale_request_id = uuid4()
    fresh_request_id = uuid4()
    terminal_request_id = uuid4()
    stale_message_id = uuid4()
    stale_trace_id = uuid4()
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            await connection.execute(
                text("INSERT INTO knowledge_base (id, name) VALUES (:id, :name)"),
                {"id": knowledge_id, "name": f"assistant-recovery-{knowledge_id}"},
            )
            await connection.execute(
                text("INSERT INTO conversation (id, knowledge_id) VALUES (:id, :knowledge_id)"),
                {"id": conversation_id, "knowledge_id": knowledge_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO message (
                        id, conversation_id, sequence_no, role, content, status
                    ) VALUES (
                        :message_id, :conversation_id, 1, 'ASSISTANT',
                        'partial answer', 'STREAMING'
                    )
                    """
                ),
                {
                    "message_id": stale_message_id,
                    "conversation_id": conversation_id,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_trace (
                        id, trace_id, request_id, kb_id, conversation_id,
                        message_id, question, mode, status, engine, stage
                    ) VALUES (
                        :id, :trace_id, 'recovery-stale-req', :kb_id, :conversation_id,
                        :message_id, '问题', 'CHAT', 'RUNNING', 'RAG', 'LLM'
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "trace_id": stale_trace_id,
                    "kb_id": knowledge_id,
                    "conversation_id": conversation_id,
                    "message_id": stale_message_id,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO assistant_request (
                        id, external_user_id, knowledge_id, idempotency_key,
                        request_hash, conversation_id, assistant_message_id,
                        trace_id, status, updated_at
                    ) VALUES
                        (:stale_id, 'recovery-user', :kb_id, 'stale', 'stale-hash',
                         :conversation_id, :message_id, :trace_id,
                         'RUNNING', now() - interval '30 minutes'),
                        (:fresh_id, 'recovery-user', :kb_id, 'fresh', 'fresh-hash',
                         NULL, NULL, NULL, 'RUNNING', now()),
                        (:terminal_id, 'recovery-user', :kb_id, 'terminal',
                         'terminal-hash', NULL, NULL, NULL, 'COMPLETED',
                         now() - interval '30 minutes')
                    """
                ),
                {
                    "stale_id": stale_request_id,
                    "fresh_id": fresh_request_id,
                    "terminal_id": terminal_request_id,
                    "kb_id": knowledge_id,
                    "conversation_id": conversation_id,
                    "message_id": stale_message_id,
                    "trace_id": stale_trace_id,
                },
            )

            recovered = await AssistantRequestRecoveryRepository(engine).reconcile(
                stale_before=datetime.now(UTC) - timedelta(minutes=10),
                connection=connection,
            )

            assert recovered == {"request_count": 1, "message_count": 1, "trace_count": 1}

            request_rows = dict(
                (
                    await connection.execute(
                        text(
                            "SELECT id, status FROM assistant_request WHERE knowledge_id = :kb_id"
                        ),
                        {"kb_id": knowledge_id},
                    )
                ).all()
            )
            assert request_rows == {
                stale_request_id: "FAILED",
                fresh_request_id: "RUNNING",
                terminal_request_id: "COMPLETED",
            }
            error_code = (
                await connection.execute(
                    text("SELECT terminal_error ->> 'code' FROM assistant_request WHERE id = :id"),
                    {"id": stale_request_id},
                )
            ).scalar_one()
            assert error_code == "ABANDONED_ASSISTANT_REQUEST"

            message_status = (
                await connection.execute(
                    text("SELECT status FROM message WHERE id = :id"),
                    {"id": stale_message_id},
                )
            ).scalar_one()
            assert message_status == "FAILED"

            trace_status = (
                await connection.execute(
                    text("SELECT status FROM rag_trace WHERE trace_id = :id"),
                    {"id": stale_trace_id},
                )
            ).scalar_one()
            assert trace_status == "FAILED"

            await transaction.rollback()
    finally:
        await engine.dispose()
