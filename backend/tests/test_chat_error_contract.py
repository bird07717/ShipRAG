"""End-user error contract for the chat endpoints.

The integrator forwards `error.message` verbatim into the embedded page, so
server-side faults (5xx) on chat routes must collapse to the uniform busy
message; admin routes keep their full technical messages, and client-side
errors (4xx) keep their actionable text.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.engine_dependencies import get_chat_gateway, get_gateway_repository
from app.common.errors import ApiError
from app.gateway.models import ExecutionPolicy, ResolvedPolicy
from app.gateway.repository import GatewayRepository


def _policy() -> ResolvedPolicy:
    return ResolvedPolicy(
        knowledge_id=uuid4(),
        policy=ExecutionPolicy.RAG_ONLY,
        config_version=1,
        fallback_conditions=("NO_MATCH",),
        conversation_mode=None,
    )


class _StubRepository:
    async def resolve_policy(self, **kwargs: Any) -> ResolvedPolicy:
        return _policy()


class _RaisingGateway:
    def __init__(self, error: ApiError) -> None:
        self.error = error

    async def handle(self, **kwargs: Any) -> dict[str, Any]:
        raise self.error

    async def stream(self, **kwargs: Any) -> Any:
        raise self.error
        yield b""  # pragma: no cover


def _install(
    client: TestClient, gateway: _RaisingGateway, repository: _StubRepository
) -> dict[str, Any]:
    overrides = {
        get_chat_gateway: lambda: cast(Any, gateway),
        get_gateway_repository: lambda: cast(GatewayRepository, repository),
    }
    client.app.dependency_overrides.update(overrides)
    return overrides


_AUTH = {"Authorization": "Bearer test-token"}


def _chat_payload() -> dict[str, Any]:
    return {"knowledge_id": str(uuid4()), "question": "如何升级"}


@pytest.mark.parametrize(
    ("error", "expected_message"),
    [
        pytest.param(
            ApiError(
                "UPSTREAM_UNAVAILABLE",
                "查询向量生成失败",
                503,
                {"detail": "HTTP 402 Payment Required"},
            ),
            "服务繁忙，请稍后重试",
            id="server-fault-5xx",
        ),
        pytest.param(
            ApiError("TURN_DEADLINE_EXCEEDED", "本次请求处理超时，请稍后重试", 504),
            "服务繁忙，请稍后重试",
            id="deadline-5xx",
        ),
        pytest.param(
            ApiError("VALIDATION_ERROR", "问题不能为空", 422),
            "问题不能为空",
            id="client-error-4xx-kept",
        ),
    ],
)
def test_chat_error_message_contract(
    client: TestClient, error: ApiError, expected_message: str
) -> None:
    gateway = _RaisingGateway(error)
    overrides = _install(client, gateway, _StubRepository())
    try:
        response = client.post("/api/v1/chat", json=_chat_payload(), headers=_AUTH)

        assert response.status_code == error.status_code
        body = response.json()["error"]
        assert body["code"] == error.code
        assert body["message"] == expected_message
        if error.status_code >= 500:
            assert body.get("details") in (None, {})
        assert body.get("request_id")
    finally:
        for key in overrides:
            client.app.dependency_overrides.pop(key, None)


def test_admin_routes_keep_technical_messages(client: TestClient) -> None:
    # Scoping check: the sanitization applies to chat paths only — an admin
    # route failing with a 5xx keeps its actionable message for operators.
    from app.api.engine_dependencies import get_rag_repository

    class _RaisingRagRepository:
        async def list_prompts(self) -> list[dict[str, Any]]:
            raise ApiError("MODEL_NOT_CONFIGURED", "LLM 模型未配置", 503)

    client.app.dependency_overrides[get_rag_repository] = lambda: cast(Any, _RaisingRagRepository())
    try:
        response = client.get("/api/v1/prompts", headers=_AUTH)

        assert response.status_code == 503
        body = response.json()["error"]
        assert body["message"] == "LLM 模型未配置"
    finally:
        client.app.dependency_overrides.pop(get_rag_repository, None)


def test_chat_stream_error_event_is_user_safe(client: TestClient) -> None:
    # Mid-stream failures surface as SSE error events: the message collapses
    # to the busy copy while code + request_id stay machine-readable.
    gateway = _RaisingGateway(ApiError("UPSTREAM_UNAVAILABLE", "查询向量生成失败", 503))
    overrides = _install(client, gateway, _StubRepository())
    try:
        with client.stream(
            "POST", "/api/v1/chat/stream", json=_chat_payload(), headers=_AUTH
        ) as response:
            assert response.status_code == 200
            events: list[tuple[str, str]] = []
            event_name = ""
            for line in response.iter_lines():
                if line.startswith("event: "):
                    event_name = line.removeprefix("event: ").strip()
                elif line.startswith("data: ") and event_name:
                    events.append((event_name, line.removeprefix("data: ")))
                    event_name = ""

        error_events = [data for name, data in events if name == "error"]
        assert error_events, events
        import json

        payload = json.loads(error_events[0])
        assert payload["code"] == "UPSTREAM_UNAVAILABLE"
        assert payload["message"] == "服务繁忙，请稍后重试"
        assert payload.get("request_id")
    finally:
        for key in overrides:
            client.app.dependency_overrides.pop(key, None)
