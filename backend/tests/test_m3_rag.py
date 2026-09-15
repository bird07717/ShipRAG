from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.rag.llm import (
    ZhipuLlmProvider,
    _validate_structured_json,
)
from app.rag.models import ModelSnapshot
from app.rag.security import RAG_SYSTEM_GUARD
from app.releases.models import ProfileScope


def test_structured_validation_accepts_a_json_object_wrapped_in_markdown() -> None:
    scope = _validate_structured_json(
        ProfileScope,
        "结果如下：\n```json\n"
        '{"core_subject":"PDC 升级","task":"升级","symptoms":[],"exclusions":[],"evidence":[]}\n'
        "```",
    )

    assert scope.core_subject == "PDC 升级"


@pytest.mark.asyncio
async def test_zhipu_stream_passes_through_thinking_and_collects_usage() -> None:
    body = (
        'data: {"choices":[{"delta":{"reasoning_content":"internal","content":"端口"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"3306。[S1]"}}],'
        '"usage":{"prompt_tokens":10,"completion_tokens":4}}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["stream"] is True
        # DB model_config parameters are authoritative: the thinking block is
        # passed through because omitting it would enable the provider default.
        assert payload["thinking"] == {"type": "enabled"}
        assert payload["messages"] == [
            {"role": "system", "content": RAG_SYSTEM_GUARD},
            {"role": "user", "content": "prompt"},
        ]
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    settings = Settings(
        _env_file=None,
        app_env="test",
        zhipu_api_key=SecretStr("not-a-real-key"),
    )
    snapshot = ModelSnapshot(
        "zhipu",
        "glm-test",
        {"thinking": {"type": "enabled"}, "max_tokens": 100, "temperature": 0},
    )
    async with httpx.AsyncClient(
        base_url=settings.zhipu_base_url, transport=httpx.MockTransport(handler)
    ) as client:
        provider = ZhipuLlmProvider(settings, snapshot, client)
        chunks = [chunk async for chunk in provider.stream("prompt")]

    assert chunks == ["端口", "3306。[S1]"]
    assert provider.usage == {"prompt_tokens": 10, "completion_tokens": 4}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("thinking_type", "expected"),
    [("enabled", True), ("disabled", False)],
)
async def test_dashscope_stream_uses_compatible_chat_and_optional_thinking(
    thinking_type: str, expected: bool | None
) -> None:
    body = 'data: {"choices":[{"delta":{"content":"回答"},"finish_reason":"stop"}]}\n\n'

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/compatible-mode/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload.get("enable_thinking") is expected
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    settings = Settings(
        _env_file=None,
        app_env="test",
        dashscope_api_key=SecretStr("not-a-real-key"),
    )
    snapshot = ModelSnapshot(
        "dashscope",
        "qwen-plus",
        {"thinking": {"type": thinking_type}, "max_tokens": 100, "temperature": 0},
    )
    async with httpx.AsyncClient(
        base_url=settings.dashscope_base_url, transport=httpx.MockTransport(handler)
    ) as client:
        provider = ZhipuLlmProvider(settings, snapshot, client)
        chunks = [chunk async for chunk in provider.stream("prompt")]

    assert chunks == ["回答"]


