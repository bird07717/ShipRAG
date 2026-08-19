from __future__ import annotations

import secrets
from typing import Annotated, cast

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.common.errors import ApiError
from app.core.config import Settings
from app.core.resources import AppResources
from app.services.health import HealthService

bearer_scheme = HTTPBearer(auto_error=False)


def get_app_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_resources(request: Request) -> AppResources:
    return cast(AppResources, request.app.state.resources)


def get_health_service(
    settings: Annotated[Settings, Depends(get_app_settings)],
    resources: Annotated[AppResources, Depends(get_resources)],
) -> HealthService:
    return HealthService(settings=settings, resources=resources)


async def require_service_token(
    settings: Annotated[Settings, Depends(get_app_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> None:
    configured = settings.service_token
    if configured is None and settings.app_env in {"development", "test"}:
        return
    if credentials is None or credentials.scheme.lower() != "bearer" or configured is None:
        raise ApiError("AUTHENTICATION_REQUIRED", "需要服务访问凭据", 401)
    if not secrets.compare_digest(credentials.credentials, configured.get_secret_value()):
        raise ApiError("AUTHENTICATION_REQUIRED", "服务访问凭据无效", 401)
