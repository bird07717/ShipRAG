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


class _WelcomeRepository:
    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled
        self.requested_enabled_only: bool | None = None
        self.requested_assistant_online_only: bool | None = None

    async def get_knowledge_base_summary(
        self,
        _: object,
        *,
        enabled_only: bool = False,
        assistant_online_only: bool = False,
    ) -> dict[str, Any]:
        self.requested_enabled_only = enabled_only
        self.requested_assistant_online_only = assistant_online_only
        if not self.enabled:
            raise ApiError("KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在", 404)
        return {
            "id": "a2eb2c52-2228-4c7f-baa5-ef06f4e26b7d",
            "name": "受控知识库",
            "description": None,
            "status": "ENABLED",
            "active_index_id": "e36e1b1c-52a4-4686-9274-b5a675527869",
        }

    async def list_kb_documents(self, _: object) -> list[dict[str, Any]]:
        return [{"document_id": "d1", "title": "操作手册"}]


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
async def test_assistant_welcome_only_queries_enabled_knowledge_bases() -> None:
    repository = _WelcomeRepository(enabled=True)
    knowledge_id = UUID("a2eb2c52-2228-4c7f-baa5-ef06f4e26b7d")

    response = await welcome(knowledge_id, repository)  # type: ignore[arg-type]

    assert response["knowledge_id"] == knowledge_id
    assert response["message"] == (
        "您好，我是您的智能技术助手。请描述您遇到的问题或想完成的操作，"
        "我会为您提供清晰、可执行的建议。"
    )
    assert response["suggestions"] == [
        "如何使用U盘升级PDC",
        "如何更换PSU电源板",
        "如何手动自检VDR",
    ]
    assert repository.requested_enabled_only is True
    assert repository.requested_assistant_online_only is True


@pytest.mark.asyncio
async def test_assistant_welcome_hides_disabled_knowledge_bases() -> None:
    repository = _WelcomeRepository(enabled=False)

    with pytest.raises(ApiError) as raised:
        await welcome(
            UUID("a2eb2c52-2228-4c7f-baa5-ef06f4e26b7d"),
            repository,  # type: ignore[arg-type]
        )

    assert raised.value.code == "KNOWLEDGE_BASE_NOT_FOUND"
    assert repository.requested_enabled_only is True
    assert repository.requested_assistant_online_only is True
