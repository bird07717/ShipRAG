from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import Request

import app.support.service as support_service_module
from app.api.schemas import ChatRequest
from app.api.support import router as support_router
from app.api.support import support_chat
from app.core.config import Settings
from app.gateway.models import ExecutionPolicy
from app.gateway.repository import GatewayRepository
from app.support.service import TechnicalSupportService


def test_compatibility_route_has_only_formal_and_manual_rollback_modes() -> None:
    support_repository = GatewayRepository(  # type: ignore[arg-type]
        None,
        Settings(_env_file=None, app_env="test", chat_execution_mode="technical_support"),
    )
    rag_repository = GatewayRepository(  # type: ignore[arg-type]
        None,
        Settings(_env_file=None, app_env="test", chat_execution_mode="legacy_rag"),
    )

    assert support_repository._default_policy() == ExecutionPolicy.SUPPORT_ONLY
    assert rag_repository._default_policy() == ExecutionPolicy.RAG_ONLY


def test_formal_support_module_does_not_import_generic_retrieval_stack() -> None:
    source = inspect.getsource(support_service_module)
    for forbidden in (
        "app.services.rag",
        "app.rag.retrieval",
        "app.rag.rerank",
        "app.rag.doc_router",
    ):
        assert forbidden not in source


def test_problem_profiles_are_read_only_outside_release_workflow() -> None:
    profile_routes = [
        route for route in support_router.routes if route.path == "/support/problem-profiles"
    ]
    assert profile_routes
    assert all("PUT" not in route.methods for route in profile_routes)


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