@pytest.mark.asyncio
async def test_dashscope_structured_call_disables_thinking_and_uses_json_mode() -> None:
    body = {
        "choices": [
            {
                "message": {
                    "content": (
                        '{"core_subject":"PDC 升级","task":"升级","symptoms":[],'
                        '"exclusions":[],"evidence":[]}'
                    )
                },
                "finish_reason": "stop",
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["enable_thinking"] is False
        assert payload["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json=body)

    settings = Settings(
        _env_file=None,
        app_env="test",
        dashscope_api_key=SecretStr("not-a-real-key"),
    )
    snapshot = ModelSnapshot(
        "dashscope",
        "qwen3.8-flash",
        {"thinking": {"type": "disabled"}},
    )
    async with httpx.AsyncClient(
        base_url=settings.dashscope_base_url, transport=httpx.MockTransport(handler)
    ) as client:
        provider = ZhipuLlmProvider(settings, snapshot, client)
        result = await provider.complete_structured(
            "请以 JSON 输出",
            system="仅输出 JSON",
            schema=ProfileScope,
            max_tokens=200,
            temperature=0,
        )

    assert result.core_subject == "PDC 升级"


def test_llm_http_error_detail_keeps_only_bounded_provider_fields() -> None:
    response = httpx.Response(
        400,
        json={
            "error": {
                "code": "invalid_parameter",
                "message": "参数不支持\n" + "x" * 500,
                "request": "must-not-be-retained",
            }
        },
    )

    detail = ZhipuLlmProvider._http_error_detail(response)

    assert detail.startswith("HTTP 400; code=invalid_parameter; message=参数不支持 ")
    assert "request" not in detail
    assert len(detail) < 320


async def test_stream_retries_without_thinking_on_always_thinking_model() -> None:
    """Always-thinking models (e.g. GLM-5.3-Flash) reject thinking control
    with HTTP 400/1210; the provider must strip the key, retry once and
    remember for subsequent calls."""
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        if "thinking" in payload:
            return httpx.Response(
                400,
                json={"error": {"code": "1210", "message": "该模型始终思考，不支持关闭思考"}},
            )
        body = 'data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    settings = Settings(_env_file=None, app_env="test", zhipu_api_key=SecretStr("not-a-real-key"))
    snapshot = ModelSnapshot("zhipu", "glm-always-thinking", {"thinking": {"type": "disabled"}})
    async with httpx.AsyncClient(
        base_url=settings.zhipu_base_url, transport=httpx.MockTransport(handler)
    ) as client:
        provider = ZhipuLlmProvider(settings, snapshot, client)
        chunks = [chunk async for chunk in provider.stream("prompt")]
        # The learned flag keeps later calls from repeating the doomed round trip.
        chunks += [chunk async for chunk in provider.stream("prompt")]

    assert chunks == ["OK", "OK"]
    assert "thinking" in calls[0]
    assert "thinking" not in calls[1]
    assert len(calls) == 3  # retry + second call goes straight through
    assert "thinking" not in calls[2]


@pytest.mark.asyncio
async def test_stream_thinking_rejection_reads_unconsumed_response_body() -> None:
    """A streaming 400 must not access response.text before aread()."""

    class _BodyStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield '{"error":{"code":"1210","message":"该模型始终思考"}}'.encode()

        async def aclose(self) -> None:
            return None

    response = httpx.Response(400, stream=_BodyStream())

    assert await ZhipuLlmProvider._stream_thinking_rejection(response)
    assert response.is_stream_consumed


@pytest.mark.asyncio
async def test_complete_strips_thinking_on_always_thinking_model() -> None:
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        if "thinking" in payload:
            return httpx.Response(
                400,
                json={"error": {"code": "1210", "message": "该模型始终思考，不支持关闭思考"}},
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    settings = Settings(_env_file=None, app_env="test", zhipu_api_key=SecretStr("not-a-real-key"))
    snapshot = ModelSnapshot("zhipu", "glm-always-thinking", {})
    async with httpx.AsyncClient(
        base_url=settings.zhipu_base_url, transport=httpx.MockTransport(handler)
    ) as client:
        provider = ZhipuLlmProvider(settings, snapshot, client)
        result = await provider.complete("prompt", system="s", max_tokens=8, temperature=0.1)

    assert result == "OK"
    assert "thinking" in calls[0]  # utility calls disable thinking by default
    assert "thinking" not in calls[1]


@pytest.mark.asyncio
async def test_glm_53_flash_skips_known_unsupported_thinking_control() -> None:
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    settings = Settings(_env_file=None, app_env="test", zhipu_api_key=SecretStr("not-a-real-key"))
    snapshot = ModelSnapshot("zhipu", "glm-5.3-flash", {"thinking": {"type": "disabled"}})
    async with httpx.AsyncClient(
        base_url=settings.zhipu_base_url, transport=httpx.MockTransport(handler)
    ) as client:
        provider = ZhipuLlmProvider(settings, snapshot, client)
        result = await provider.complete("prompt", system="s", max_tokens=8, temperature=0.1)

    assert result == "OK"
    assert len(calls) == 1
    assert "thinking" not in calls[0]


@pytest.mark.asyncio
async def test_complete_grows_token_budget_when_reasoning_exhausts_it() -> None:
    """Always-thinking models spend completion tokens on reasoning first; a
    length-finish with empty content must grow max_tokens and retry."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(int(payload["max_tokens"]))
        if payload["max_tokens"] <= 1_024:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"content": "", "reasoning_content": "thinking..."},
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    settings = Settings(_env_file=None, app_env="test", zhipu_api_key=SecretStr("not-a-real-key"))
    snapshot = ModelSnapshot("zhipu", "glm-always-thinking", {})
    async with httpx.AsyncClient(
        base_url=settings.zhipu_base_url, transport=httpx.MockTransport(handler)
    ) as client:
        provider = ZhipuLlmProvider(settings, snapshot, client)
        result = await provider.complete("prompt", system="s", max_tokens=1_024, temperature=0.1)

    assert result == "OK"
    assert calls[0] == 1_024
    assert calls[1] == 4_096  # budget grew 4x after reasoning exhausted it


def test_elements_from_rows_drops_decorative_image_strips() -> None:
    # Converted documents export highlighted text as flat colour strips
    # (<= 65px tall) whose OCR text is hallucinated on a solid rectangle:
    # they must vanish from delivery and doc-QA context, while real
    # screenshots and dimension-less assets stay.
    from app.rag.repository import elements_from_rows

    def row(element_type: str, content: str, asset_id=None, w=None, h=None, seq=1):
        return {
            "id": uuid4(),
            "element_type": element_type,
            "content": content,
            "section_path": ["章节"],
            "asset_id": asset_id,
            "vision_caption": "终端截图",
            "ocr_text": "幻觉内容",
            "asset_width": w,
            "asset_height": h,
            "sequence_no": seq,
        }

    real_asset, strip_asset = uuid4(), uuid4()
    rows = [
        row("TEXT", "打开FFC胶囊，检查接触点。", seq=1),
        row("IMAGE", "图片文字：幻觉", asset_id=strip_asset, w=333, h=50, seq=2),
        row("IMAGE", "图片文字：真实截图", asset_id=real_asset, w=1145, h=764, seq=3),
        row("IMAGE", "图片文字：维度缺失保留", asset_id=uuid4(), w=None, h=None, seq=4),
        row("TABLE", "| a | b |", seq=5),
    ]
    elements = elements_from_rows(rows)
    assert [element["element_type"] for element in elements] == [
        "TEXT",
        "IMAGE",
        "IMAGE",
        "TABLE",
    ]
    assert elements[1]["image_asset_id"] == real_asset
    assert elements[1]["image_caption"] == "终端截图"
