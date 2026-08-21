from __future__ import annotations

import asyncio
import base64
import math
from collections.abc import Sequence
from typing import Protocol

import httpx

from app.core.config import Settings
from app.rag.models import ModelSnapshot, RerankDocument, RerankItem, RerankOutcome


class RerankError(RuntimeError):
    def __init__(self, code: str, message: str, *, degradable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.degradable = degradable


class RerankProvider(Protocol):
    provider: str
    model_name: str

    async def rerank(
        self, query: str, documents: Sequence[RerankDocument], top_n: int
    ) -> RerankOutcome: ...

    async def aclose(self) -> None: ...


class FakeRerankProvider:
    provider = "fake"
    model_name = "deterministic-rerank"

    async def rerank(
        self, query: str, documents: Sequence[RerankDocument], top_n: int
    ) -> RerankOutcome:
        del query
        items = [
            RerankItem(index, 1.0 / (index + 1)) for index in range(min(top_n, len(documents)))
        ]
        return RerankOutcome(items, self.provider, self.model_name, {"documents": len(documents)})

    async def aclose(self) -> None:
        return None


class SiliconFlowRerankProvider:
    provider = "siliconflow"

    def __init__(
        self,
        settings: Settings,
        snapshot: ModelSnapshot,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not settings.has_secret(settings.siliconflow_api_key):
            raise RerankError("MODEL_NOT_CONFIGURED", "SiliconFlow Rerank 未配置", degradable=False)
        if snapshot.provider.lower() != "siliconflow":
            raise RerankError("MODEL_NOT_CONFIGURED", "Rerank Provider 不受支持", degradable=False)
        assert settings.siliconflow_api_key is not None
        self.model_name = snapshot.model_name
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            # DB model_config.base_url wins (admin-adjustable per turn);
            # the env setting is the fallback for legacy rows.
            base_url=snapshot.base_url or settings.siliconflow_base_url,
            headers={"Authorization": f"Bearer {settings.siliconflow_api_key.get_secret_value()}"},
            timeout=httpx.Timeout(settings.m5_provider_timeout_seconds),
            follow_redirects=False,
        )

    @staticmethod
    def _document_payload(document: RerankDocument) -> str | dict[str, str]:
        if document.image_bytes is None:
            return document.text
        mime_type = document.image_mime_type or "image/png"
        encoded = base64.b64encode(document.image_bytes).decode("ascii")
        return {
            "text": document.text,
            "image": f"data:{mime_type};base64,{encoded}",
        }

    async def rerank(
        self, query: str, documents: Sequence[RerankDocument], top_n: int
    ) -> RerankOutcome:
        if not documents:
            return RerankOutcome([], self.provider, self.model_name)
        payload = {
            "model": self.model_name,
            "query": query,
            "documents": [self._document_payload(document) for document in documents],
            "top_n": min(top_n, len(documents)),
            "return_documents": False,
        }
        response: httpx.Response | None = None
        for attempt in range(3):
            try:
                response = await self.client.post("rerank", json=payload)
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    raise RerankError(
                        "UPSTREAM_UNAVAILABLE", "Rerank 服务暂时不可用", degradable=True
                    )
                if response.is_error:
                    raise RerankError(
                        "UPSTREAM_PROTOCOL_ERROR", "Rerank 服务请求失败", degradable=False
                    )
                break
            except httpx.TimeoutException as exc:
                if attempt == 2:
                    raise RerankError(
                        "UPSTREAM_TIMEOUT", "Rerank 服务响应超时", degradable=True
                    ) from exc
                await asyncio.sleep(0.5 * (2**attempt))
            except httpx.RequestError as exc:
                if attempt == 2:
                    raise RerankError(
                        "UPSTREAM_UNAVAILABLE", "Rerank 服务暂时不可用", degradable=True
                    ) from exc
                await asyncio.sleep(0.5 * (2**attempt))
        if response is None:
            raise RerankError("UPSTREAM_UNAVAILABLE", "Rerank 服务不可用", degradable=True)
        try:
            body = response.json()
            results = body["results"]
        except (ValueError, KeyError, TypeError) as exc:
            raise RerankError(
                "UPSTREAM_PROTOCOL_ERROR", "Rerank 返回格式异常", degradable=False
            ) from exc
        if not isinstance(results, list) or len(results) > len(documents):
            raise RerankError("UPSTREAM_PROTOCOL_ERROR", "Rerank 返回数量异常", degradable=False)
        items: list[RerankItem] = []
        seen: set[int] = set()
        for result in results:
            index = result.get("index") if isinstance(result, dict) else None
            score = result.get("relevance_score") if isinstance(result, dict) else None
            if (
                not isinstance(index, int)
                or index in seen
                or not 0 <= index < len(documents)
                or not isinstance(score, int | float)
                or not math.isfinite(score)
            ):
                raise RerankError(
                    "UPSTREAM_PROTOCOL_ERROR", "Rerank 返回候选非法", degradable=False
                )
            seen.add(index)
            items.append(RerankItem(index, float(score)))
        if documents and not items:
            raise RerankError("UPSTREAM_PROTOCOL_ERROR", "Rerank 返回为空", degradable=False)
        usage = body.get("meta") if isinstance(body.get("meta"), dict) else {}
        return RerankOutcome(
            items[: min(top_n, len(documents))], self.provider, self.model_name, usage
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()


def create_rerank_provider(settings: Settings, snapshot: ModelSnapshot) -> RerankProvider:
    mode = settings.m5_rerank_provider
    if mode == "fake" or (mode == "auto" and not settings.has_secret(settings.siliconflow_api_key)):
        return FakeRerankProvider()
    return SiliconFlowRerankProvider(settings, snapshot)
