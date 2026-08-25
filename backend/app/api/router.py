from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.chat import router as chat_router
from app.api.dependencies import get_app_settings, require_service_token
from app.api.ingestion import router as ingestion_router
from app.api.logs import router as logs_router
from app.api.metrics import router as metrics_router
from app.api.rag import router as rag_router
from app.api.releases import router as releases_router
from app.api.support import router as support_router
from app.core.config import Settings

router = APIRouter(dependencies=[Depends(require_service_token)])
router.include_router(chat_router)
router.include_router(ingestion_router)
router.include_router(logs_router)
router.include_router(metrics_router)
router.include_router(rag_router)
router.include_router(releases_router)
router.include_router(support_router)


@router.get("/system/info", tags=["system"])
async def system_info(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> dict[str, str]:
    return {"name": settings.app_name, "environment": settings.app_env, "version": "1.0.0"}
