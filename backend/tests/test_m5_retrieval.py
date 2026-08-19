from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.ingestion.embedding import FakeEmbeddingProvider
from app.rag.models import (
    ModelSnapshot,
    RerankDocument,
    RetrievalCandidate,
    Source,
    Turn,
)
from app.rag.repository import RagRepository
from app.rag.rerank import RerankError, RerankProvider, SiliconFlowRerankProvider
from app.rag.retrieval import apply_rerank, reciprocal_rank_fusion
from app.services.rag import RagService


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


def _turn() -> Turn:
    return Turn(
        trace_id=uuid4(),
        conversation_id=uuid4(),
        user_message_id=uuid4(),
        assistant_message_id=uuid4(),
        knowledge_id=uuid4(),
        index_id=uuid4(),
        embedding_model_name="embedding-model",
        prompt_template="历史：{{history}}\n资料：{{context}}\n问题：{{question}}",
        llm=ModelSnapshot("zhipu", "glm-test", {}),
        history=[],
        rerank=ModelSnapshot("siliconflow", "rerank-test", {}),
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


class _HybridRepository:
    def __init__(self) -> None:
        self.turn = _turn()
        self.candidate = _candidate("hybrid", 1)
        self.saved: dict[str, Any] | None = None
        self.failed: dict[str, Any] | None = None

    async def begin_turn(self, **kwargs: Any) -> Turn:
        del kwargs
        return self.turn

    async def bm25_search(self, **kwargs: Any) -> list[RetrievalCandidate]:
        del kwargs
        return [
            replace(
                self.candidate,
                distance=None,
                similarity=None,
                bm25_score=2.5,
                bm25_rank=1,
            )
        ]

    async def vector_search(self, **kwargs: Any) -> list[RetrievalCandidate]:
        assert len(kwargs["embedding"]) == 1024
        return [self.candidate]

    async def save_prepared(self, turn: Turn, **kwargs: Any) -> None:
        assert turn == self.turn
        self.saved = kwargs

    async def fail_turn(self, turn: Turn, **kwargs: Any) -> None:
        assert turn == self.turn
        self.failed = kwargs


class _DegradedRerankProvider:
    provider = "siliconflow"
    model_name = "rerank-test"

    async def rerank(self, query: str, documents: Any, top_n: int) -> Any:
        del query, documents, top_n
        raise RerankError("UPSTREAM_TIMEOUT", "Rerank 服务响应超时", degradable=True)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_rerank_timeout_degrades_to_rrf_and_is_traced() -> None:
    repository = _HybridRepository()
    provider = FakeEmbeddingProvider(1024)
    service = RagService(
        cast(RagRepository, repository),
        Settings(_env_file=None, app_env="test"),
        embedding_factory=lambda _: provider,
        rerank_factory=lambda settings, snapshot: cast(RerankProvider, _DegradedRerankProvider()),
    )

    prepared = await service.prepare(
        knowledge_id=repository.turn.knowledge_id,
        conversation_id=None,
        question="数据库端口是多少？",
        request_id="m5-degraded",
    )

    assert prepared.selected_candidates[0].chunk_id == repository.candidate.chunk_id
    assert repository.saved is not None
    assert repository.saved["rerank_result"] | {"context_expansion": None} == {
        "status": "DEGRADED",
        "degraded": True,
        "provider": "siliconflow",
        "model": "rerank-test",
        "error_code": "UPSTREAM_TIMEOUT",
        "context_expansion": None,
    }
    assert repository.saved["rerank_result"]["context_expansion"]["status"] == "SKIPPED"
    assert repository.failed is None


def test_citation_result_records_missing_and_invalid_ids() -> None:
    source = Source("S1", uuid4(), "手册.docx", [], None, [uuid4()], uuid4())

    valid, result = RagService._validate_citations("未知[S99]，重复[S1][S1]", [source])
    missing, missing_result = RagService._validate_citations("没有引用", [source])

    assert valid == [source.public_dict()]
    assert result["valid_source_ids"] == ["S1"]
    assert result["invalid_source_ids"] == ["S99"]
    assert result["citation_missing"] is False
    assert missing == []
    assert missing_result["citation_missing"] is True
