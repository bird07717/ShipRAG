"""End-user error contract for the chat endpoints.

The integrator forwards `error.message` verbatim into the embedded page, so
server-side faults (5xx) on chat routes must collapse to the uniform busy
message; admin routes keep their full technical messages, and client-side
errors (4xx) keep their actionable text.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.engine_dependencies import get_chat_gateway, get_gateway_repository
from app.common.errors import ApiError
from app.common.turn_gate import TurnSlot
from app.core.config import Settings
from app.gateway.models import ExecutionPolicy, ResolvedPolicy
from app.gateway.repository import GatewayRepository
from app.main import create_app


def _policy() -> ResolvedPolicy:
    return ResolvedPolicy(
        knowledge_id=uuid4(),
        policy=ExecutionPolicy.RAG_ONLY,
        config_version=1,
        fallback_conditions=("NO_MATCH",),
        conversation_mode=None,
    )


class _StubRepository:
    def __init__(self, settings: Settings | None = None) -> None:
        # The chat endpoints read turn-gate settings from the repository;
        # keep the gate disabled by default so contract tests stay focused.
        self.settings = settings or Settings(
            _env_file=None, app_env="test", chat_turn_concurrency_limit=0
        )

    async def resolve_policy(self, **kwargs: Any) -> ResolvedPolicy:
        return _policy()


class _RaisingGateway:
    def __init__(self, error: ApiError) -> None:
        self.error = error
        self.handled = 0

    async def handle(self, **kwargs: Any) -> dict[str, Any]:
        self.handled += 1
        raise self.error

    async def stream(self, **kwargs: Any) -> Any:
        self.handled += 1
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


# --- Turn admission gate -----------------------------------------------------


def _gate_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        service_token=SecretStr("test-token"),
        database_url=SecretStr("postgresql+asyncpg://rag:password@127.0.0.1:1/test"),
        redis_url=SecretStr("redis://:password@127.0.0.1:1/0"),
        minio_endpoint="127.0.0.1:1",
        readiness_timeout_seconds=0.1,
        chat_turn_concurrency_limit=1,
        chat_turn_wait_seconds=0.1,
    )


async def _stream_error_events(async_client: httpx.AsyncClient) -> tuple[int, list[str]]:
    async with async_client.stream(
        "POST", "/api/v1/chat/stream", json=_chat_payload(), headers=_AUTH
    ) as response:
        status_code = response.status_code
        body = (await response.aread()).decode()
    events = [
        line.removeprefix("data: ").strip()
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    return status_code, events


@pytest.mark.asyncio
async def test_chat_endpoints_reject_when_turn_gate_is_busy_and_pass_after_release() -> None:
    """The admin endpoints hold one gate slot per turn: an exhausted gate
    answers 503/SSE SERVICE_BUSY before the gateway runs, and frees the slot
    for the next request once the holder releases."""
    settings = _gate_settings()
    app = create_app(settings)
    repository = _StubRepository(settings)
    gateway = _RaisingGateway(ApiError("VALIDATION_ERROR", "问题不能为空", 422))
    app.dependency_overrides[get_chat_gateway] = lambda: cast(Any, gateway)
    app.dependency_overrides[get_gateway_repository] = lambda: cast(GatewayRepository, repository)
    holder = TurnSlot(settings)
    await holder.acquire()
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as async_client:
                sync_busy = await async_client.post(
                    "/api/v1/chat", json=_chat_payload(), headers=_AUTH
                )
                assert sync_busy.status_code == 503
                assert sync_busy.json()["error"]["code"] == "SERVICE_BUSY"

                stream_status, stream_events = await _stream_error_events(async_client)
                assert stream_status == 200
                assert any('"SERVICE_BUSY"' in event for event in stream_events), stream_events

                # The gateway never ran while the only slot was held.
                assert gateway.handled == 0

                holder.release()

                # The slot is free again: requests reach the gateway (whose
                # stubbed VALIDATION_ERROR proves pass-through).
                sync_pass = await async_client.post(
                    "/api/v1/chat", json=_chat_payload(), headers=_AUTH
                )
                assert sync_pass.status_code == 422
                assert sync_pass.json()["error"]["code"] == "VALIDATION_ERROR"

                pass_status, pass_events = await _stream_error_events(async_client)
                assert pass_status == 200
                assert any('"VALIDATION_ERROR"' in event for event in pass_events), pass_events
    finally:
        holder.release()
