"""Process-wide admission gate for chat turn generation.

Both the admin playground (``/api/v1/chat*``) and the external Assistant
stream go through this gate before they reserve a request or call a model
provider.  The gate bounds how many turns a single API process generates at
the same time so that an uncontrolled burst of traffic degrades into fast
503 responses instead of exhausting upstream provider quotas, memory, or the
database pool.  ``CHAT_TURN_CONCURRENCY_LIMIT=0`` disables the gate.

The semaphore is keyed by event loop: production processes run exactly one
loop forever, while tests may create several short-lived loops in the same
process.  Reusing one semaphore across loops would raise
``RuntimeError: ... is bound to a different event loop``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from weakref import WeakKeyDictionary

from app.common.errors import SERVER_BUSY_MESSAGE, ApiError
from app.core.config import Settings

SERVICE_BUSY_CODE = "SERVICE_BUSY"
SERVICE_BUSY_STATUS = 503


@dataclass
class _LoopGate:
    semaphore: asyncio.Semaphore
    in_flight: int = 0


_gates: WeakKeyDictionary[asyncio.AbstractEventLoop, _LoopGate] = WeakKeyDictionary()


def _gate_for(settings: Settings) -> _LoopGate:
    loop = asyncio.get_running_loop()
    gate = _gates.get(loop)
    if gate is None:
        gate = _LoopGate(asyncio.Semaphore(settings.chat_turn_concurrency_limit))
        _gates[loop] = gate
    return gate


class TurnSlot:
    """One admission slot for a chat turn; ``release`` is idempotent."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._gate: _LoopGate | None = None

    async def acquire(self) -> None:
        """Occupy one slot or fail fast with 503 after the configured wait."""
        if self._gate is not None or self._settings.chat_turn_concurrency_limit <= 0:
            return
        gate = _gate_for(self._settings)
        try:
            async with asyncio.timeout(self._settings.chat_turn_wait_seconds):
                await gate.semaphore.acquire()
        except TimeoutError as exc:
            raise ApiError(SERVICE_BUSY_CODE, SERVER_BUSY_MESSAGE, SERVICE_BUSY_STATUS) from exc
        gate.in_flight += 1
        self._gate = gate

    def release(self) -> None:
        gate = self._gate
        if gate is None:
            return
        self._gate = None
        gate.in_flight -= 1
        gate.semaphore.release()


@asynccontextmanager
async def turn_slot_scope(settings: Settings) -> AsyncIterator[None]:
    """Hold one turn slot for the duration of the context."""
    slot = TurnSlot(settings)
    await slot.acquire()
    try:
        yield
    finally:
        slot.release()


def turn_gate_snapshot(settings: Settings) -> dict[str, int]:
    """Per-process occupancy of the gate on the current event loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return {"limit": settings.chat_turn_concurrency_limit, "in_flight": 0}
    gate = _gates.get(loop)
    return {
        "limit": settings.chat_turn_concurrency_limit,
        "in_flight": gate.in_flight if gate is not None else 0,
    }
