from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, Protocol

import httpx

from app.core.config import Settings
from app.rag.models import ModelSnapshot
from app.rag.prompt import RAG_SYSTEM_GUARD


class LlmError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.retryable = retryable


class LlmProvider(Protocol):
    usage: dict[str, Any]

    def stream(self, prompt: str) -> AsyncIterator[str]: ...

    async def aclose(self) -> None: ...


class FakeLlmProvider:
    def __init__(self, chunks: list[str] | None = None) -> None:
        self.chunks = chunks or ["根据知识库资料，", "相关配置请参见所列说明。[S1]"]
        self.usage: dict[str, Any] = {"provider": "fake"}

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        del prompt
        for chunk in self.chunks:
            await asyncio.sleep(0)
            yield chunk

    async def aclose(self) -> None:
        return None


class ZhipuLlmProvider:
    def __init__(
        self,
        settings: Settings,
        snapshot: ModelSnapshot,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not settings.has_secret(settings.zhipu_api_key):
            raise LlmError("MODEL_NOT_CONFIGURED", "智谱 LLM 未配置", retryable=False)
        if snapshot.provider.lower() != "zhipu":
            raise LlmError("MODEL_NOT_CONFIGURED", "LLM Provider 不受支持", retryable=False)
        assert settings.zhipu_api_key is not None
        self.model_name = snapshot.model_name
        self.parameters = snapshot.parameters
        self.settings = settings
        self.usage: dict[str, Any] = {}
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=settings.zhipu_base_url,
            headers={"Authorization": f"Bearer {settings.zhipu_api_key.get_secret_value()}"},
            timeout=httpx.Timeout(settings.m3_provider_timeout_seconds),
            follow_redirects=False,
        )

    def _payload(self, prompt: str) -> dict[str, Any]:
        max_tokens = int(self.parameters.get("max_tokens", self.settings.m3_llm_max_tokens))
        temperature = float(self.parameters.get("temperature", self.settings.m3_llm_temperature))
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": RAG_SYSTEM_GUARD},
                {"role": "user", "content": prompt},
            ],
            "stream": True,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        thinking = self.parameters.get("thinking")
        if thinking is not None and self.settings.m3_llm_thinking_enabled:
            payload["thinking"] = thinking
        return payload

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        try:
            async with self.client.stream(
                "POST", "chat/completions", json=self._payload(prompt)
            ) as response:
                if response.status_code == 429 or response.status_code >= 500:
                    raise LlmError("UPSTREAM_UNAVAILABLE", "模型服务暂时不可用", retryable=True)
                if response.is_error:
                    raise LlmError("UPSTREAM_UNAVAILABLE", "模型服务请求失败", retryable=False)
                async for line in response.aiter_lines():
                    stripped = line.strip()
                    if not stripped or stripped.startswith(":") or not stripped.startswith("data:"):
                        continue
                    data = stripped[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise LlmError(
                            "UPSTREAM_PROTOCOL_ERROR", "模型流响应格式异常", retryable=False
                        ) from exc
                    if isinstance(event.get("usage"), dict):
                        self.usage = dict(event["usage"])
                    choices = event.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") if isinstance(choice, dict) else None
                    content = delta.get("content") if isinstance(delta, dict) else None
                    if isinstance(content, str) and content:
                        yield content
        except LlmError:
            raise
        except httpx.TimeoutException as exc:
            raise LlmError("UPSTREAM_TIMEOUT", "模型服务响应超时", retryable=True) from exc
        except httpx.RequestError as exc:
            raise LlmError("UPSTREAM_UNAVAILABLE", "模型服务暂时不可用", retryable=True) from exc

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()


def create_llm_provider(settings: Settings, snapshot: ModelSnapshot) -> LlmProvider:
    mode = settings.m3_llm_provider
    if mode == "fake" or (mode == "auto" and not settings.has_secret(settings.zhipu_api_key)):
        return FakeLlmProvider()
    return ZhipuLlmProvider(settings, snapshot)
