from __future__ import annotations

import asyncio
import base64
import hashlib
from typing import Any, Literal, Protocol

import httpx

from app.core.config import Settings
from app.ingestion.models import ImageUnderstandingResult

ImageCapability = Literal["OCR", "VISION"]


class ImageUnderstandingError(RuntimeError):
    """Image understanding failed without exposing upstream content or credentials."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ImageUnderstandingProvider(Protocol):
    capability: ImageCapability
    provider: str
    model_name: str

    async def analyze(self, image_bytes: bytes, mime_type: str) -> ImageUnderstandingResult: ...

    async def aclose(self) -> None: ...


class DisabledImageUnderstandingProvider:
    provider = "disabled"
    model_name = "disabled"

    def __init__(self, capability: ImageCapability) -> None:
        self.capability = capability

    async def analyze(self, image_bytes: bytes, mime_type: str) -> ImageUnderstandingResult:
        _ = image_bytes, mime_type
        raise ImageUnderstandingError("DISABLED", f"{self.capability} 已禁用")

    async def aclose(self) -> None:
        return None


class UnavailableImageUnderstandingProvider:
    provider = "unavailable"

    def __init__(self, capability: ImageCapability, model_name: str) -> None:
        self.capability = capability
        self.model_name = model_name

    async def analyze(self, image_bytes: bytes, mime_type: str) -> ImageUnderstandingResult:
        _ = image_bytes, mime_type
        raise ImageUnderstandingError("NOT_CONFIGURED", f"{self.capability} 凭据未配置")

    async def aclose(self) -> None:
        return None


class FakeImageUnderstandingProvider:
    provider = "fake"

    def __init__(self, capability: ImageCapability) -> None:
        self.capability = capability
        self.model_name = f"deterministic-{capability.lower()}"

    async def analyze(self, image_bytes: bytes, mime_type: str) -> ImageUnderstandingResult:
        _ = mime_type
        digest = hashlib.sha256(image_bytes).hexdigest()[:12]
        if self.capability == "OCR":
            value = f"模拟OCR文本 image-{digest}"
        else:
            value = f"产品文档截图，图片标识 image-{digest}。"
        return ImageUnderstandingResult(value, self.provider, self.model_name)

    async def aclose(self) -> None:
        return None


class OpenAiCompatibleImageProvider:
    def __init__(
        self,
        *,
        capability: ImageCapability,
        provider: str,
        model_name: str,
        base_url: str,
        api_key: str,
        timeout_seconds: float,
        max_tokens: int,
        max_output_chars: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.capability = capability
        self.provider = provider
        self.model_name = model_name
        self.max_tokens = max_tokens
        self.max_output_chars = max_output_chars
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        )

    def _prompt(self) -> str:
        if self.capability == "OCR":
            return "提取图片中的文字，只返回识别结果。"
        return (
            "请为企业产品知识检索生成事实性图片描述。描述界面、组件、状态、"
            "操作关系和关键数值，不要臆测图片外的信息，不要使用 Markdown。"
        )

    async def analyze(self, image_bytes: bytes, mime_type: str) -> ImageUnderstandingResult:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                        },
                        {"type": "text", "text": self._prompt()},
                    ],
                }
            ],
            "max_tokens": self.max_tokens,
            "temperature": 0,
        }
        if self.provider == "zhipu":
            payload["thinking"] = {"type": "disabled"}

        response: httpx.Response | None = None
        for attempt in range(3):
            try:
                response = await self.client.post("chat/completions", json=payload)
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                response.raise_for_status()
                break
            except httpx.RequestError as exc:
                if attempt == 2:
                    raise ImageUnderstandingError(
                        "UPSTREAM_NETWORK_ERROR", f"{self.capability} 上游网络异常"
                    ) from exc
                await asyncio.sleep(0.5 * (2**attempt))
            except httpx.HTTPStatusError as exc:
                raise ImageUnderstandingError(
                    "UPSTREAM_HTTP_ERROR",
                    f"{self.capability} 上游返回 HTTP {exc.response.status_code}",
                ) from exc
        if response is None or response.is_error:
            raise ImageUnderstandingError("UPSTREAM_ERROR", f"{self.capability} 上游调用失败")
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ImageUnderstandingError(
                "INVALID_RESPONSE", f"{self.capability} 返回格式错误"
            ) from exc
        if not isinstance(content, str):
            raise ImageUnderstandingError("INVALID_RESPONSE", f"{self.capability} 返回文本缺失")
        normalized = content.strip()
        if self.capability == "OCR" and not normalized:
            raise ImageUnderstandingError("EMPTY_RESULT", "OCR 返回文本为空")
        if len(normalized) > self.max_output_chars:
            normalized = normalized[: self.max_output_chars]
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        request_id = next(
            (
                response.headers[name]
                for name in ("x-request-id", "x-siliconcloud-trace-id", "x-zhipu-request-id")
                if name in response.headers
            ),
            body.get("request_id") or body.get("id"),
        )
        return ImageUnderstandingResult(
            normalized,
            self.provider,
            self.model_name,
            {
                "usage": usage,
                "request_id": request_id if isinstance(request_id, str) else None,
            },
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()


def create_ocr_provider(settings: Settings) -> ImageUnderstandingProvider:
    mode = settings.m4_ocr_provider
    if mode == "disabled":
        return DisabledImageUnderstandingProvider("OCR")
    if mode == "fake" or (mode == "auto" and not settings.has_secret(settings.siliconflow_api_key)):
        return FakeImageUnderstandingProvider("OCR")
    if not settings.has_secret(settings.siliconflow_api_key):
        return UnavailableImageUnderstandingProvider("OCR", settings.siliconflow_ocr_model)
    assert settings.siliconflow_api_key is not None
    return OpenAiCompatibleImageProvider(
        capability="OCR",
        provider="siliconflow",
        model_name=settings.siliconflow_ocr_model,
        base_url=settings.siliconflow_base_url,
        api_key=settings.siliconflow_api_key.get_secret_value(),
        timeout_seconds=settings.m4_provider_timeout_seconds,
        max_tokens=settings.m4_ocr_max_tokens,
        max_output_chars=settings.m4_max_output_chars,
    )


def create_vision_provider(settings: Settings) -> ImageUnderstandingProvider:
    mode = settings.m4_vision_provider
    if mode == "disabled":
        return DisabledImageUnderstandingProvider("VISION")
    if mode == "fake" or (mode == "auto" and not settings.has_secret(settings.zhipu_api_key)):
        return FakeImageUnderstandingProvider("VISION")
    if not settings.has_secret(settings.zhipu_api_key):
        return UnavailableImageUnderstandingProvider("VISION", settings.zhipu_vision_model)
    assert settings.zhipu_api_key is not None
    return OpenAiCompatibleImageProvider(
        capability="VISION",
        provider="zhipu",
        model_name=settings.zhipu_vision_model,
        base_url=settings.zhipu_base_url,
        api_key=settings.zhipu_api_key.get_secret_value(),
        timeout_seconds=settings.m4_provider_timeout_seconds,
        max_tokens=settings.m4_vision_max_tokens,
        max_output_chars=settings.m4_max_output_chars,
    )
