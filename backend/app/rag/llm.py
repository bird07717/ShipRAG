from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.rag.model_providers import (
    environment_api_key,
    environment_base_url,
    get_provider_capability,
    normalize_provider,
)
from app.rag.models import ModelSnapshot
from app.rag.prompt import RAG_SYSTEM_GUARD


class LlmError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.retryable = retryable
        # Non-sensitive diagnostic context (e.g. upstream HTTP status) that
        # does not belong in the user-facing message but is persisted in the
        # trace error payload for operators.
        self.detail = detail


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


def _validate_structured_json(schema: type[StructuredModel], raw: str) -> StructuredModel:
    """Validate a model result while tolerating harmless JSON wrappers.

    Some OpenAI-compatible endpoints return a valid object inside a Markdown
    fence or add a short explanatory prefix even when JSON mode is requested.
    The Profile pipeline must still reject invalid schemas, but should not
    degrade to a fallback merely because transport-compatible providers add
    that presentation wrapper.
    """
    candidates = [raw.strip()]
    decoder = json.JSONDecoder()
    for offset, character in enumerate(raw):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(raw[offset:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidate = json.dumps(value, ensure_ascii=False)
            if candidate not in candidates:
                candidates.append(candidate)

    first_error: ValidationError | ValueError | None = None
    for candidate in candidates:
        try:
            return schema.model_validate_json(candidate)
        except (ValidationError, ValueError) as exc:
            if first_error is None:
                first_error = exc
    assert first_error is not None
    raise first_error


class LlmProvider(Protocol):
    usage: dict[str, Any]

    def stream(self, prompt: str) -> AsyncIterator[str]: ...

    async def complete(
        self,
        prompt: str,
        *,
        system: str,
        max_tokens: int,
        temperature: float,
        timeout_seconds: float | None = None,
    ) -> str: ...

    async def complete_structured(
        self,
        prompt: str,
        *,
        system: str,
        schema: type[StructuredModel],
        max_tokens: int,
        temperature: float,
        timeout_seconds: float | None = None,
    ) -> StructuredModel: ...

    async def aclose(self) -> None: ...


class FakeLlmProvider:
    def __init__(
        self, chunks: list[str] | None = None, completions: list[str] | None = None
    ) -> None:
        self.chunks = chunks or ["根据知识库资料，", "相关配置请参见所列说明。[S1]"]
        self.completions = list(completions or [])
        self.usage: dict[str, Any] = {"provider": "fake"}

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        del prompt
        for chunk in self.chunks:
            await asyncio.sleep(0)
            yield chunk

    async def complete(
        self,
        prompt: str,
        *,
        system: str,
        max_tokens: int,
        temperature: float,
        timeout_seconds: float | None = None,
    ) -> str:
        del prompt, system, max_tokens, temperature, timeout_seconds
        if not self.completions:
            raise LlmError("UPSTREAM_UNAVAILABLE", "Fake complete 未配置", retryable=False)
        return self.completions.pop(0)

    async def complete_structured(
        self,
        prompt: str,
        *,
        system: str,
        schema: type[StructuredModel],
        max_tokens: int,
        temperature: float,
        timeout_seconds: float | None = None,
    ) -> StructuredModel:
        return await _complete_structured(
            self,
            prompt,
            system=system,
            schema=schema,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
        )

    async def aclose(self) -> None:
        return None


class ZhipuLlmProvider:
    """OpenAI-compatible chat provider for the supported LLM vendors.

    The class name is retained for backwards-compatible imports.  Provider
    selection, credentials and endpoint fallback all come from the snapshot's
    provider value.
    """

    # Utility calls cap their growth here: reasoning budget retries double a
    # few times, but a prompt that needs more than this is a prompt problem.
    _THINKING_MAX_TOKENS_CEILING = 16_384

    def __init__(
        self,
        settings: Settings,
        snapshot: ModelSnapshot,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.provider = normalize_provider(snapshot.provider)
        capability = get_provider_capability("LLM", self.provider)
        if capability is None:
            raise LlmError("MODEL_NOT_CONFIGURED", "LLM Provider 不受支持", retryable=False)
        # DB-stored key (decrypted per turn) wins; the provider's environment
        # key is the bootstrap fallback.
        api_key = snapshot.api_key or environment_api_key(settings, "LLM", self.provider)
        if not settings.has_secret(api_key):
            raise LlmError("MODEL_NOT_CONFIGURED", f"{self.provider} LLM 未配置", retryable=False)
        assert api_key is not None
        self.model_name = snapshot.model_name
        self.parameters = snapshot.parameters
        self.settings = settings
        self._supports_json_mode = capability.supports_json_mode
        self.usage: dict[str, Any] = {}
        # Set after the upstream rejects a thinking-control payload with
        # "该模型始终思考" (HTTP 400, code 1210).  Always-thinking models
        # (e.g. GLM-5.3-Flash) must not receive a thinking key at all.
        self._thinking_unsupported = False
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            # DB model_config.base_url wins (admin-adjustable per turn);
            # the selected provider's endpoint is the fallback for legacy rows.
            base_url=snapshot.base_url
            or environment_base_url(settings, "LLM", self.provider)
            or settings.zhipu_base_url,
            headers={"Authorization": f"Bearer {api_key.get_secret_value()}"},
            timeout=httpx.Timeout(settings.m3_provider_timeout_seconds),
            follow_redirects=False,
        )

    @staticmethod
    def _thinking_rejection(response: httpx.Response) -> bool:
        if response.status_code != 400:
            return False
        body = response.text
        return '"1210"' in body or "不支持关闭思考" in body or "始终思考" in body

    @staticmethod
    async def _stream_thinking_rejection(response: httpx.Response) -> bool:
        """Inspect a streaming 400 only after consuming its response body.

        ``httpx.Response.text`` is unavailable inside ``client.stream`` until
        the response has been read. Accessing it first converted a normal
        upstream thinking-control rejection into ``ResponseNotRead`` and then
        an unrelated INTERNAL_ERROR in the RAG answer stage.
        """
        if response.status_code != 400:
            return False
        body = (await response.aread()).decode(errors="replace")
        return '"1210"' in body or "不支持关闭思考" in body or "始终思考" in body

    def _apply_thinking(self, payload: dict[str, Any], thinking: dict[str, Any]) -> None:
        if self.provider == "zhipu":
            payload["thinking"] = thinking
        elif self.provider == "siliconflow":
            payload["enable_thinking"] = thinking.get("type") == "enabled"

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
        if isinstance(thinking, dict) and not self._thinking_unsupported:
            self._apply_thinking(payload, thinking)
        return payload

    async def complete(
        self,
        prompt: str,
        *,
        system: str,
        max_tokens: int,
        temperature: float,
        timeout_seconds: float | None = None,
    ) -> str:
        return await self._complete_response(
            prompt,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
        )

    async def _complete_response(
        self,
        prompt: str,
        *,
        system: str,
        max_tokens: int,
        temperature: float,
        timeout_seconds: float | None,
        response_format: dict[str, str] | None = None,
    ) -> str:
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if not self._thinking_unsupported:
            # complete() serves short utility calls (e.g. query rewriting);
            # reasoning would only burn the token and latency budgets.
            # Always-thinking models reject the key with HTTP 400/1210; the
            # retry below strips it once and remembers for this provider.
            self._apply_thinking(payload, {"type": "disabled"})
        if response_format is not None:
            payload["response_format"] = response_format
        request_timeout = httpx.Timeout(timeout_seconds) if timeout_seconds is not None else None
        try:
            effective_max_tokens = max_tokens
            for _attempt in range(3):
                payload["max_tokens"] = effective_max_tokens
                response = await self.client.post(
                    "chat/completions", json=payload, timeout=request_timeout
                )
                if self.provider == "zhipu" and self._thinking_rejection(response):
                    self._thinking_unsupported = True
                    payload.pop("thinking", None)
                    continue
                if response.status_code == 429 or response.status_code >= 500:
                    raise LlmError(
                        "UPSTREAM_UNAVAILABLE",
                        "模型服务暂时不可用",
                        retryable=True,
                        detail=f"HTTP {response.status_code}",
                    )
                if response.is_error:
                    raise LlmError(
                        "UPSTREAM_UNAVAILABLE",
                        "模型服务请求失败",
                        retryable=False,
                        detail=f"HTTP {response.status_code}",
                    )
                data = response.json()
                if isinstance(data.get("usage"), dict):
                    self.usage = dict(data["usage"])
                choices = data.get("choices")
                if not isinstance(choices, list) or not choices:
                    raise LlmError("UPSTREAM_PROTOCOL_ERROR", "模型响应格式异常", retryable=False)
                message = choices[0].get("message") if isinstance(choices[0], dict) else None
                content = message.get("content") if isinstance(message, dict) else None
                if isinstance(content, str) and content.strip():
                    return content.strip()
                # Always-thinking models spend completion tokens on reasoning
                # before any visible content: a length-finish with reasoning
                # means the budget, not the model, ran out.  Grow it and retry.
                finish_reason = (
                    choices[0].get("finish_reason") if isinstance(choices[0], dict) else None
                )
                has_reasoning = isinstance(message, dict) and bool(message.get("reasoning_content"))
                if (
                    finish_reason == "length"
                    and has_reasoning
                    and effective_max_tokens < self._THINKING_MAX_TOKENS_CEILING
                ):
                    effective_max_tokens = min(
                        self._THINKING_MAX_TOKENS_CEILING, effective_max_tokens * 4
                    )
                    continue
                raise LlmError("UPSTREAM_PROTOCOL_ERROR", "模型响应为空", retryable=False)
            raise LlmError("UPSTREAM_PROTOCOL_ERROR", "模型响应为空", retryable=False)
        except LlmError:
            raise
        except httpx.TimeoutException as exc:
            raise LlmError("UPSTREAM_TIMEOUT", "模型服务响应超时", retryable=True) from exc
        except httpx.RequestError as exc:
            raise LlmError("UPSTREAM_UNAVAILABLE", "模型服务暂时不可用", retryable=True) from exc

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        payload = self._payload(prompt)
        try:
            for _attempt in range(2):
                async with self.client.stream("POST", "chat/completions", json=payload) as response:
                    if self.provider == "zhipu" and await self._stream_thinking_rejection(response):
                        # Always-thinking model: drop the key, retry once and
                        # remember so later calls skip the doomed round trip.
                        self._thinking_unsupported = True
                        payload.pop("thinking", None)
                        continue
                    if response.status_code == 429 or response.status_code >= 500:
                        raise LlmError(
                            "UPSTREAM_UNAVAILABLE",
                            "模型服务暂时不可用",
                            retryable=True,
                            detail=f"HTTP {response.status_code}",
                        )
                    if response.is_error:
                        raise LlmError(
                            "UPSTREAM_UNAVAILABLE",
                            "模型服务请求失败",
                            retryable=False,
                            detail=f"HTTP {response.status_code}",
                        )
                    async for line in response.aiter_lines():
                        stripped = line.strip()
                        if (
                            not stripped
                            or stripped.startswith(":")
                            or not stripped.startswith("data:")
                        ):
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
                    return
            raise LlmError(
                "UPSTREAM_UNAVAILABLE",
                "模型服务请求失败",
                retryable=False,
                detail="thinking 控制被上游拒绝",
            )
        except LlmError:
            raise
        except httpx.TimeoutException as exc:
            raise LlmError("UPSTREAM_TIMEOUT", "模型服务响应超时", retryable=True) from exc
        except httpx.RequestError as exc:
            raise LlmError("UPSTREAM_UNAVAILABLE", "模型服务暂时不可用", retryable=True) from exc

    async def complete_structured(
        self,
        prompt: str,
        *,
        system: str,
        schema: type[StructuredModel],
        max_tokens: int,
        temperature: float,
        timeout_seconds: float | None = None,
    ) -> StructuredModel:
        # GLM supports JSON mode.  Without it, a long focused-document prompt
        # can occasionally produce prose or a Markdown fence even after the
        # normal schema repair retry, which leaves a technical-support turn
        # needlessly failed.
        current_prompt = prompt
        for attempt in range(2):
            raw = await self._complete_response(
                current_prompt,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout_seconds=timeout_seconds,
                response_format={"type": "json_object"} if self._supports_json_mode else None,
            )
            self.usage["structured_attempts"] = attempt + 1
            try:
                return _validate_structured_json(schema, raw)
            except (ValidationError, ValueError) as exc:
                if attempt:
                    raise LlmError(
                        "STRUCTURED_OUTPUT_INVALID",
                        "模型未返回有效的结构化结果",
                        retryable=False,
                        detail=str(exc)[:500],
                    ) from exc
                current_prompt = (
                    prompt + "\n上一次输出不符合 JSON Schema。"
                    "仅重新输出完整 JSON，不要 Markdown。校验错误：" + str(exc)[:500]
                )
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()


async def _complete_structured(
    provider: LlmProvider,
    prompt: str,
    *,
    system: str,
    schema: type[StructuredModel],
    max_tokens: int,
    temperature: float,
    timeout_seconds: float | None,
) -> StructuredModel:
    """Collect and validate JSON, allowing one repair request only."""
    current_prompt = prompt
    for attempt in range(2):
        raw = await provider.complete(
            current_prompt,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
        )
        # Keep lightweight operational metadata alongside provider usage so
        # Support traces can distinguish a first-pass structured completion
        # from a schema-repair retry without retaining the raw model output.
        provider.usage["structured_attempts"] = attempt + 1
        try:
            return _validate_structured_json(schema, raw)
        except (ValidationError, ValueError) as exc:
            if attempt:
                raise LlmError(
                    "STRUCTURED_OUTPUT_INVALID",
                    "模型未返回有效的结构化结果",
                    retryable=False,
                    detail=str(exc)[:500],
                ) from exc
            current_prompt = (
                prompt
                + "\n上一次输出不符合 JSON Schema。仅重新输出完整 JSON，不要 Markdown。校验错误："
                + str(exc)[:500]
            )
    raise AssertionError("unreachable")


def create_llm_provider(settings: Settings, snapshot: ModelSnapshot) -> LlmProvider:
    mode = settings.m3_llm_provider
    provider = normalize_provider(snapshot.provider)
    has_any_key = settings.has_secret(
        environment_api_key(settings, "LLM", provider)
    ) or settings.has_secret(snapshot.api_key)
    if mode == "fake":
        if settings.app_env in {"staging", "production"}:
            raise LlmError(
                "FAKE_PROVIDER_FORBIDDEN",
                "当前环境禁止使用模拟模型",
                retryable=False,
            )
        return FakeLlmProvider()
    if mode == "auto" and not has_any_key:
        if settings.app_env == "test":
            return FakeLlmProvider()
        raise LlmError(
            "MODEL_NOT_CONFIGURED",
            "LLM 模型或 API Key 未配置",
            retryable=False,
        )
    return ZhipuLlmProvider(settings, snapshot)
