"""Tests for multi-turn retrieval query rewriting."""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.ingestion.embedding import FakeEmbeddingProvider
from app.rag.llm import FakeLlmProvider, LlmProvider
from app.rag.models import ModelSnapshot, RetrievalCandidate, Turn
from app.rag.query_rewrite import rewrite_query, should_rewrite
from app.rag.repository import RagRepository
from app.rag.rerank import FakeRerankProvider
from app.services.rag import RagService

_HISTORY = [
    {"role": "USER", "content": "VDR主机如何做U盘升级"},
    {"role": "ASSISTANT", "content": "将U盘插入VDR主机箱内的USB端口上，执行升级程序。"},
]


# --- gate -------------------------------------------------------------------


def test_should_rewrite_false_without_history() -> None:
    assert should_rewrite("那它的密码呢", [], max_chars=40) is False


def test_should_rewrite_true_for_short_follow_up_with_history() -> None:
    assert should_rewrite("那它的密码呢", _HISTORY, max_chars=40) is True


def test_should_rewrite_false_for_long_standalone_question() -> None:
    question = (
        "VDR主机升级过程中断电导致系统无法启动，应该按照什么样的完整流程恢复固件并保留配置？"
        * 2
    )
    assert len(question) > 40
    assert should_rewrite(question, _HISTORY, max_chars=40) is False


def test_should_rewrite_true_for_long_question_with_follow_up_marker() -> None:
    question = (
        "升级完成后如果它提示证书过期导致无法连接平台，需要重新申请证书还是可以直接续期？"
        * 2
    )
    assert len(question) > 40
    assert should_rewrite(question, _HISTORY, max_chars=40) is True


# --- rewrite_query ----------------------------------------------------------


def _rewrite_provider(completions: list[str] | None = None) -> FakeLlmProvider:
    return FakeLlmProvider(completions=completions)


@pytest.mark.asyncio
async def test_rewrite_query_returns_rewritten_query() -> None:
    provider = _rewrite_provider(["VDR主机U盘升级的密码是什么"])
    query, record = await rewrite_query(
        provider,
        question="那它的密码呢",
        history=_HISTORY,
        max_tokens=192,
        timeout_seconds=8.0,
        max_chars=4_000,
    )
    assert query == "VDR主机U盘升级的密码是什么"
    assert record == {
        "status": "REWRITTEN",
        "original": "那它的密码呢",
        "rewritten": "VDR主机U盘升级的密码是什么",
    }


@pytest.mark.asyncio
async def test_rewrite_query_strips_surrounding_quotes() -> None:
    provider = _rewrite_provider(['"VDR主机U盘升级的密码"'])
    query, record = await rewrite_query(
        provider,
        question="那它的密码呢",
        history=_HISTORY,
        max_tokens=192,
        timeout_seconds=8.0,
        max_chars=4_000,
    )
    assert query == "VDR主机U盘升级的密码"
    assert record["status"] == "REWRITTEN"


@pytest.mark.asyncio
async def test_rewrite_query_degrades_to_original_on_llm_error() -> None:
    provider = _rewrite_provider([])  # no completions configured -> LlmError
    query, record = await rewrite_query(
        provider,
        question="那它的密码呢",
        history=_HISTORY,
        max_tokens=192,
        timeout_seconds=8.0,
        max_chars=4_000,
    )
    assert query == "那它的密码呢"
    assert record == {"status": "DEGRADED", "reason": "UPSTREAM_UNAVAILABLE"}


@pytest.mark.asyncio
async def test_rewrite_query_unchanged_when_identical() -> None:
    provider = _rewrite_provider(["那它的密码呢"])
    query, record = await rewrite_query(
        provider,
        question="那它的密码呢",
        history=_HISTORY,
        max_tokens=192,
        timeout_seconds=8.0,
        max_chars=4_000,
    )
    assert query == "那它的密码呢"
    assert record == {"status": "UNCHANGED", "reason": "IDENTICAL"}


@pytest.mark.asyncio
async def test_rewrite_query_unchanged_when_output_exceeds_limit() -> None:
    provider = _rewrite_provider(["很" * 100])
    query, record = await rewrite_query(
        provider,
        question="那它的密码呢",
        history=_HISTORY,
        max_tokens=192,
        timeout_seconds=8.0,
        max_chars=50,
    )
    assert query == "那它的密码呢"
    assert record == {"status": "UNCHANGED", "reason": "INVALID_OUTPUT"}


# --- prepare() integration ----------------------------------------------------


class _RewriteRepository:
    def __init__(self, turn: Turn, candidates: list[RetrievalCandidate]) -> None:
        self.turn = turn
        self.candidates = candidates
        self.bm25_queries: list[str] = []
        self.saved: dict[str, Any] | None = None

    async def begin_turn(self, **kwargs: Any) -> Turn:
        return self.turn

    async def vector_search(self, **kwargs: Any) -> list[RetrievalCandidate]:
        return self.candidates

    async def bm25_search(self, **kwargs: Any) -> list[RetrievalCandidate]:
        self.bm25_queries.append(str(kwargs["query"]))
        return self.candidates

    async def save_prepared(self, turn: Turn, **kwargs: Any) -> None:
        self.saved = kwargs

    async def get_rerank_image_assets(self, **kwargs: Any) -> dict:
        return {}

    async def get_chunks_by_ids(self, **kwargs: Any) -> list:
        return []


