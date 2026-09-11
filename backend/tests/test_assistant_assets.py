from __future__ import annotations

from typing import Any, cast
from uuid import UUID

import pytest

import app.api.assistant as assistant_api
from app.api.assistant import _asset_response, welcome
from app.common.assets import attachment_content_disposition
from app.common.errors import ApiError
from app.core.resources import AppResources


class _Object:
    def __init__(self) -> None:
        self.parts = [b"first", b"second", b""]
        self.headers = {"Content-Type": "application/vnd.test", "Content-Length": "11"}
        self.closed = False
        self.released = False

    def read(self, _: int) -> bytes:
        return self.parts.pop(0)

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.released = True


class _Minio:
    def __init__(self, upstream: _Object) -> None:
        self.upstream = upstream
        self.calls: list[tuple[str, str]] = []

    def get_object(self, bucket: str, key: str) -> _Object:
        self.calls.append((bucket, key))
        return self.upstream


def test_attachment_disposition_never_contains_control_characters() -> None:
    value = attachment_content_disposition("safe.docx\r\nX-Injected: yes")

    assert "\r" not in value
    assert "\n" not in value
    assert "X-Injected" in value
    assert value.startswith('attachment; filename="')


def test_assistant_document_download_route_is_not_registered(client: Any) -> None:
    response = client.get(
        "/api/v1/assistant/knowledge-bases/"
        "a2eb2c52-2228-4c7f-baa5-ef06f4e26b7d/documents/"
        "e36e1b1c-52a4-4686-9274-b5a675527869/content",
        headers={"X-External-User-ID": "user-1"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_assistant_asset_response_streams_chunks_and_releases_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run_sync(function: Any, *args: Any) -> Any:
        return function(*args)

    monkeypatch.setattr(assistant_api, "run_in_threadpool", run_sync)
    upstream = _Object()
    minio = _Minio(upstream)
    response = await _asset_response(
        cast(AppResources, type("Resources", (), {"minio": minio})()),
        {"minio_bucket": "documents", "minio_object_key": "source.docx"},
        filename="manual.docx",
    )

    chunks = [chunk async for chunk in response.body_iterator]

    assert chunks == [b"first", b"second"]
    assert minio.calls == [("documents", "source.docx")]
    assert upstream.closed is True
    assert upstream.released is True
    assert response.headers["content-disposition"] == attachment_content_disposition("manual.docx")
    assert response.headers["content-type"] == "application/vnd.test"
    assert response.headers["content-length"] == "11"


@pytest.mark.asyncio
@pytest.mark.parametrize("ready", [True, False])
async def test_assistant_welcome_uses_public_config_and_full_readiness(
    monkeypatch: pytest.MonkeyPatch, ready: bool
) -> None:
    knowledge_id = UUID("a2eb2c52-2228-4c7f-baa5-ef06f4e26b7d")
    questions = ["如何检查设备电源", "如何查看系统状态", "如何执行设备自检"]

    async def get(self: Any, requested: UUID, *, public: bool = False) -> dict:
        assert requested == knowledge_id
        assert public is True
        return {"ready": ready, "suggestions": questions}

    monkeypatch.setattr(assistant_api.AssistantConfiguration, "get", get)
    response = await welcome(knowledge_id, object())  # type: ignore[arg-type]
    assert set(response) == {"knowledge_id", "message", "suggestions"}
    assert response["knowledge_id"] == knowledge_id
    assert response["suggestions"] == (questions if ready else [])
    if not ready:
        assert response["message"] == "服务正在准备中，请稍后再试。"


@pytest.mark.asyncio
async def test_assistant_welcome_hides_offline_knowledge_bases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def get(self: Any, requested: UUID, *, public: bool = False) -> dict:
        assert public is True
        raise ApiError("KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在", 404)

    monkeypatch.setattr(assistant_api.AssistantConfiguration, "get", get)
    with pytest.raises(ApiError, match="知识库不存在"):
        await welcome(UUID("a2eb2c52-2228-4c7f-baa5-ef06f4e26b7d"), object())  # type: ignore[arg-type]
