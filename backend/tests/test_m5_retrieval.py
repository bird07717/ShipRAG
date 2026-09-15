from __future__ import annotations

import json
from dataclasses import replace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.rag.models import (
    ModelSnapshot,
    RerankDocument,
    RetrievalCandidate,
)
from app.rag.rerank import RerankError, SiliconFlowRerankProvider
from app.rag.retrieval import apply_rerank, reciprocal_rank_fusion


def _candidate(name: str, rank: int) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=uuid4(),
        document_id=uuid4(),
        document=f"{name}.docx",
        chunk_type="TEXT",
        content=f"{name} 数据库默认端口",
        token_count=20,
        section_path=["配置"],
        element_ids=[uuid4()],
        distance=0.1 * rank,
        similarity=1 - 0.1 * rank,
        rank=rank,
    )


def test_rrf_unions_deduplicates_and_uses_ranks_only() -> None:
    first = _candidate("vector-only", 1)
    shared = _candidate("shared", 2)
    lexical_only = _candidate("bm25-only", 2)
    bm25 = [
        replace(shared, rank=1, distance=None, similarity=None, bm25_score=1000),
        replace(lexical_only, distance=None, similarity=None, bm25_score=0.01),
    ]

    fused = reciprocal_rank_fusion([first, shared], bm25, rrf_k=60, limit=10)

    assert [item.chunk_id for item in fused] == [
        shared.chunk_id,
        first.chunk_id,
        lexical_only.chunk_id,
    ]
    assert fused[0].vector_rank == 2
    assert fused[0].bm25_rank == 1
    assert fused[0].rrf_score == pytest.approx(1 / 62 + 1 / 61)
    assert fused[2].bm25_score == 0.01


def test_apply_rerank_uses_provider_indices_and_scores() -> None:
    from app.rag.models import RerankItem

    candidates = [_candidate("first", 1), _candidate("second", 2)]
    reranked = apply_rerank(candidates, [RerankItem(1, 0.9), RerankItem(0, 0.2)])

    assert [item.document for item in reranked] == ["second.docx", "first.docx"]
    assert [item.rerank_score for item in reranked] == [0.9, 0.2]
    assert [item.rank for item in reranked] == [1, 2]


@pytest.mark.asyncio
async def test_siliconflow_vl_rerank_contract_supports_text_and_image() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 1, "relevance_score": 0.95},
                    {"index": 0, "relevance_score": 0.2},
                ],
                "meta": {"tokens": 10},
            },
        )

    settings = Settings(
        _env_file=None,
        app_env="test",
        siliconflow_api_key=SecretStr("not-a-real-key"),
    )
    async with httpx.AsyncClient(
        base_url=settings.siliconflow_base_url, transport=httpx.MockTransport(handler)
    ) as client:
        provider = SiliconFlowRerankProvider(
            settings, ModelSnapshot("siliconflow", "rerank-model", {}), client
        )
        outcome = await provider.rerank(
            "数据库端口截图",
            [
                RerankDocument("普通文本"),
                RerankDocument("端口截图", b"png", "image/png"),
            ],
            2,
        )

    assert [item.index for item in outcome.items] == [1, 0]
    assert outcome.usage == {"tokens": 10}
    assert requests[0]["return_documents"] is False
    assert requests[0]["documents"][1]["text"] == "端口截图"
    assert requests[0]["documents"][1]["image"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_rerank_truncates_valid_extra_results_to_requested_top_n() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 2, "relevance_score": 0.9},
                    {"index": 1, "relevance_score": 0.8},
                    {"index": 0, "relevance_score": 0.7},
                ]
            },
        )

    settings = Settings(
        _env_file=None,
        app_env="test",
        siliconflow_api_key=SecretStr("not-a-real-key"),
    )
    async with httpx.AsyncClient(
        base_url=settings.siliconflow_base_url, transport=httpx.MockTransport(handler)
    ) as client:
        provider = SiliconFlowRerankProvider(
            settings, ModelSnapshot("siliconflow", "rerank-model", {}), client
        )
        outcome = await provider.rerank(
            "query",
            [RerankDocument("one"), RerankDocument("two"), RerankDocument("three")],
            2,
        )

    assert [item.index for item in outcome.items] == [2, 1]


@pytest.mark.asyncio
async def test_rerank_rejects_duplicate_indices_as_protocol_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.8},
                ]
            },
        )

    settings = Settings(
        _env_file=None,
        app_env="test",
        siliconflow_api_key=SecretStr("not-a-real-key"),
    )
    async with httpx.AsyncClient(
        base_url=settings.siliconflow_base_url, transport=httpx.MockTransport(handler)
    ) as client:
        provider = SiliconFlowRerankProvider(
            settings, ModelSnapshot("siliconflow", "rerank-model", {}), client
        )
        with pytest.raises(RerankError) as captured:
            await provider.rerank("query", [RerankDocument("one")], 2)

    assert captured.value.code == "UPSTREAM_PROTOCOL_ERROR"
    assert captured.value.degradable is False


def test_bm25_expansion_terms_from_synonym_groups() -> None:
    from app.rag.synonyms import bm25_expansion_terms

    # Only the used group contributes; the used member itself is excluded.
    assert bm25_expansion_terms("如何检查VDR") == ["排查", "检测", "自检"]
    assert bm25_expansion_terms("主板坏了怎么更换") == ["替换", "核心板"]
    # Case-insensitive for latin members.
    assert bm25_expansion_terms("MIC有杂音") == ["麦克风"]
    assert bm25_expansion_terms("怎么update") == ["升级"]
    # No group member present -> no expansion.
    assert bm25_expansion_terms("船名在哪里修改") == []


def test_merge_expanded_candidates_reassigns_sequential_ranks() -> None:
    from app.rag.retrieval import merge_expanded_candidates

    def candidate(chunk_id, rank):
        return RetrievalCandidate(
            chunk_id=chunk_id,
            document_id=uuid4(),
            document="doc",
            chunk_type="TEXT",
            content="content",
            token_count=1,
            section_path=[],
            element_ids=[],
            distance=None,
            similarity=None,
            rank=rank,
        )

    a, b, c, d = uuid4(), uuid4(), uuid4(), uuid4()
    base = [candidate(a, 1), candidate(b, 2)]
    extras = [candidate(c, 1), candidate(a, 1), candidate(d, 3)]
    merged = merge_expanded_candidates(base, extras)
    # Originals keep their order and strictly better ranks; duplicates are
    # dropped; appended chunks get fresh sequential ranks so RRF stays sound.
    assert [item.chunk_id for item in merged] == [a, b, c, d]
    assert [item.rank for item in merged] == [1, 2, 3, 4]
