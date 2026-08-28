"""Minimal live connectivity probes for admin-managed model configs.

Each probe issues one small upstream request with the effective config
(DB row + decrypted key, falling back to env settings) and reports
ok/latency without ever surfacing key material or raw upstream bodies.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from pydantic import SecretStr

from app.core.config import Settings
from app.m0.fixtures import image_data_url
from app.rag.model_providers import (
    environment_api_key,
    environment_base_url,
    is_supported_provider,
    normalize_provider,
    supported_providers,
)


def _effective_settings(
    settings: Settings, model_type: str, provider: str
) -> tuple[str, SecretStr | None]:
    return (
        environment_base_url(settings, model_type, provider) or "",
        environment_api_key(settings, model_type, provider),
    )


def _image_probe_payload(model_name: str, prompt: str, max_tokens: int) -> dict[str, Any]:
    return {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_data_url()}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
    }


def _payload_for(
    settings: Settings, model_type: str, provider: str, model_name: str
) -> dict[str, Any] | None:
    if model_type == "LLM":
        return {
            "model": model_name,
            "messages": [{"role": "user", "content": "ping"}],
            "stream": False,
            "max_tokens": 1,
        }
    if model_type == "RERANK":
        return {
            "model": model_name,
            "query": "ping",
            "documents": ["ping"],
            "top_n": 1,
            "return_documents": False,
        }
    if model_type == "EMBEDDING":
        return {
            "model": model_name,
            "input": "ping",
            "dimensions": settings.embedding_dimension,
            "encoding_format": "float",
        }
    if model_type == "OCR":
        return _image_probe_payload(model_name, "提取图片中的文字，只返回识别结果。", 64)
    if model_type == "VISION":
        payload = _image_probe_payload(model_name, "简要说明图片内容。", 64)
        if provider == "zhipu":
            payload["thinking"] = {"type": "disabled"}
        return payload
    return None


def _endpoint_for(model_type: str) -> str:
    if model_type in {"LLM", "OCR", "VISION"}:
        return "chat/completions"
    if model_type == "RERANK":
        return "rerank"
    return "embeddings"


def _parse_dimension(model_type: str, body: Any) -> int | None:
    if model_type != "EMBEDDING" or not isinstance(body, dict):
        return None
    data = body.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        embedding = data[0].get("embedding")
        if isinstance(embedding, list):
            return len(embedding)
    return None


async def probe_model(
    settings: Settings,
    *,
    model_type: str,
    provider: str,
    model_name: str,
    base_url: str | None,
    api_key: SecretStr | None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Returns {ok, model_type, provider, model_name, latency_ms, detail, dimension?}."""
    result: dict[str, Any] = {
        "ok": False,
        "model_type": model_type,
        "provider": provider,
        "model_name": model_name,
        "latency_ms": 0,
        "detail": "",
        "dimension": None,
    }
    if not supported_providers(model_type):
        result["detail"] = f"不支持的模型类型：{model_type}"
        return result
    provider = normalize_provider(provider)
    result["provider"] = provider
    if not is_supported_provider(model_type, provider):
        supported = "、".join(supported_providers(model_type))
        result["detail"] = f"Provider 不受支持：{provider}（支持：{supported}）"
        return result

    env_base_url, env_key = _effective_settings(settings, model_type, provider)
    effective_key = api_key or env_key
    if not settings.has_secret(effective_key):
        result["detail"] = "未配置 API 密钥"
        return result
    assert effective_key is not None

    effective_base_url = base_url or env_base_url
    payload = _payload_for(settings, model_type, provider, model_name)
    if payload is None:
        result["detail"] = "探测请求构造失败"
        return result
    url = effective_base_url.rstrip("/") + "/" + _endpoint_for(model_type)

    owns_client = client is None
    probe_client = client or httpx.AsyncClient(
        base_url=effective_base_url,
        headers={"Authorization": f"Bearer {effective_key.get_secret_value()}"},
        timeout=httpx.Timeout(settings.model_probe_timeout_seconds),
        follow_redirects=False,
    )
    started = time.perf_counter()
    try:
        response = await probe_client.post(url, json=payload)
        result["latency_ms"] = max(0, round((time.perf_counter() - started) * 1000))
        if response.status_code >= 400:
            result["detail"] = f"上游返回 HTTP {response.status_code}"
            return result
        body: Any = None
        try:
            body = response.json()
        except ValueError:
            body = None
        dimension = _parse_dimension(model_type, body)
        if model_type == "EMBEDDING":
            if dimension is None:
                result["detail"] = "上游未返回有效向量"
                return result
            result["dimension"] = dimension
            if dimension != settings.embedding_dimension:
                result["detail"] = (
                    f"输出维度 {dimension} 与冻结维度 {settings.embedding_dimension} 不一致"
                )
                return result
        result["ok"] = True
        result["detail"] = "连通正常"
        return result
    except httpx.RequestError:
        result["latency_ms"] = max(0, round((time.perf_counter() - started) * 1000))
        result["detail"] = "上游网络异常或超时"
        return result
    finally:
        if owns_client:
            await probe_client.aclose()
