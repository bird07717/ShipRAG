from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

import httpx

from app.core.config import Settings
from app.m0.fixtures import image_data_url
from app.m0.models import ProbeReport, ProbeResult

JsonObject = dict[str, Any]


class ContractFailure(Exception):
    """An upstream response does not satisfy the frozen M0 contract."""


class OnlineContractProbe:
    def __init__(
        self,
        settings: Settings,
        siliconflow_client: httpx.AsyncClient | None = None,
        zhipu_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._siliconflow_client = siliconflow_client
        self._zhipu_client = zhipu_client
        self._owned_clients: list[httpx.AsyncClient] = []

    async def run(self) -> ProbeReport:
        siliconflow_available = self.settings.has_secret(self.settings.siliconflow_api_key)
        zhipu_available = self.settings.has_secret(self.settings.zhipu_api_key)
        if self._siliconflow_client is None and siliconflow_available:
            self._siliconflow_client = self._client(
                self.settings.siliconflow_base_url,
                self.settings.siliconflow_api_key,
            )
            self._owned_clients.append(self._siliconflow_client)
        if self._zhipu_client is None and zhipu_available:
            self._zhipu_client = self._client(
                self.settings.zhipu_base_url,
                self.settings.zhipu_api_key,
            )
            self._owned_clients.append(self._zhipu_client)

        results: list[ProbeResult] = []
        try:
            if self._siliconflow_client is None:
                results.extend(self._blocked_siliconflow())
            else:
                results.extend(await self._run_siliconflow())
            if self._zhipu_client is None:
                results.extend(self._blocked_zhipu())
            else:
                results.extend(await self._run_zhipu())
        finally:
            await asyncio.gather(*(client.aclose() for client in self._owned_clients))
        return ProbeReport(results=results)

    def _client(self, base_url: str, secret: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {secret.get_secret_value()}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(self.settings.m0_provider_timeout_seconds),
            follow_redirects=False,
        )

    async def _run_siliconflow(self) -> list[ProbeResult]:
        siliconflow_client = self._siliconflow_client
        assert siliconflow_client is not None
        embedding_model = self.settings.siliconflow_embedding_model
        dimension = self.settings.m0_target_embedding_dimension
        image = image_data_url()
        cases: list[tuple[str, str, JsonObject, int]] = [
            ("embedding_text", "text", {"input": "数据库默认端口为3306"}, 1),
            (
                "embedding_text_batch",
                "batch",
                {"input": ["数据库配置", {"text": "默认端口3306"}]},
                2,
            ),
            ("embedding_image", "image", {"input": {"image": image}}, 1),
            (
                "embedding_mixed_list",
                "mixed_batch",
                {"input": [{"text": "数据库端口截图"}, {"image": image}]},
                2,
            ),
            (
                "embedding_fused_object",
                "fused_object",
                {"input": {"text": "数据库端口截图", "image": image}},
                1,
            ),
        ]
        results: list[ProbeResult] = []
        for name, mode, body, expected_count in cases:
            payload: JsonObject = {
                "model": embedding_model,
                "dimensions": dimension,
                "encoding_format": "float",
                **body,
            }
            results.append(
                await self._execute(
                    name,
                    "SiliconFlow",
                    embedding_model,
                    partial(self._probe_embedding, payload, expected_count, mode),
                )
            )

        rerank_model = self.settings.siliconflow_rerank_model
        results.append(
            await self._execute(
                "rerank_text",
                "SiliconFlow",
                rerank_model,
                lambda: self._probe_rerank(
                    {
                        "model": rerank_model,
                        "query": "数据库默认端口是什么？",
                        "documents": ["默认端口为3306", "默认端口为8080"],
                        "top_n": 2,
                        "return_documents": True,
                    },
                    expected_top_index=0,
                ),
            )
        )
        results.append(
            await self._execute(
                "rerank_multimodal",
                "SiliconFlow",
                rerank_model,
                lambda: self._probe_rerank(
                    {
                        "model": rerank_model,
                        "query": "数据库端口截图",
                        "documents": [{"image": image}, {"text": "这是一段无关文字"}],
                        "top_n": 2,
                        "return_documents": False,
                    },
                    expected_top_index=0,
                ),
            )
        )

        ocr_model = self.settings.siliconflow_ocr_model
        results.append(
            await self._execute(
                "ocr_image_data_url",
                "SiliconFlow",
                ocr_model,
                lambda: self._probe_chat_json(
                    siliconflow_client,
                    {
                        "model": ocr_model,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "image_url", "image_url": {"url": image}},
                                    {
                                        "type": "text",
                                        "text": "提取图片中的文字，只返回识别结果。",
                                    },
                                ],
                            }
                        ],
                        "max_tokens": 512,
                        "temperature": 0,
                    },
                    required_substring="3306",
                ),
            )
        )
        return results

    async def _run_zhipu(self) -> list[ProbeResult]:
        zhipu_client = self._zhipu_client
        assert zhipu_client is not None
        llm_model = self.settings.zhipu_llm_model
        vision_model = self.settings.zhipu_vision_model
        image = image_data_url()
        return [
            await self._execute(
                "llm_stream_thinking",
                "Zhipu",
                llm_model,
                lambda: self._probe_llm_stream(
                    {
                        "model": llm_model,
                        "messages": [
                            {
                                "role": "user",
                                "content": "请先思考，然后只回答M0_OK。",
                            }
                        ],
                        "thinking": {"type": "enabled"},
                        "reasoning_effort": "high",
                        "stream": True,
                        "max_tokens": 256,
                    }
                ),
            ),
            await self._execute(
                "vision_image_data_url",
                "Zhipu",
                vision_model,
                lambda: self._probe_chat_json(
                    zhipu_client,
                    {
                        "model": vision_model,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "image_url", "image_url": {"url": image}},
                                    {
                                        "type": "text",
                                        "text": "识别图片中的端口号，并简要说明图片内容。",
                                    },
                                ],
                            }
                        ],
                        "thinking": {"type": "disabled"},
                        "max_tokens": 256,
                    },
                    required_substring="3306",
                ),
            ),
        ]

    async def _probe_embedding(
        self, payload: JsonObject, expected_count: int, mode: str
    ) -> JsonObject:
        assert self._siliconflow_client is not None
        response = await self._siliconflow_client.post("embeddings", json=payload)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ContractFailure("embedding response is not an object")
        data = body.get("data")
        if not isinstance(data, list) or len(data) != expected_count:
            raise ContractFailure("unexpected embedding item count")
        dimensions: set[int] = set()
        for item in data:
            embedding = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(embedding, list) or not embedding:
                raise ContractFailure("embedding vector is missing")
            if not all(
                isinstance(value, int | float) and math.isfinite(value) for value in embedding
            ):
                raise ContractFailure("embedding vector contains an invalid number")
            dimensions.add(len(embedding))
        if dimensions != {self.settings.m0_target_embedding_dimension}:
            raise ContractFailure("embedding dimension differs from target")
        return {
            "mode": mode,
            "item_count": len(data),
            "dimension": next(iter(dimensions)),
            "request_id_present": self._request_id_present(response),
        }

    async def _probe_rerank(self, payload: JsonObject, expected_top_index: int) -> JsonObject:
        assert self._siliconflow_client is not None
        response = await self._siliconflow_client.post("rerank", json=payload)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ContractFailure("rerank response is not an object")
        results = body.get("results")
        if not isinstance(results, list) or not results:
            raise ContractFailure("rerank results are missing")
        indices: list[int] = []
        document_count = len(payload["documents"])
        for item in results:
            if not isinstance(item, dict):
                raise ContractFailure("rerank result is not an object")
            index = item.get("index")
            score = item.get("relevance_score")
            if (
                not isinstance(index, int)
                or not 0 <= index < document_count
                or not isinstance(score, int | float)
                or not math.isfinite(score)
            ):
                raise ContractFailure("rerank result fields are invalid")
            indices.append(index)
        if len(indices) != len(set(indices)):
            raise ContractFailure("rerank contains duplicate indices")
        if indices[0] != expected_top_index:
            raise ContractFailure("rerank top result differs from expected document")
        return {
            "result_count": len(results),
            "indices_valid": True,
            "expected_top_result": True,
            "request_id_present": self._request_id_present(response),
        }

    async def _probe_chat_json(
        self,
        client: httpx.AsyncClient,
        payload: JsonObject,
        required_substring: str,
    ) -> JsonObject:
        response = await client.post("chat/completions", json=payload)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ContractFailure("chat response is not an object")
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ContractFailure("chat content is missing") from exc
        if not isinstance(content, str) or not content.strip():
            raise ContractFailure("chat content is empty")
        if required_substring not in content:
            raise ContractFailure("chat content did not identify the fixture value")
        return {
            "content_present": True,
            "content_chars": len(content),
            "required_value_present": True,
            "request_id_present": self._request_id_present(response) or self._body_id_present(body),
        }

    async def _probe_llm_stream(self, payload: JsonObject) -> JsonObject:
        assert self._zhipu_client is not None
        content_chars = 0
        reasoning_field_observed = False
        terminal_observed = False
        event_id_observed = False
        async with self._zhipu_client.stream("POST", "chat/completions", json=payload) as response:
            response.raise_for_status()
            header_request_id_present = self._request_id_present(response)
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    terminal_observed = True
                    continue
                if not data:
                    continue
                event = json.loads(data)
                if not isinstance(event, dict):
                    raise ContractFailure("stream event is not an object")
                event_id_observed = event_id_observed or self._body_id_present(event)
                choices = event.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                reasoning_field_observed = reasoning_field_observed or (
                    "reasoning_content" in delta
                )
                content = delta.get("content")
                if isinstance(content, str):
                    content_chars += len(content)
        if not terminal_observed or content_chars == 0 or not reasoning_field_observed:
            raise ContractFailure("stream did not contain reasoning, content, and terminal marker")
        return {
            "content_chars": content_chars,
            "reasoning_field_observed": reasoning_field_observed,
            "terminal_observed": terminal_observed,
            "request_id_present": header_request_id_present or event_id_observed,
        }

    async def _execute(
        self,
        name: str,
        provider: str,
        model: str,
        operation: Callable[[], Awaitable[JsonObject]],
    ) -> ProbeResult:
        started = time.perf_counter()
        try:
            details = await operation()
            return ProbeResult(
                name=name,
                status="passed",
                provider=provider,
                model=model,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                details=details,
            )
        except httpx.HTTPStatusError as exc:
            return ProbeResult(
                name=name,
                status="failed",
                provider=provider,
                model=model,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                details={"http_status": exc.response.status_code},
                error_code="upstream_http_error",
            )
        except (httpx.HTTPError, ContractFailure, json.JSONDecodeError) as exc:
            return ProbeResult(
                name=name,
                status="failed",
                provider=provider,
                model=model,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                details={},
                error_code=type(exc).__name__,
            )

    @staticmethod
    def _request_id_present(response: httpx.Response) -> bool:
        return any(
            header in response.headers
            for header in ("x-request-id", "x-siliconcloud-trace-id", "x-zhipu-request-id")
        )

    @staticmethod
    def _body_id_present(body: JsonObject) -> bool:
        response_id = body.get("id")
        request_id = body.get("request_id")
        return (isinstance(response_id, str) and bool(response_id)) or (
            isinstance(request_id, str) and bool(request_id)
        )

    def _blocked_siliconflow(self) -> list[ProbeResult]:
        names_and_models = [
            (name, self.settings.siliconflow_embedding_model)
            for name in (
                "embedding_text",
                "embedding_text_batch",
                "embedding_image",
                "embedding_mixed_list",
                "embedding_fused_object",
            )
        ] + [
            ("rerank_text", self.settings.siliconflow_rerank_model),
            ("rerank_multimodal", self.settings.siliconflow_rerank_model),
            ("ocr_image_data_url", self.settings.siliconflow_ocr_model),
        ]
        return [
            ProbeResult(
                name=name,
                status="blocked",
                provider="SiliconFlow",
                model=model,
                error_code="missing_siliconflow_api_key",
            )
            for name, model in names_and_models
        ]

    def _blocked_zhipu(self) -> list[ProbeResult]:
        return [
            ProbeResult(
                name="llm_stream_thinking",
                status="blocked",
                provider="Zhipu",
                model=self.settings.zhipu_llm_model,
                error_code="missing_zhipu_api_key",
            ),
            ProbeResult(
                name="vision_image_data_url",
                status="blocked",
                provider="Zhipu",
                model=self.settings.zhipu_vision_model,
                error_code="missing_zhipu_api_key",
            ),
        ]
