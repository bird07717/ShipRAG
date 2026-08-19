from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.m0.models import ProbeReport, ProbeResult
from app.m0.online import OnlineContractProbe


def _handler(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    headers = {"x-request-id": "sanitized-request-id"}
    if request.url.path.endswith("/embeddings"):
        input_value = payload["input"]
        if isinstance(input_value, list):
            count = len(input_value)
        else:
            count = 1
        dimension = payload["dimensions"]
        return httpx.Response(
            200,
            headers=headers,
            json={
                "data": [{"embedding": [0.1] * dimension, "index": index} for index in range(count)]
            },
        )
    if request.url.path.endswith("/rerank"):
        return httpx.Response(
            200,
            headers=headers,
            json={
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 1, "relevance_score": 0.1},
                ]
            },
        )
    if payload["model"] == "glm-5.2":
        body = (
            'data: {"choices":[{"delta":{"reasoning_content":"ok"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"M0_OK"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, headers=headers, text=body)
    return httpx.Response(
        200,
        headers=headers,
        json={"choices": [{"message": {"content": "DATABASE PORT: 3306"}}]},
    )


def _invalid_contract_handler(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    if request.url.path.endswith("/embeddings"):
        input_value = payload["input"]
        count = len(input_value) if isinstance(input_value, list) else 1
        return httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": [0.1] * (payload["dimensions"] - 1), "index": index}
                    for index in range(count)
                ]
            },
        )
    if request.url.path.endswith("/rerank"):
        return httpx.Response(
            200,
            json={"results": [{"index": 1, "relevance_score": 0.9}]},
        )
    if payload["model"] == "glm-5.2":
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"M0_OK"}}]}\n\ndata: [DONE]\n\n',
        )
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": "unrelated response"}}]},
    )


@pytest.mark.asyncio
async def test_online_contract_suite_with_mocked_upstreams() -> None:
    transport = httpx.MockTransport(_handler)
    settings = Settings(
        _env_file=None,
        app_env="test",
        siliconflow_api_key=SecretStr("not-a-real-key"),
        zhipu_api_key=SecretStr("not-a-real-key"),
        m0_target_embedding_dimension=64,
    )
    async with (
        httpx.AsyncClient(base_url=settings.siliconflow_base_url, transport=transport) as sf,
        httpx.AsyncClient(base_url=settings.zhipu_base_url, transport=transport) as zhipu,
    ):
        report = await OnlineContractProbe(settings, sf, zhipu).run()

    assert report.status == "passed"
    assert len(report.results) == 10
    assert all(result.status == "passed" for result in report.results)
    stream = next(result for result in report.results if result.name == "llm_stream_thinking")
    assert stream.details["reasoning_field_observed"] is True


@pytest.mark.asyncio
async def test_online_contract_suite_is_blocked_without_credentials() -> None:
    report = await OnlineContractProbe(Settings(_env_file=None, app_env="test")).run()

    assert report.status == "blocked"
    assert len(report.results) == 10
    assert all(result.status == "blocked" for result in report.results)


@pytest.mark.asyncio
async def test_online_contract_suite_rejects_semantically_invalid_responses() -> None:
    transport = httpx.MockTransport(_invalid_contract_handler)
    settings = Settings(
        _env_file=None,
        app_env="test",
        siliconflow_api_key=SecretStr("not-a-real-key"),
        zhipu_api_key=SecretStr("not-a-real-key"),
        m0_target_embedding_dimension=64,
    )
    async with (
        httpx.AsyncClient(base_url=settings.siliconflow_base_url, transport=transport) as sf,
        httpx.AsyncClient(base_url=settings.zhipu_base_url, transport=transport) as zhipu,
    ):
        report = await OnlineContractProbe(settings, sf, zhipu).run()

    assert report.status == "failed"
    assert len(report.results) == 10
    assert all(result.status == "failed" for result in report.results)
    assert all(result.details == {} for result in report.results)


def test_probe_report_prioritizes_failure_over_blocked() -> None:
    report = ProbeReport(
        results=[
            ProbeResult("a", "blocked", "p", "m"),
            ProbeResult("b", "failed", "p", "m"),
        ]
    )

    assert report.status == "failed"
    assert report.to_dict()["summary"] == {"passed": 0, "failed": 1, "blocked": 1}
