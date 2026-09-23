"""Automatic recovery of Assistant requests abandoned in RUNNING state.

A killed API process (OOM, SIGKILL, node failure) cannot run the streaming
finalizer, so its ``assistant_request`` rows stay ``RUNNING`` forever and the
partial unique index keeps the linked conversations locked out with
``CONVERSATION_BUSY``.  This task fails stale rows using the same auditable
single-statement reconciliation as the manual ``app.assistant.recovery``
command, on maintenance-worker startup and on a short periodic cadence.

The stale window (``ASSISTANT_REQUEST_STALE_SECONDS``) is validated to exceed
the turn deadline.  RAG turns are deadline-bounded and the Support-first
policy is frozen (ADR-0021), so that deadline is the worst-case legitimate
turn duration; re-enabling Support would require widening the bound (see the
Settings validator).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from redis import Redis
from rq import Queue

from app.assistant.recovery import AssistantRequestRecoveryRepository
from app.core.config import Settings, get_settings
from app.core.resources import AppResources

logger = logging.getLogger(__name__)

_PERIODIC_SENTINEL_KEY = "rq:periodic-assistant-request-recovery"
# Short on purpose: a blocked conversation should self-heal within minutes,
# not after the daily retention pass.
_RECOVERY_INTERVAL_SECONDS = 900
_JOB_TIMEOUT_SECONDS = 600


async def _run_recovery(settings: Settings) -> dict[str, int]:
    """Fail Assistant requests (and linked rows) RUNNING past the window."""
    stale_seconds = settings.assistant_request_stale_seconds
    if stale_seconds <= 0:
        return {"request_count": 0, "message_count": 0, "trace_count": 0, "internal_trace_count": 0}
    stale_before = datetime.now(UTC) - timedelta(seconds=stale_seconds)
    resources = AppResources.create(settings)
    try:
        return await AssistantRequestRecoveryRepository(resources.database).reconcile(
            stale_before=stale_before
        )
    finally:
        await resources.close()


def run_stale_assistant_request_recovery_sync(settings: Settings) -> dict[str, int]:
    return asyncio.run(_run_recovery(settings))


def _enqueue_periodic(queue: Queue, interval_seconds: int) -> None:
    queue.enqueue_in(
        timedelta(seconds=interval_seconds),
        process_periodic_assistant_request_recovery,
        interval=interval_seconds,
        job_timeout=_JOB_TIMEOUT_SECONDS,
        result_ttl=86_400,
        failure_ttl=604_800,
    )


def ensure_periodic_assistant_request_recovery(
    connection: Redis,
    interval_seconds: int = _RECOVERY_INTERVAL_SECONDS,
) -> bool:
    """Register one fleet-wide delayed-job chain for stale recovery."""
    queue = Queue("maintenance", connection=connection)
    claimed = connection.set(_PERIODIC_SENTINEL_KEY, "1", nx=True, ex=interval_seconds * 2)
    if not claimed:
        return False
    _enqueue_periodic(queue, interval_seconds)
    return True


def _reschedule_periodic(settings: Settings, interval_seconds: int) -> None:
    if settings.assistant_request_stale_seconds <= 0:
        return
    connection = Redis.from_url(settings.redis_url.get_secret_value())
    try:
        queue = Queue("maintenance", connection=connection)
        _enqueue_periodic(queue, interval_seconds)
        connection.set(_PERIODIC_SENTINEL_KEY, "1", ex=interval_seconds * 2)
    except Exception:
        logger.warning("Failed to reschedule periodic Assistant request recovery", exc_info=True)
    finally:
        connection.close()


def process_periodic_assistant_request_recovery(interval: int) -> dict[str, Any]:
    """Run one stale-recovery pass and keep the delayed-job chain alive."""
    settings = get_settings()
    if settings.assistant_request_stale_seconds <= 0:
        return {"request_count": 0, "message_count": 0, "trace_count": 0, "internal_trace_count": 0}
    try:
        recovered = run_stale_assistant_request_recovery_sync(settings)
    finally:
        _reschedule_periodic(settings, interval)
    if recovered.get("request_count"):
        logger.warning("Periodic recovery failed %s stale Assistant request(s)", recovered)
    return recovered
