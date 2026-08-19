from __future__ import annotations

import asyncio
import base64
import hashlib
import math
import struct
from collections.abc import Sequence
from typing import Protocol

import httpx

from app.core.config import Settings
from app.ingestion.models import EmbeddingInput


class EmbeddingError(RuntimeError):
    """Embedding generation failed without exposing upstream response content."""


class EmbeddingProvider(Protocol):
    provider: str
    model_name: str

    async def embed(self, inputs: Sequence[EmbeddingInput]) -> list[list[float]]: ...

    async def aclose(self) -> None: ...


class FakeEmbeddingProvider:
    provider = "fake"
    model_name = "deterministic-shake256"

    def __init__(self, dimension: int) -> None:
        self.dimension = dimension

    async def embed(self, inputs: Sequence[EmbeddingInput]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for item in inputs:
            seed = item.text.encode("utf-8") + (item.image_bytes or b"")
            raw = hashlib.shake_256(seed).digest(self.dimension * 4)
            values = [
                (number / 2_147_483_647.5) - 1.0 for (number,) in struct.iter_unpack(">I", raw)
            ]
            norm = math.sqrt(sum(value * value for value in values)) or 1.0
            vectors.append([value / norm for value in values])
        return vectors

    async def aclose(self) -> None:
        return None


class SiliconFlowEmbeddingProvider:
    provider = "siliconflow"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        if not settings.has_secret(settings.siliconflow_api_key):
            raise EmbeddingError("SiliconFlow Embedding 未配置")
        assert settings.siliconflow_api_key is not None
        self.model_name = settings.siliconflow_embedding_model
        self.dimension = settings.embedding_dimension
        self.batch_size = settings.m2_embedding_batch_size
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=settings.siliconflow_base_url,
            headers={"Authorization": f"Bearer {settings.siliconflow_api_key.get_secret_value()}"},
            timeout=httpx.Timeout(settings.m0_provider_timeout_seconds),
            follow_redirects=False,
        )

    @staticmethod
    def _payload_input(item: EmbeddingInput) -> str | dict[str, str]:
        if item.image_bytes is None:
            return item.text
        mime_type = item.image_mime_type or "image/png"
        encoded = base64.b64encode(item.image_bytes).decode("ascii")
        return {
            "text": item.text,
            "image": f"data:{mime_type};base64,{encoded}",
        }

    async def embed(self, inputs: Sequence[EmbeddingInput]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for offset in range(0, len(inputs), self.batch_size):
            batch = inputs[offset : offset + self.batch_size]
            payload = {
                "model": self.model_name,
                "input": [self._payload_input(item) for item in batch],
                "dimensions": self.dimension,
                "encoding_format": "float",
            }
            response: httpx.Response | None = None
            for attempt in range(3):
                try:
                    response = await self.client.post("embeddings", json=payload)
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt < 2:
                            await asyncio.sleep(0.5 * (2**attempt))
                            continue
                    response.raise_for_status()
                    break
                except httpx.RequestError as exc:
                    if attempt == 2:
                        raise EmbeddingError("Embedding 上游网络异常") from exc
                    await asyncio.sleep(0.5 * (2**attempt))
                except httpx.HTTPStatusError as exc:
                    raise EmbeddingError(
                        f"Embedding 上游返回 HTTP {exc.response.status_code}"
                    ) from exc
            if response is None or response.is_error:
                raise EmbeddingError("Embedding 上游调用失败")
            body = response.json()
            data = body.get("data") if isinstance(body, dict) else None
            if not isinstance(data, list) or len(data) != len(batch):
                raise EmbeddingError("Embedding 返回数量不匹配")
            ordered = sorted(data, key=lambda item: item.get("index", 0))
            for result in ordered:
                vector = result.get("embedding") if isinstance(result, dict) else None
                if not isinstance(vector, list) or len(vector) != self.dimension:
                    raise EmbeddingError("Embedding 返回维度不匹配")
                if not all(
                    isinstance(value, int | float) and math.isfinite(value) for value in vector
                ):
                    raise EmbeddingError("Embedding 包含非法数值")
                vectors.append([float(value) for value in vector])
        return vectors

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()


def create_embedding_provider(settings: Settings) -> EmbeddingProvider:
    mode = settings.m2_embedding_provider
    if mode == "fake" or (mode == "auto" and not settings.has_secret(settings.siliconflow_api_key)):
        return FakeEmbeddingProvider(settings.embedding_dimension)
    return SiliconFlowEmbeddingProvider(settings)
