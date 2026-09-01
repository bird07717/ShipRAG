from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import httpx
import pytest


def _runner() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "rag-acceptance" / "run_golden.py"
    spec = importlib.util.spec_from_file_location("rag_golden_runner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_assistant_mode_sends_public_contract_headers_and_reads_complete() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=(
                'event: accepted\ndata: {"request_id":"golden-request"}\n\n'
                ": ping\n\n"
                'event: delta\ndata: {"text":"回答"}\n\n'
                'event: complete\ndata: {"conversation_id":"conversation-1",'
                '"message_id":"message-1","answer":"回答","references":[]}\n\n'
            ).encode(),
        )

    runner = _runner()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer assistant-token"},
    ) as client:
        payload, _latency, error = await runner.run_turn(
            client,
            base_url="http://assistant.test",
            kb_id="00000000-0000-0000-0000-000000000001",
            question="测试问题",
            conversation_id=None,
            request_id="golden-request",
            api_mode="assistant",
            external_user_id="golden-user",
        )

    assert error is None
    assert payload is not None and payload["answer"] == "回答"
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["authorization"] == "Bearer assistant-token"
    assert headers["x-external-user-id"] == "golden-user"
    assert headers["x-request-id"] == "golden-request"
    assert headers["idempotency-key"] == "golden-request"
    assert '"request_id"' not in str(captured["body"])


def test_assistant_public_references_are_evaluated_without_admin_catalog() -> None:
    runner = _runner()
    hits = runner.extract_hits(
        {
            "references": [{"title": "如何使用U盘升级PDC"}],
            "answer": "请参考《如何使用U盘升级PDC》。",
        },
        {},
    )
    passed, problems = runner.evaluate(
        {
            "document_keys": ["U盘升级PDC"],
            "forbidden_keys": ["升级3359核心板"],
            "tolerance": "EXACT",
        },
        hits,
    )

    assert passed is True
    assert problems == []
