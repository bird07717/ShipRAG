from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.health import router as health_router
from app.api.pagination import TOTAL_COUNT_HEADER
from app.api.router import assistant_api_router
from app.api.router import router as api_router
from app.common.errors import SERVER_BUSY_MESSAGE, ApiError, user_safe_chat_message
from app.common.middleware import RequestContextMiddleware
from app.core.build_info import read_build_info
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.resources import AppResources
from app.observability.recorder import (
    record_unhandled_exception,
    schedule_request_error,
)

logger = structlog.get_logger(__name__)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def _error_body(
    request: Request,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": _request_id(request),
        }
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resources = AppResources.create(resolved_settings)
        app.state.resources = resources
        try:
            yield
        finally:
            await resources.close()

    app = FastAPI(
        title=resolved_settings.app_name,
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.build_info = read_build_info()
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Request-ID",
            "Idempotency-Key",
            "X-External-User-ID",
        ],
        expose_headers=["X-Request-ID", TOTAL_COUNT_HEADER],
    )
    app.include_router(health_router)
    app.include_router(api_router, prefix=resolved_settings.api_prefix)
    app.include_router(assistant_api_router, prefix=resolved_settings.api_prefix)

    def _is_user_chat_path(request: Request) -> bool:
        # Chat answer endpoints are end-user facing (the integrator forwards
        # the message verbatim); admin/playground routes keep full detail.
        return request.url.path.startswith(f"{resolved_settings.api_prefix}/chat")

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        schedule_request_error(
            request,
            status_code=exc.status_code,
            error_code=exc.code,
            message=exc.message,
            detail=exc.details or None,
        )
        message = exc.message
        details: dict[str, Any] | None = exc.details
        if _is_user_chat_path(request) and exc.status_code >= 500:
            message = user_safe_chat_message(exc)
            details = None
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(request, exc.code, message, details),
            headers={"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields = [
            {"location": list(error["loc"]), "message": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        schedule_request_error(
            request,
            status_code=422,
            error_code="VALIDATION_ERROR",
            message="请求参数不符合接口约定",
            detail={"fields": fields},
        )
        return JSONResponse(
            status_code=422,
            content=_error_body(
                request, "VALIDATION_ERROR", "请求参数不符合接口约定", {"fields": fields}
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Routing failures (404/405) previously bypassed the unified error
        # body, leaving the integration side without a request_id to report.
        code = f"HTTP_{exc.status_code}"
        message = str(exc.detail) if exc.detail else "请求无法处理"
        if exc.status_code == 404 and message == "Not Found":
            # A bare "Not Found" from the router almost always means the
            # running API process predates the route (e.g. after a deploy);
            # say so instead of surfacing FastAPI's default text.
            message = "接口不存在：后端服务可能是旧版本，请重启后端进程后重试"
        schedule_request_error(
            request, status_code=exc.status_code, error_code=code, message=message
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(request, code, message),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", error_type=type(exc).__name__)
        await record_unhandled_exception(request, exc)
        message = SERVER_BUSY_MESSAGE if _is_user_chat_path(request) else "服务发生内部错误"
        return JSONResponse(
            status_code=500,
            content=_error_body(request, "INTERNAL_ERROR", message),
        )

    return app


app = create_app()
