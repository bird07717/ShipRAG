from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.health import router as health_router
from app.api.router import router as api_router
from app.common.errors import ApiError
from app.common.middleware import RequestContextMiddleware
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.resources import AppResources

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
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Idempotency-Key"],
        expose_headers=["X-Request-ID"],
    )
    app.include_router(health_router)
    app.include_router(api_router, prefix=resolved_settings.api_prefix)

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(request, exc.code, exc.message, exc.details),
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
        return JSONResponse(
            status_code=422,
            content=_error_body(
                request, "VALIDATION_ERROR", "请求参数不符合接口约定", {"fields": fields}
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", error_type=type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content=_error_body(request, "INTERNAL_ERROR", "服务发生内部错误"),
        )

    return app


app = create_app()