def _turn(history: list[dict[str, str]]) -> Turn:
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
        history=history,
        rerank=ModelSnapshot("siliconflow", "rerank-test", {}),
        focus_document_id=None,
        chat_context={},
    )


def _candidate() -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=uuid4(),
        document_id=uuid4(),
        document="故障解决文档",
        chunk_type="TEXT",
        content="操作步骤内容",
        token_count=20,
        section_path=[],
        element_ids=[uuid4()],
        distance=None,
        similarity=None,
        rank=1,
        sequence_no=1,
    )


def _service(repo: _RewriteRepository, llm: LlmProvider) -> RagService:
    settings = Settings(
        _env_file=None,
        app_env="test",
        m2_embedding_provider="fake",
        m3_llm_provider="fake",
    )
    return RagService(
        cast(RagRepository, repo),
        settings,
        embedding_factory=lambda _: FakeEmbeddingProvider(1024),
        llm_factory=lambda s, snap: llm,
        rerank_factory=lambda s, snap: FakeRerankProvider(),
    )


@pytest.mark.asyncio
async def test_prepare_uses_rewritten_query_for_retrieval_only() -> None:
    repo = _RewriteRepository(_turn(_HISTORY), [_candidate()])
    llm = FakeLlmProvider(completions=["VDR主机U盘升级的密码是什么"])
    service = _service(repo, llm)

    prepared = await service.prepare(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=repo.turn.conversation_id,
        question="那它的密码呢",
        request_id="req-rewrite",
        mode="CHAT",
    )

    # BM25 searched with the rewritten query
    assert repo.bm25_queries == ["VDR主机U盘升级的密码是什么"]
    # the answer prompt keeps the ORIGINAL question, not the rewrite
    assert "问题：那它的密码呢" in prepared.prompt
    assert "VDR主机U盘升级的密码是什么" not in prepared.prompt
    # rewrite outcome is persisted for observability
    assert repo.saved is not None
    assert repo.saved["rerank_result"]["query_rewrite"] == {
        "status": "REWRITTEN",
        "original": "那它的密码呢",
        "rewritten": "VDR主机U盘升级的密码是什么",
    }
    assert prepared.timings["query_rewrite_ms"] >= 0


@pytest.mark.asyncio
async def test_prepare_falls_back_to_original_query_on_rewrite_failure() -> None:
    repo = _RewriteRepository(_turn(_HISTORY), [_candidate()])
    llm = FakeLlmProvider()  # no completions -> complete() raises LlmError
    service = _service(repo, llm)

    prepared = await service.prepare(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=repo.turn.conversation_id,
        question="那它的密码呢",
        request_id="req-rewrite-degraded",
        mode="CHAT",
    )

    assert repo.bm25_queries == ["那它的密码呢"]
    assert repo.saved is not None
    assert repo.saved["rerank_result"]["query_rewrite"] == {
        "status": "DEGRADED",
        "reason": "UPSTREAM_UNAVAILABLE",
    }
    assert "问题：那它的密码呢" in prepared.prompt


@pytest.mark.asyncio
async def test_prepare_skips_rewrite_without_history() -> None:
    repo = _RewriteRepository(_turn([]), [_candidate()])
    llm = FakeLlmProvider(completions=["不应被使用"])
    service = _service(repo, llm)

    await service.prepare(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=repo.turn.conversation_id,
        question="VDR主机如何升级",
        request_id="req-rewrite-skip",
        mode="CHAT",
    )

    assert repo.bm25_queries == ["VDR主机如何升级"]
    assert "query_rewrite" not in repo.saved["rerank_result"]
    assert llm.completions == ["不应被使用"]  # untouched


@pytest.mark.asyncio
async def test_prepare_skips_rewrite_for_ask_mode() -> None:
    repo = _RewriteRepository(_turn(_HISTORY), [_candidate()])
    llm = FakeLlmProvider(completions=["不应被使用"])
    service = _service(repo, llm)

    await service.prepare(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=repo.turn.conversation_id,
        question="那它的密码呢",
        request_id="req-rewrite-ask",
        mode="ASK",
    )

    assert repo.bm25_queries == ["那它的密码呢"]
    assert "query_rewrite" not in repo.saved["rerank_result"]


@pytest.mark.asyncio
async def test_prepare_skips_rewrite_when_disabled() -> None:
    repo = _RewriteRepository(_turn(_HISTORY), [_candidate()])
    llm = FakeLlmProvider(completions=["不应被使用"])
    settings = Settings(
        _env_file=None,
        app_env="test",
        m2_embedding_provider="fake",
        m3_llm_provider="fake",
        m3_query_rewrite_enabled=False,
    )
    service = RagService(
        cast(RagRepository, repo),
        settings,
        embedding_factory=lambda _: FakeEmbeddingProvider(1024),
        llm_factory=lambda s, snap: llm,
        rerank_factory=lambda s, snap: FakeRerankProvider(),
    )

    await service.prepare(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=repo.turn.conversation_id,
        question="那它的密码呢",
        request_id="req-rewrite-disabled",
        mode="CHAT",
    )

    assert repo.bm25_queries == ["那它的密码呢"]
    assert "query_rewrite" not in repo.saved["rerank_result"]
