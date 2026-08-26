from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.config import Settings
from app.rag.models import RetrievalCandidate
from app.support.document_router import SupportDocumentRouter


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


@pytest.mark.asyncio
async def test_document_router_delivers_after_aggregating_multiple_chunks() -> None:
    document_id = uuid4()
    candidates = [_candidate(document_id, uuid4(), 1), _candidate(document_id, uuid4(), 2)]
    router = SupportDocumentRouter(
        _SupportRepository(),  # type: ignore[arg-type]
        Settings(_env_file=None, app_env="test"),
        rag_repository=_RagRepository(candidates),  # type: ignore[arg-type]
    )

    route = await router.resolve(
        knowledge_id=uuid4(),
        index_id=uuid4(),
        question="PDC 更新中断后怎么恢复？",
    )

    assert route.action == "DELIVER"
    assert route.document_id == document_id
    assert route.trace["source"] == "RAG_FALLBACK"
    assert route.trace["rerank"]["status"] == "DEGRADED"
    assert route.trace["document_routing"]["decision"] == "DELIVER"
