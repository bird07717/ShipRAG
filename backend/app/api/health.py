from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.dependencies import get_health_service
from app.services.health import HealthService

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def liveness() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/ready")
async def readiness(
    service: Annotated[HealthService, Depends(get_health_service)],
) -> JSONResponse:
    result = await service.check()
    status_code = 200 if result["status"] == "ready" else 503
    return JSONResponse(status_code=status_code, content=result)
