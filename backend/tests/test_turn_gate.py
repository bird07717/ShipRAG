"""Admission gate for chat turn generation (per-process semaphore)."""

from __future__ import annotations

import asyncio

import pytest

from app.common.errors import ApiError
from app.common.turn_gate import TurnSlot, turn_gate_snapshot, turn_slot_scope
from app.core.config import Settings


def _settings(*, limit: int = 1, wait: float = 0.1) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        chat_turn_concurrency_limit=limit,
        chat_turn_wait_seconds=wait,
    )


@pytest.mark.asyncio
async def test_disabled_gate_is_noop() -> None:
    settings = _settings(limit=0)
    slot = TurnSlot(settings)
    await slot.acquire()
    slot.release()
    assert turn_gate_snapshot(settings) == {"limit": 0, "in_flight": 0}


@pytest.mark.asyncio
async def test_slot_tracks_in_flight_and_frees_capacity() -> None:
    settings = _settings(limit=2)
    first = TurnSlot(settings)
    second = TurnSlot(settings)
    await first.acquire()
    await second.acquire()
    assert turn_gate_snapshot(settings) == {"limit": 2, "in_flight": 2}

    first.release()
    second.release()
    assert turn_gate_snapshot(settings) == {"limit": 2, "in_flight": 0}

    third = TurnSlot(settings)
    await third.acquire()
    third.release()


@pytest.mark.asyncio
async def test_exhausted_gate_fails_fast_with_service_busy() -> None:
    settings = _settings(limit=1)
    holder = TurnSlot(settings)
    await holder.acquire()
    try:
        queued = TurnSlot(settings)
        with pytest.raises(ApiError) as raised:
            await queued.acquire()
        assert raised.value.code == "SERVICE_BUSY"
        assert raised.value.status_code == 503
        # The failed acquire must not have been counted.
        assert turn_gate_snapshot(settings) == {"limit": 1, "in_flight": 1}
    finally:
        holder.release()
    # Capacity is available again after the holder released.
    nxt = TurnSlot(settings)
    await nxt.acquire()
    nxt.release()


@pytest.mark.asyncio
async def test_release_is_idempotent() -> None:
    settings = _settings(limit=1)
    slot = TurnSlot(settings)
    await slot.acquire()
    slot.release()
    slot.release()
    assert turn_gate_snapshot(settings) == {"limit": 1, "in_flight": 0}


@pytest.mark.asyncio
async def test_scope_releases_on_success_and_exception() -> None:
    settings = _settings(limit=1)
    async with turn_slot_scope(settings):
        assert turn_gate_snapshot(settings)["in_flight"] == 1
    assert turn_gate_snapshot(settings)["in_flight"] == 0

    with pytest.raises(RuntimeError, match="boom"):
        async with turn_slot_scope(settings):
            raise RuntimeError("boom")
    assert turn_gate_snapshot(settings)["in_flight"] == 0


def test_gates_are_isolated_per_event_loop() -> None:
    """Sequential loops (tests, asyncio.run tasks) each get their own gate."""

    async def acquire_and_report(limit: int) -> dict[str, int]:
        settings = _settings(limit=limit)
        slot = TurnSlot(settings)
        await slot.acquire()
        snapshot = turn_gate_snapshot(settings)
        slot.release()
        return snapshot

    first = asyncio.run(acquire_and_report(3))
    second = asyncio.run(acquire_and_report(3))
    assert first == {"limit": 3, "in_flight": 1}
    assert second == {"limit": 3, "in_flight": 1}


def test_snapshot_outside_event_loop() -> None:
    settings = _settings(limit=5)
    assert turn_gate_snapshot(settings) == {"limit": 5, "in_flight": 0}
