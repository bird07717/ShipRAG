from __future__ import annotations

import asyncio
import logging

from redis import Redis
from rq import Queue, Worker

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.tasks.gc import run_index_gc_sync
from app.tasks.recovery import reconcile_abandoned_index_builds

logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    connection = Redis.from_url(settings.redis_url.get_secret_value())
    queues = [Queue(name, connection=connection) for name in settings.queue_names]
    worker = Worker(queues, connection=connection, name="rag-worker")
    worker.clean_registries()  # type: ignore[no-untyped-call]
    recovered = asyncio.run(reconcile_abandoned_index_builds(settings, connection))
    if recovered:
        logger.warning("Reconciled %s abandoned index build task(s)", recovered)
    if settings.index_gc_enabled:
        gc_result = run_index_gc_sync(settings)
        if gc_result["deleted_count"]:
            logger.info("GC deleted %s obsolete index(es)", gc_result["deleted_count"])
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
