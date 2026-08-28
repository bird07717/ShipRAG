from __future__ import annotations

import os

import pytest

from app.tasks.gc import (
    _PERIODIC_GC_SENTINEL_KEY,
    ensure_periodic_index_gc,
    process_periodic_gc_task,
)

redis = pytest.importorskip("redis")
from rq import Queue  # noqa: E402
from rq.registry import ScheduledJobRegistry  # noqa: E402


@pytest.fixture()
def connection():
    url = os.getenv("GC_TEST_REDIS_URL")
    if not url:
        pytest.skip("GC_TEST_REDIS_URL is not configured")
    client = redis.Redis.from_url(url)
    client.flushdb()
    try:
        yield client
    finally:
        client.flushdb()
        client.close()


def _registry(connection) -> ScheduledJobRegistry:
    return ScheduledJobRegistry(queue=Queue("maintenance", connection=connection))


def test_ensure_periodic_gc_registers_single_chain(connection) -> None:
    queue = Queue("maintenance", connection=connection)

    assert ensure_periodic_index_gc(connection, 3_600) is True
    assert ensure_periodic_index_gc(connection, 3_600) is False

    registry = _registry(connection)
    assert registry.count == 1
    job = queue.fetch_job(registry.get_job_ids()[0])
    assert job.func_name.endswith("process_periodic_gc_task")
    assert job.kwargs == {"interval": 3_600}
    assert connection.exists(_PERIODIC_GC_SENTINEL_KEY)
    assert connection.ttl(_PERIODIC_GC_SENTINEL_KEY) <= 7_200


def test_sentinel_expiry_heals_chain_after_crash(connection) -> None:
    assert ensure_periodic_index_gc(connection, 3_600) is True
    connection.delete(_PERIODIC_GC_SENTINEL_KEY)

    assert ensure_periodic_index_gc(connection, 3_600) is True
    assert _registry(connection).count == 2


def test_periodic_task_runs_gc_and_reschedules(connection, monkeypatch) -> None:
    from app.tasks import gc as gc_module

    url = os.environ["GC_TEST_REDIS_URL"]

    class _FakeSettings:
        redis_url = type("Secret", (), {"get_secret_value": lambda self: url})()

    monkeypatch.setattr(gc_module, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(gc_module, "run_index_gc_sync", lambda settings: {"deleted_count": 0})

    result = process_periodic_gc_task(interval=60)

    assert result == {"deleted_count": 0}
    assert _registry(connection).count == 1
    assert connection.exists(_PERIODIC_GC_SENTINEL_KEY)
    assert 0 < connection.ttl(_PERIODIC_GC_SENTINEL_KEY) <= 120


def test_periodic_task_reschedules_even_when_gc_fails(connection, monkeypatch) -> None:
    from app.tasks import gc as gc_module

    url = os.environ["GC_TEST_REDIS_URL"]

    class _FakeSettings:
        redis_url = type("Secret", (), {"get_secret_value": lambda self: url})()

    monkeypatch.setattr(gc_module, "get_settings", lambda: _FakeSettings())

    def _boom(settings):
        raise RuntimeError("gc unavailable")

    monkeypatch.setattr(gc_module, "run_index_gc_sync", _boom)

    with pytest.raises(RuntimeError):
        process_periodic_gc_task(interval=60)

    assert _registry(connection).count == 1
    assert connection.exists(_PERIODIC_GC_SENTINEL_KEY)
