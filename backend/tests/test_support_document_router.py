from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from app.common.errors import ApiError
from app.core.config import Settings
from app.rag.models import (
    ModelSnapshot,
    RerankItem,
    RerankOutcome,
    RetrievalCandidate,
)
from app.support.document_router import SupportDocumentRouter
from app.support.models import ProblemProfile


def _candidate(document_id: UUID, chunk_id: UUID, rank: int) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=chunk_id,
        document_id=document_id,
        document="PDC 固件升级指南",
        chunk_type="TEXT",
        content=f"PDC 固件升级步骤 {rank}",
        token_count=10,
        section_path=[],
        element_ids=[],
        distance=None,
        similarity=None,
        rank=rank,
    )


class _RagRepository:
    def __init__(self, candidates: list[RetrievalCandidate]) -> None:
        self.candidates = candidates

    async def bm25_search(self, **_: Any) -> list[RetrievalCandidate]:
        return self.candidates

    async def vector_search(self, **_: Any) -> list[RetrievalCandidate]:
        return self.candidates


class _SupportRepository:
    async def get_rerank_model(self) -> None:
        return None


class _ConfiguredSupportRepository:
    async def get_rerank_model(self) -> ModelSnapshot:
        return ModelSnapshot(provider="fake", model_name="rerank", parameters={})


@pytest.mark.asyncio
async def test_document_router_degraded_rerank_requires_clarification() -> None:
    document_id = uuid4()
    candidates = [_candidate(document_id, uuid4(), 1), _candidate(document_id, uuid4(), 2)]

    class DegradedReranker:
        provider = "fake"
        model_name = "rerank"

        async def rerank(self, *_: Any) -> RerankOutcome:
            from app.rag.rerank import RerankError

            raise RerankError("UPSTREAM_TIMEOUT", "Rerank 服务响应超时", degradable=True)

        async def aclose(self) -> None:
            return None

    router = SupportDocumentRouter(
        _ConfiguredSupportRepository(),  # type: ignore[arg-type]
        Settings(_env_file=None, app_env="test"),
        rag_repository=_RagRepository(candidates),  # type: ignore[arg-type]
        rerank_factory=lambda _settings, _snapshot: DegradedReranker(),  # type: ignore[arg-type]
    )

    route = await router.resolve(
        knowledge_id=uuid4(),
        index_id=uuid4(),
        question="PDC 更新中断后怎么恢复？",
    )

    assert route.action == "CLARIFY"
    assert route.document_id is None
    assert route.trace["source"] == "RAG_FALLBACK"
    assert route.trace["rerank"]["status"] == "DEGRADED"
    assert route.trace["document_routing"]["decision"] == "CLARIFY"
    assert route.trace["document_routing"]["resolved_from"] == "RERANK_DEGRADED_GUARD"


@pytest.mark.asyncio
async def test_document_router_uses_focus_aware_stay_decision() -> None:
    focus_document_id, other_document_id = uuid4(), uuid4()
    candidates = [
        _candidate(focus_document_id, uuid4(), 1),
        _candidate(focus_document_id, uuid4(), 2),
        _candidate(other_document_id, uuid4(), 3),
    ]

    class FocusReranker:
        provider = "fake"
        model_name = "rerank"

        async def rerank(self, *_: Any) -> RerankOutcome:
            return RerankOutcome(
                items=[
                    RerankItem(index=0, score=0.95),
                    RerankItem(index=1, score=0.9),
                    RerankItem(index=2, score=0.8),
                ],
                provider=self.provider,
                model_name=self.model_name,
            )

        async def aclose(self) -> None:
            return None

    router = SupportDocumentRouter(
        _ConfiguredSupportRepository(),  # type: ignore[arg-type]
        Settings(_env_file=None, app_env="test"),
        rag_repository=_RagRepository(candidates),  # type: ignore[arg-type]
        rerank_factory=lambda _settings, _snapshot: FocusReranker(),  # type: ignore[arg-type]
    )

    route = await router.resolve(
        knowledge_id=uuid4(),
        index_id=uuid4(),
        question="这个步骤完成后怎么办？",
        focus_document_id=focus_document_id,
    )

    assert route.action == "STAY"
    assert route.document_id == focus_document_id


@pytest.mark.asyncio
async def test_document_router_surfaces_permanent_rerank_error() -> None:
    document_id = uuid4()
    candidates = [_candidate(document_id, uuid4(), 1)]

    class BrokenReranker:
        provider = "fake"
        model_name = "rerank"

        async def rerank(self, *_: Any) -> RerankOutcome:
            from app.rag.rerank import RerankError

            raise RerankError("UPSTREAM_PROTOCOL_ERROR", "Rerank 返回格式异常", degradable=False)

        async def aclose(self) -> None:
            return None

    router = SupportDocumentRouter(
        _ConfiguredSupportRepository(),  # type: ignore[arg-type]
        Settings(_env_file=None, app_env="test"),
        rag_repository=_RagRepository(candidates),  # type: ignore[arg-type]
        rerank_factory=lambda _settings, _snapshot: BrokenReranker(),  # type: ignore[arg-type]
    )

    with pytest.raises(ApiError, match="Rerank 返回格式异常") as failure:
        await router.resolve(knowledge_id=uuid4(), index_id=uuid4(), question="PDC升级失败")
    assert failure.value.code == "UPSTREAM_PROTOCOL_ERROR"


class _PassingSupportRepository:
    async def get_rerank_model(self) -> ModelSnapshot:
        return ModelSnapshot(provider="fake", model_name="rerank", parameters={})


class _Reranker:
    provider = "fake"
    model_name = "rerank"

    def __init__(self) -> None:
        self.documents: list[str] = []

    async def rerank(self, _query: str, documents: list[Any], _top_n: int) -> RerankOutcome:
        self.documents = [document.text for document in documents]
        return RerankOutcome(
            items=[RerankItem(index=0, score=0.95), RerankItem(index=1, score=0.9)],
            provider=self.provider,
            model_name=self.model_name,
        )

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_document_router_reranks_title_and_profile_metadata_then_delivers() -> None:
    document_id = uuid4()
    knowledge_id = uuid4()
    candidates = [_candidate(document_id, uuid4(), 1), _candidate(document_id, uuid4(), 2)]
    profile = ProblemProfile(
        id=document_id,
        document_id=document_id,
        knowledge_id=knowledge_id,
        problem_code="PDC_USB_UPGRADE",
        canonical_question="如何使用U盘升级PDC",
        aliases=["PDC U盘升级"],
        keywords=["升级"],
        product_name="PDC",
        task_type="UPGRADE",
        symptoms=["升级失败"],
        routing_summary="PDC U盘固件升级操作",
    )
    reranker = _Reranker()
    router = SupportDocumentRouter(
        _PassingSupportRepository(),  # type: ignore[arg-type]
        Settings(_env_file=None, app_env="test"),
        rag_repository=_RagRepository(candidates),  # type: ignore[arg-type]
        rerank_factory=lambda _settings, _snapshot: reranker,  # type: ignore[arg-type]
    )

    route = await router.resolve(
        knowledge_id=knowledge_id,
        index_id=uuid4(),
        question="PDC 更新中断后怎么恢复？",
        profiles=[profile],
    )

    assert route.action == "DELIVER"
    assert route.document_id == document_id
    assert route.trace["rerank"]["status"] == "PASSED"
    assert "文档标题：PDC 固件升级指南" in reranker.documents[0]
    assert "标准问题：如何使用U盘升级PDC" in reranker.documents[0]
    assert "路由摘要：PDC U盘固件升级操作" in reranker.documents[0]
