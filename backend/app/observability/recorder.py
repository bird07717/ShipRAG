"""Best-effort persistence of failed API requests.

Called from the global exception handlers in app.main. Recording must never
change the response the caller gets: every failure inside the recorder is
swallowed into a warning log.
"""

from __future__ import annotations

import traceback
from typing import Any

import structlog
from fastapi import Request

from app.observability.repository import ObservabilityRepository

logger = structlog.get_logger(__name__)

_STACK_CHAR_LIMIT = 8_000

# Health probes fail loudly and often during outages; persisting them would
# only add noise (and the DB is likely the thing that is down).
_SKIPPED_PATH_PREFIXES = ("/health",)


def _should_skip(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in _SKIPPED_PATH_PREFIXES)


def _engine(request: Request) -> Any:
    resources = getattr(request.app.state, "resources", None)
    return getattr(resources, "database", None)


async def record_request_error(
    request: Request,
    *,
    status_code: int,
    error_code: str,
    message: str,
    detail: dict[str, Any] | None = None,
) -> None:
    path = request.url.path
    if _should_skip(path):
        return
    engine = _engine(request)
    if engine is None:
        return
    repository = ObservabilityRepository(engine)
    try:
        await repository.insert_request_error(
            request_id=getattr(request.state, "request_id", "unknown"),
            method=request.method,
            path=path,
            status_code=status_code,
            error_code=error_code,
            message=message,
            detail=detail,
        )
    except Exception:
        logger.warning(
            "request_error_log_write_failed",
            path=path,
            status_code=status_code,
            error_code=error_code,
        )


async def record_unhandled_exception(request: Request, exc: Exception) -> None:
    stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    await record_request_error(
        request,
        status_code=500,
        error_code="INTERNAL_ERROR",
        message="服务发生内部错误",
        detail={"exception_type": type(exc).__name__, "stack": stack[:_STACK_CHAR_LIMIT]},
    )
