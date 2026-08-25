from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.config import Settings
from app.core.resources import AppResources
from app.observability.repository import ObservabilityRepository

logger = logging.getLogger(__name__)


async def _run_prune(settings: Settings) -> dict[str, Any]:
    resources = AppResources.create(settings)
    repository = ObservabilityRepository(resources.database)
    try:
        deleted = await repository.delete_expired(
            retention_days=settings.request_error_log_retention_days
        )
        return {"deleted_count": deleted}
    finally:
        await resources.close()


def run_request_error_log_retention_sync(settings: Settings) -> dict[str, Any]:
    return asyncio.run(_run_prune(settings))
