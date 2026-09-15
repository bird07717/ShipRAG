from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import get_app_settings, get_resources
from app.api.schemas import (
    RagDocumentCardGenerateRequest,
    RagDocumentCardListResponse,
    RagDocumentCardReadinessResponse,
    RagDocumentCardResponse,
    RagDocumentCardReviewRequest,
    RagDocumentCardUpdateRequest,
    TaskSubmissionResponse,
)
from app.common.errors import ApiError
from app.core.config import Settings
from app.core.resources import AppResources
from app.rag.document_card_repository import DocumentCardRepository
from app.rag.document_card_service import DocumentCardService

router = APIRouter(tags=["rag-document-cards"])


def get_document_card_service(
    settings: Annotated[Settings, Depends(get_app_settings)],
    resources: Annotated[AppResources, Depends(get_resources)],
) -> DocumentCardService:
    return DocumentCardService(DocumentCardRepository(resources.database, settings), settings)


@router.get(
    "/knowledge-bases/{knowledge_id}/rag-document-cards",
    response_model=RagDocumentCardListResponse,
)
async def list_rag_document_cards(
    knowledge_id: UUID,
    service: Annotated[DocumentCardService, Depends(get_document_card_service)],
    index_id: Annotated[UUID | None, Query()] = None,
) -> dict[str, Any]:
    return await service.list_cards(knowledge_id, index_id=index_id)


@router.get(
    "/knowledge-bases/{knowledge_id}/rag-document-cards/readiness",
    response_model=RagDocumentCardReadinessResponse,
)
async def get_rag_document_card_readiness(
    knowledge_id: UUID,
    service: Annotated[DocumentCardService, Depends(get_document_card_service)],
    index_id: Annotated[UUID | None, Query()] = None,
) -> dict[str, Any]:
    return await service.readiness(knowledge_id, index_id=index_id)


@router.get(
    "/knowledge-bases/{knowledge_id}/documents/{document_id}/rag-document-card",
    response_model=RagDocumentCardResponse | None,
)
async def get_rag_document_card(
    knowledge_id: UUID,
    document_id: UUID,
    service: Annotated[DocumentCardService, Depends(get_document_card_service)],
    index_id: Annotated[UUID | None, Query()] = None,
) -> dict[str, Any] | None:
    return await service.get_card(knowledge_id, document_id, index_id=index_id)


@router.post(
    "/knowledge-bases/{knowledge_id}/rag-document-cards/generate",
    response_model=TaskSubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_rag_document_cards(
    knowledge_id: UUID,
    payload: RagDocumentCardGenerateRequest,
    service: Annotated[DocumentCardService, Depends(get_document_card_service)],
) -> dict[str, Any]:
    return await service.request_generation(
        knowledge_id,
        index_id=payload.index_id,
        document_id=payload.document_id,
        replace_confirmed=payload.replace_confirmed,
    )


@router.put(
    "/knowledge-bases/{knowledge_id}/documents/{document_id}/rag-document-card",
    response_model=RagDocumentCardResponse,
)
async def update_rag_document_card(
    knowledge_id: UUID,
    document_id: UUID,
    payload: RagDocumentCardUpdateRequest,
    service: Annotated[DocumentCardService, Depends(get_document_card_service)],
) -> dict[str, Any]:
    return await service.update_card(
        knowledge_id=knowledge_id,
        index_id=payload.index_id,
        document_id=document_id,
        expected_revision=payload.expected_revision,
        payload=payload.card_payload,
    )


@router.post(
    "/knowledge-bases/{knowledge_id}/documents/{document_id}/rag-document-card/review",
    response_model=RagDocumentCardResponse,
)
async def review_rag_document_card(
    knowledge_id: UUID,
    document_id: UUID,
    payload: RagDocumentCardReviewRequest,
    service: Annotated[DocumentCardService, Depends(get_document_card_service)],
) -> dict[str, Any]:
    card = await service.get_card(knowledge_id, document_id, index_id=payload.index_id)
    if card is None:
        raise ApiError("RAG_DOCUMENT_CARD_NOT_FOUND", "RAG 文档卡片不存在", 404)
    return await service.review_card(
        knowledge_id=knowledge_id,
        index_id=payload.index_id,
        document_id=document_id,
        expected_revision=payload.expected_revision,
        review_status=payload.review_status,
    )
