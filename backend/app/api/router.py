from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.answer_reviews import router as answer_reviews_router
from app.api.assistant import router as assistant_router
from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.dependencies import (
    get_app_settings,
    require_assistant_service_token,
    require_service_token,
)
from app.api.ingestion import router as ingestion_router
from app.api.knowledge_assistant import router as knowledge_assistant_router
from app.api.logs import router as logs_router
from app.api.metrics import router as metrics_router
from app.api.rag import router as rag_router
from app.api.rag_document_cards import router as rag_document_cards_router
from app.api.releases import router as releases_router
from app.api.support import router as support_router
from app.core.config import Settings

router = APIRouter(dependencies=[Depends(require_service_token)])
router.include_router(answer_reviews_router)
router.include_router(chat_router)
router.include_router(conversations_router)
router.include_router(ingestion_router)
router.include_router(knowledge_assistant_router)
router.include_router(logs_router)
router.include_router(metrics_router)
router.include_router(rag_router)
router.include_router(rag_document_cards_router)
router.include_router(releases_router)
router.include_router(support_router)

# The business-backend contract intentionally has a separate credential from
# the administrative surface above.  Do not nest it under the admin router.
assistant_api_router = APIRouter(dependencies=[Depends(require_assistant_service_token)])
assistant_api_router.include_router(assistant_router)


@router.get("/system/info", tags=["system"])
async def system_info(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> dict[str, str]:
    return {"name": settings.app_name, "environment": settings.app_env, "version": "1.0.0"}
