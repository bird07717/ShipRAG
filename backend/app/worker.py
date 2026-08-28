from __future__ import annotations

import asyncio
import logging

from redis import Redis
from rq import Queue, Worker

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.tasks.gc import ensure_periodic_index_gc, run_index_gc_sync
from app.tasks.log_retention import run_request_error_log_retention_sync
from app.tasks.recovery import (
    reconcile_abandoned_index_builds,
    reconcile_abandoned_maintenance_tasks,
)

logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    connection = Redis.from_url(settings.redis_url.get_secret_value())
    queues = [Queue(name, connection=connection) for name in settings.queue_names]
    worker = Worker(queues, connection=connection, name=settings.rq_worker_name)
    worker.clean_registries()  # type: ignore[no-untyped-call]
    # Index recovery and index GC must have one owner.  In production the
    # index and maintenance queues run in separate workers, so only the index
    # worker performs these index-specific startup jobs.
    if "index_build" in settings.queue_names:
        recovered = asyncio.run(reconcile_abandoned_index_builds(settings, connection))
        if recovered:
            logger.warning("Reconciled %s abandoned index build task(s)", recovered)
        if settings.index_gc_enabled:
            gc_result = run_index_gc_sync(settings)
            if gc_result["deleted_count"]:
                logger.info("GC deleted %s obsolete index(es)", gc_result["deleted_count"])
    if "maintenance" in settings.queue_names:
        # A crashed maintenance worker leaves DOCUMENT_PROFILE_GENERATE rows
        # RUNNING forever, which permanently blocks new generation requests
        # (they replay the zombie).  Fail those orphans before serving.
        recovered_maintenance = asyncio.run(
            reconcile_abandoned_maintenance_tasks(settings, connection)
        )
        if recovered_maintenance:
            logger.warning("Reconciled %s abandoned maintenance task(s)", recovered_maintenance)
        if settings.request_error_log_retention_days > 0:
            retention_result = run_request_error_log_retention_sync(settings)
            if retention_result["deleted_count"]:
                logger.info(
                    "Pruned %s expired request error log row(s)",
                    retention_result["deleted_count"],
                )
        # Periodic GC runs on the maintenance queue as a self-rescheduling
        # delayed job; the built-in scheduler on this worker promotes due
        # jobs.  The sentinel keeps concurrent worker startups from
        # registering duplicate chains.
        periodic_gc_enabled = settings.index_gc_enabled and settings.index_gc_interval_seconds > 0
        if periodic_gc_enabled and ensure_periodic_index_gc(
            connection, settings.index_gc_interval_seconds
        ):
            logger.info(
                "Periodic index GC scheduled every %s second(s)",
                settings.index_gc_interval_seconds,
            )
    worker.work(
        with_scheduler=(
            "maintenance" in settings.queue_names
            and settings.index_gc_enabled
            and settings.index_gc_interval_seconds > 0
        )
    )


if __name__ == "__main__":
    main()
