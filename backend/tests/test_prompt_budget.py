"""Tests for prompt token budgeting with context-trimming degradation."""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest

from app.common.errors import ApiError
from app.core.config import Settings
from app.ingestion.embedding import FakeEmbeddingProvider
from app.rag.llm import FakeLlmProvider, LlmProvider
from app.rag.models import ModelSnapshot, RetrievalCandidate, Turn
from app.rag.repository import RagRepository
from app.rag.rerank import FakeRerankProvider
from app.services.rag import RagService


class _TrimRepository:
    def __init__(self, turn: Turn, candidates: list[RetrievalCandidate]) -> None:
        self.turn = turn
        self.candidates = candidates
        self.saved: dict[str, Any] | None = None
        self.failed: dict[str, Any] | None = None

    async def begin_turn(self, **kwargs: Any) -> Turn:
        return self.turn

    async def vector_search(self, **kwargs: Any) -> list[RetrievalCandidate]:
        return self.candidates

    async def bm25_search(self, **kwargs: Any) -> list[RetrievalCandidate]:
        return []

    async def save_prepared(self, turn: Turn, **kwargs: Any) -> None:
        self.saved = kwargs

    async def fail_turn(self, turn: Turn, **kwargs: Any) -> None:
        self.failed = kwargs

    async def get_rerank_image_assets(self, **kwargs: Any) -> dict:
        return {}

    async def get_chunks_by_ids(self, **kwargs: Any) -> list:
        return []


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
        focus_document_id=None,
        chat_context={},
    )


def _candidate(content: str, *, rank: int) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=uuid4(),
        document_id=uuid4(),
        document="故障解决文档",
        chunk_type="TEXT",
        content=content,
        token_count=max(1, len(content)),
        section_path=[],
        element_ids=[uuid4()],
        distance=None,
        similarity=None,
        rank=rank,
        sequence_no=rank,
    )


def _service(
    repo: _TrimRepository, *, token_budget: int, prompt_max_chars: int = 60_000
) -> RagService:
    settings = Settings(
        _env_file=None,
        app_env="test",
        m2_embedding_provider="fake",
        m3_llm_provider="fake",
        m3_prompt_token_budget=token_budget,
        m3_prompt_max_chars=prompt_max_chars,
    )
    return RagService(
        cast(RagRepository, repo),
        settings,
        embedding_factory=lambda _: FakeEmbeddingProvider(1024),
        llm_factory=lambda s, snap: cast(LlmProvider, FakeLlmProvider()),
        rerank_factory=lambda s, snap: FakeRerankProvider(),
    )


@pytest.mark.asyncio
async def test_prompt_within_budget_keeps_all_chunks() -> None:
    candidates = [_candidate("第一块内容" * 10, rank=1), _candidate("第二块内容" * 10, rank=2)]
    repo = _TrimRepository(_turn(), candidates)
    service = _service(repo, token_budget=1_000)

    prepared = await service.prepare(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=None,
        question="如何升级",
        request_id="req-fit",
    )

    assert len(prepared.selected_candidates) == 2
    assert "prompt_trimmed_chunks" not in prepared.timings
    assert repo.failed is None


@pytest.mark.asyncio
async def test_oversized_context_trims_tail_chunks_instead_of_failing() -> None:
    # each chunk is ~425 tokens (400 content + block metadata); three chunks
    # exceed the 1_100 budget, two fit after trimming the lowest-ranked one
    candidates = [
        _candidate("甲" * 400, rank=1),
        _candidate("乙" * 400, rank=2),
        _candidate("丙" * 400, rank=3),
    ]
    repo = _TrimRepository(_turn(), candidates)
    service = _service(repo, token_budget=1_100)

    prepared = await service.prepare(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=None,
        question="如何升级",
        request_id="req-trim",
    )

    # the turn succeeds with the two best-ranked chunks retained
    assert repo.failed is None
    assert len(prepared.selected_candidates) == 2
    assert "甲" * 400 in prepared.prompt
    assert "乙" * 400 in prepared.prompt
    assert "丙" * 400 not in prepared.prompt
    assert prepared.timings["prompt_trimmed_chunks"] == 1
    # saved sources match the retained context
    assert repo.saved is not None
    assert len(repo.saved["selected_context"]) == 2
    assert repo.saved["prompt"] == prepared.prompt


@pytest.mark.asyncio
async def test_empty_context_still_too_large_raises() -> None:
    # a template so large that even with zero chunks it exceeds the budget
    template = "填充" * 800 + "\n历史：{{history}}\n资料：{{context}}\n问题：{{question}}"
    turn = _turn()
    turn = Turn(
        trace_id=turn.trace_id,
        conversation_id=turn.conversation_id,
        user_message_id=turn.user_message_id,
        assistant_message_id=turn.assistant_message_id,
        knowledge_id=turn.knowledge_id,
        index_id=turn.index_id,
        embedding_model_name=turn.embedding_model_name,
        prompt_template=template,
        llm=turn.llm,
        history=[],
        rerank=turn.rerank,
        focus_document_id=None,
        chat_context={},
    )
    repo = _TrimRepository(turn, [_candidate("甲" * 50, rank=1)])
    service = _service(repo, token_budget=1_000)

    with pytest.raises(ApiError) as exc_info:
        await service.prepare(
            knowledge_id=repo.turn.knowledge_id,
            conversation_id=None,
            question="如何升级",
            request_id="req-overflow",
        )

    assert exc_info.value.code == "PROMPT_TOO_LARGE"
    assert repo.failed is not None
    assert repo.failed["status"] == "FAILED"
