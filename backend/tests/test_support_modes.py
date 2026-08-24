from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import Request

import app.support.service as support_service_module
from app.api.rag import _use_technical_support, support_chat
from app.api.schemas import ChatRequest
from app.core.config import Settings
from app.support.service import TechnicalSupportService


def test_compatibility_route_has_only_formal_and_manual_rollback_modes() -> None:
    knowledge_id = uuid4()
    conversation_id = uuid4()

    assert _use_technical_support(
        Settings(_env_file=None, app_env="test", chat_execution_mode="technical_support"),
        knowledge_id=knowledge_id,
        conversation_id=conversation_id,
    )
    assert not _use_technical_support(
        Settings(_env_file=None, app_env="test", chat_execution_mode="legacy_rag"),
        knowledge_id=knowledge_id,
        conversation_id=conversation_id,
    )


def test_formal_support_module_does_not_import_generic_retrieval_stack() -> None:
    source = inspect.getsource(support_service_module)
    for forbidden in (
        "app.services.rag",
        "app.rag.retrieval",
        "app.rag.rerank",
        "app.rag.doc_router",
    ):
        assert forbidden not in source


class _FormalSupportService:
    def __init__(self) -> None:
        self.received: dict[str, Any] | None = None

    async def handle(self, **kwargs: Any) -> dict[str, Any]:
        self.received = kwargs
        return {
            "message_id": str(uuid4()),
            "conversation_id": str(uuid4()),
            "trace_id": str(uuid4()),
            "response_type": "NO_MATCH",
            "answer": "未匹配",
            "content": [],
            "images": [],
            "sections": [],
            "references": [],
            "usage": {},
            "latency_ms": 1,
        }


@pytest.mark.asyncio
async def test_formal_support_endpoint_has_no_legacy_service_dependency() -> None:
    service = _FormalSupportService()
    request = cast(Request, SimpleNamespace(state=SimpleNamespace(request_id="req-formal")))

    result = await support_chat(
        ChatRequest(knowledge_id=uuid4(), question="企业问题"),
        request,
        cast(TechnicalSupportService, service),
        "idempotency-key",
    )

    assert result["response_type"] == "NO_MATCH"
    assert service.received is not None
    assert service.received["request_id"] == "req-formal"
    assert service.received["idempotency_key"] == "idempotency-key"
