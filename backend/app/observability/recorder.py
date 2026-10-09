"""Best-effort persistence of failed API requests.

Called from the global exception handlers in app.main. Recording must never
change the response the caller gets: every failure inside the recorder is
swallowed into a warning log.
"""

from __future__ import annotations

import asyncio
import json
import traceback
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import Request

from app.common.errors import SERVER_BUSY_MESSAGE, ApiError, user_safe_chat_message
from app.observability.repository import ObservabilityRepository

logger = structlog.get_logger(__name__)

_STACK_CHAR_LIMIT = 8_000
_REQUEST_ERROR_RECORD_TIMEOUT_SECONDS = 1.0
_PENDING_REQUEST_ERROR_TASKS: set[asyncio.Task[None]] = set()

# Health probes fail loudly and often during outages; persisting them would
# only add noise (and the DB is likely the thing that is down).
_SKIPPED_PATH_PREFIXES = ("/health",)


# Rejected credentials are not diagnostic failures of the service. A single stale
# browser tab polling the dashboard produced ~96% of all rows (thousands per day) and
# drowned the real errors, so these are never persisted (the access log still has them).
_UNSTORED_ERROR_CODES = frozenset({"AUTHENTICATION_REQUIRED"})


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
    if error_code in _UNSTORED_ERROR_CODES or _should_skip(path):
        return
    engine = _engine(request)
    if engine is None:
        return
    repository = ObservabilityRepository(engine)
    try:
        async with asyncio.timeout(_REQUEST_ERROR_RECORD_TIMEOUT_SECONDS):
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


def schedule_request_error(
    request: Request,
    *,
    status_code: int,
    error_code: str,
    message: str,
    detail: dict[str, Any] | None = None,
) -> None:
    """Persist an error without making the caller's error response wait for it."""
    task = asyncio.create_task(
        record_request_error(
            request,
            status_code=status_code,
            error_code=error_code,
            message=message,
            detail=detail,
        )
    )
    _PENDING_REQUEST_ERROR_TASKS.add(task)
    task.add_done_callback(_PENDING_REQUEST_ERROR_TASKS.discard)


async def record_unhandled_exception(request: Request, exc: Exception) -> None:
    stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    await record_request_error(
        request,
        status_code=500,
        error_code="INTERNAL_ERROR",
        message="服务发生内部错误",
        detail={"exception_type": type(exc).__name__, "stack": stack[:_STACK_CHAR_LIMIT]},
    )


def _sse_error_payload(frame: bytes) -> dict[str, Any] | None:
    text = frame.decode("utf-8", errors="replace")
    if "event: error\n" not in text:
        return None
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            value = json.loads(line[6:])
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None
    return None


def _sse(event: str, payload: dict[str, Any]) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n".encode()


async def observe_stream_errors(
    request: Request,
    source: AsyncIterator[bytes],
    *,
    user_facing: bool = False,
) -> AsyncIterator[bytes]:
    """Persist SSE failures that can no longer reach FastAPI exception handlers.

    `user_facing=True` (chat answer stream) rewrites server-fault messages in
    the SSE error event to the uniform busy message; the request error log
    above still records the original message for diagnostics. Admin streams
    (playground) keep the full detail.
    """

    request_id = getattr(request.state, "request_id", "unknown")
    try:
        async for frame in source:
            payload = _sse_error_payload(frame)
            if payload is None:
                yield frame
                continue
            payload.setdefault("request_id", request_id)
            status_code = int(payload.pop("status_code", 500))
            trace_id = payload.get("trace_id")
            await record_request_error(
                request,
                status_code=status_code,
                error_code=str(payload.get("code") or "STREAM_ERROR"),
                message=str(payload.get("message") or "流式请求失败"),
                detail={"stream": True, **({"trace_id": trace_id} if trace_id else {})},
            )
            yield _sse("error", payload)
    except asyncio.CancelledError:
        raise
    except ApiError as exc:
        await record_request_error(
            request,
            status_code=exc.status_code,
            error_code=exc.code,
            message=exc.message,
            detail=exc.details or {"stream": True},
        )
        message = user_safe_chat_message(exc) if user_facing else exc.message
        yield _sse(
            "error",
            {"code": exc.code, "message": message, "request_id": request_id},
        )
    except Exception as exc:
        await record_unhandled_exception(request, exc)
        yield _sse(
            "error",
            {
                "code": "INTERNAL_ERROR",
                "message": (SERVER_BUSY_MESSAGE if user_facing else "流式请求处理失败"),
                "request_id": request_id,
            },
        )
