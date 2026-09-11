from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_resources
from app.api.engine_dependencies import get_chat_gateway
from app.api.schemas import AnswerRetestRequest, AnswerReviewRequest
from app.assistant.retest import run_retest
from app.assistant.reviews import AnswerReviews
from app.core.resources import AppResources
from app.gateway.service import ChatGatewayService

router = APIRouter(prefix="/knowledge-bases/{knowledge_id}", tags=["answer-reviews"])


def get_answer_reviews(resources: Annotated[AppResources, Depends(get_resources)]) -> AnswerReviews:
    return AnswerReviews(resources.database)


@router.get("/answer-records")
async def list_records(
    knowledge_id: UUID,
    reviews: Annotated[AnswerReviews, Depends(get_answer_reviews)],
    marked: bool = False,
    status: Literal["OPEN", "VERIFY", "CLOSED"] | None = None,
    query: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    return await reviews.records(
        knowledge_id, marked=marked, status=status, query=query, limit=limit, offset=offset
    )


@router.get("/answer-records/{request_id}")
async def record_detail(
    knowledge_id: UUID,
    request_id: UUID,
    reviews: Annotated[AnswerReviews, Depends(get_answer_reviews)],
) -> dict[str, Any]:
    return await reviews.detail(knowledge_id, request_id)


@router.put("/answer-records/{request_id}/review")
async def save_review(
    knowledge_id: UUID,
    request_id: UUID,
    payload: AnswerReviewRequest,
    reviews: Annotated[AnswerReviews, Depends(get_answer_reviews)],
) -> dict[str, Any]:
    return await reviews.save(knowledge_id, request_id, payload.model_dump())


@router.post("/answer-reviews/{review_id}/retests")
async def retest(
    knowledge_id: UUID,
    review_id: UUID,
    payload: AnswerRetestRequest,
    reviews: Annotated[AnswerReviews, Depends(get_answer_reviews)],
    gateway: Annotated[ChatGatewayService, Depends(get_chat_gateway)],
) -> dict[str, Any]:
    return await run_retest(
        reviews,
        gateway,
        kb=knowledge_id,
        review_id=review_id,
        run_id=payload.run_id,
        revision=payload.revision,
    )
