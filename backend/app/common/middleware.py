from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = structlog.get_logger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("X-Request-ID", "").strip()[:128] or str(uuid4())
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("request_failed", method=request.method, path=request.url.path)
            raise
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers["X-Request-ID"] = request_id
        await logger.ainfo(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return response
