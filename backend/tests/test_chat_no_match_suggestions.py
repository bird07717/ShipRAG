"""NO_MATCH routing turns: weak-candidate prompt hints and next_suggestions.

Covers the guidance enhancement: retrieval weak candidates (real hits below
the delivery thresholds) are injected into the ROUTING prompt as hints only,
and the done/payload exposes clickable retry suggestions. Weak candidates
must never become pending_options (auto-delivery loop stays rule-gated).
"""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.ingestion.embedding import FakeEmbeddingProvider
from app.rag.llm import FakeLlmProvider, LlmProvider
from app.rag.models import (
    ModelSnapshot,
    RerankItem,
    RerankOutcome,
    RetrievalCandidate,
    Turn,
)
from app.rag.repository import RagRepository
from app.rag.rerank import FakeRerankProvider
from app.services.rag import RagService

DOC_A = uuid4()
DOC_B = uuid4()
TITLE_A = "故障解决文档11—如何U盘升级PDC"
TITLE_B = "故障解决文档10—如何更换故障PDC"

DEFAULT_CATALOG = [
    {"document_id": str(DOC_A), "title": TITLE_A},
    {"document_id": str(DOC_B), "title": TITLE_B},
]


class _WeakRerankProvider:
    """Every chunk scores below t_low so aggregation ends in NO_MATCH."""

    provider = "weak-fake"
    model_name = "weak-rerank"

    def __init__(self, score: float = 0.3) -> None:
        self.score = score

    async def rerank(self, query: str, documents: Any, top_n: int) -> RerankOutcome:
        del query
        items = [RerankItem(index, self.score) for index in range(min(top_n, len(documents)))]
        return RerankOutcome(items, self.provider, self.model_name, {"documents": len(documents)})

    async def aclose(self) -> None:
        return None


class _RecordingLlmProvider(FakeLlmProvider):
    def __init__(self, chunks: list[str]) -> None:
        super().__init__(chunks)
        self.prompts: list[str] = []

    async def stream(self, prompt: str):
        self.prompts.append(prompt)
        async for chunk in super().stream(prompt):
            yield chunk


def _candidate(document_id: Any, rank: int, *, title: str) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=uuid4(),
        document_id=document_id,
        document=title,
        chunk_type="TEXT",
        content="操作步骤内容",
        token_count=20,
        section_path=[],
        element_ids=[uuid4()],
        distance=None,
        similarity=None,
        rank=rank,
        sequence_no=rank,
    )


class _RoutingRepository:
    """Mock repository covering the doc-routing surface of RagRepository."""

    def __init__(
        self,
        *,
        turn: Turn,
        candidates: list[RetrievalCandidate],
        catalog: list[dict[str, Any]] | None = None,
    ) -> None:
        self.turn = turn
        self.candidates = candidates
        self.catalog = catalog
        self.completed: dict[str, Any] | None = None
        self.failed: dict[str, Any] | None = None
        self.focus_updates: list[Any] = []
        self.pending_updates: list[dict[str, Any]] = []
        self.pending_clears = 0

    async def begin_turn(self, **kwargs: Any) -> Turn:
        return self.turn

    async def vector_search(self, **kwargs: Any) -> list[RetrievalCandidate]:
        return self.candidates

    async def bm25_search(self, **kwargs: Any) -> list[RetrievalCandidate]:
        return self.candidates

    async def save_prepared(self, turn: Turn, **kwargs: Any) -> None:
        return None

    async def complete_turn(self, turn: Turn, **kwargs: Any) -> None:
        self.completed = kwargs

    async def fail_turn(self, turn: Turn, **kwargs: Any) -> None:
        self.failed = kwargs

    async def get_rerank_image_assets(self, **kwargs: Any) -> dict:
        return {}

    async def get_chunks_by_ids(self, **kwargs: Any) -> list:
        return []

    async def set_conversation_focus(
        self, conversation_id: Any, document_id: Any, index_id: Any, **_: Any
    ) -> int:
        self.focus_updates.append(document_id)
        return self.turn.focus_epoch + 1

    async def clear_conversation_focus(self, conversation_id: Any) -> None:
        self.focus_updates.append(None)

    async def set_conversation_pending(
        self,
        conversation_id: Any,
        *,
        pending_options: list[dict[str, Any]],
        pending_query: str,
        **_: Any,
    ) -> None:
        self.pending_updates.append({"options": pending_options, "query": pending_query})

    async def clear_conversation_pending(self, conversation_id: Any, **_: Any) -> None:
        self.pending_clears += 1

    async def list_kb_documents(self, knowledge_id: Any) -> list[dict[str, Any]]:
        return self.catalog if self.catalog is not None else DEFAULT_CATALOG

    async def get_document_blocks(
        self, *, knowledge_id: Any, index_id: Any, document_id: Any
    ) -> dict[str, Any]:
        return {
            "document_id": str(document_id),
            "title": TITLE_A,
            "elements": [
                {
                    "element_id": uuid4(),
                    "element_type": "TEXT",
                    "content": "将U盘插入VDR主机箱内的USB端口上。",
                    "section_path": [],
                    "image_asset_id": None,
                    "image_caption": "",
                }
            ],
        }

    async def get_document_source(self, document_id: Any) -> dict[str, Any]:
        return {"display_name": TITLE_A, "filename": "a.docx"}


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


def _service(
    repo: _RoutingRepository,
    llm: LlmProvider,
    *,
    rerank: Any = None,
) -> RagService:
    settings = Settings(
        _env_file=None,
        app_env="test",
        m2_embedding_provider="fake",
        m3_llm_provider="fake",
    )
    provider = rerank if rerank is not None else FakeRerankProvider()
    return RagService(
        cast(RagRepository, repo),
        settings,
        embedding_factory=lambda _: FakeEmbeddingProvider(1024),
        llm_factory=lambda s, snap: llm,
        rerank_factory=lambda s, snap: provider,
    )


@pytest.mark.asyncio
async def test_no_match_prompt_includes_weak_candidates_and_suggestions():
    # One sub-threshold hit per document (0.3 < t_low): NO_MATCH with both
    # documents retained in doc_scores as weak candidates.
    repo = _RoutingRepository(
        turn=_turn(),
        candidates=[
            _candidate(DOC_A, 1, title=TITLE_A),
            _candidate(DOC_B, 2, title=TITLE_B),
        ],
    )
    llm = _RecordingLlmProvider(["请补充您要处理的具体设备或故障现象。"])
    service = _service(repo, llm, rerank=_WeakRerankProvider())

    result = await service.generate(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=None,
        question="升级失败了怎么办",
        request_id="req-weak",
    )

    assert repo.completed["doc_routing"]["decision"] == "NO_MATCH"
    assert "检索器弱候选" in llm.prompts[0]
    assert TITLE_A in llm.prompts[0]
    assert "相关度 0.30" in llm.prompts[0]
    # Weak candidates are hints only: no pending auto-delivery options.
    assert repo.pending_updates == []
    # doc_scores tie-break is by uuid, so compare order-insensitively.
    assert sorted(result["next_suggestions"]) == sorted(["如何U盘升级PDC？", "如何更换故障PDC？"])


@pytest.mark.asyncio
async def test_no_match_without_weak_candidates_falls_back_to_catalog():
    repo = _RoutingRepository(turn=_turn(), candidates=[])
    llm = _RecordingLlmProvider(["请换一个与产品相关的问题。"])
    service = _service(repo, llm)

    result = await service.generate(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=None,
        question="随便聊聊",
        request_id="req-no-weak",
    )

    assert repo.completed["doc_routing"]["decision"] == "NO_MATCH"
    assert "检索器弱候选" not in llm.prompts[0]
    assert "文档目录" in llm.prompts[0]
    assert sorted(result["next_suggestions"]) == sorted(["如何U盘升级PDC？", "如何更换故障PDC？"])


@pytest.mark.asyncio
async def test_clarify_turn_has_no_next_suggestions():
    # Single candidate at 1.0 passes t_low but cannot lock: CLARIFY keeps
    # its existing pending-option loop and carries no retry suggestions.
    repo = _RoutingRepository(
        turn=_turn(),
        candidates=[_candidate(DOC_A, 1, title=TITLE_A)],
    )
    llm = _RecordingLlmProvider(["您是想了解《故障解决文档11—如何U盘升级PDC》的内容吗？"])
    service = _service(repo, llm)

    result = await service.generate(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=None,
        question="你可以帮我做什么",
        request_id="req-clarify",
    )

    assert "候选文档" in llm.prompts[0]
    assert "检索器弱候选" not in llm.prompts[0]
    assert len(repo.pending_updates) == 1
    assert result["next_suggestions"] == []


@pytest.mark.asyncio
async def test_delivery_has_no_next_suggestions():
    # Two top chunks from the same document: hits=2, score=1.5 >= t_high ->
    # lock and DELIVER without LLM; no retry suggestions on a delivered turn.
    repo = _RoutingRepository(
        turn=_turn(),
        candidates=[
            _candidate(DOC_A, 1, title=TITLE_A),
            _candidate(DOC_A, 2, title=TITLE_A),
        ],
    )
    llm = _RecordingLlmProvider(["不应被调用"])
    service = _service(repo, llm)

    result = await service.generate(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=None,
        question="如何U盘升级PDC",
        request_id="req-deliver",
    )

    assert result["response_type"] == "DOC_DELIVERED"
    assert llm.prompts == []
    assert result["next_suggestions"] == []


@pytest.mark.asyncio
async def test_no_match_empty_catalog_keeps_fixed_answer_without_suggestions():
    repo = _RoutingRepository(turn=_turn(), candidates=[], catalog=[])
    llm = _RecordingLlmProvider(["不应被调用"])
    service = _service(repo, llm)

    result = await service.generate(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=None,
        question="今天天气如何",
        request_id="req-empty-catalog",
    )

    assert "当前知识库没有足够的可靠信息" in result["answer"]
    assert llm.prompts == []
    assert result["next_suggestions"] == []
