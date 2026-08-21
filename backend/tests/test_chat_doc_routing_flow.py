"""Integration tests for the chat document-routing state machine."""

from __future__ import annotations

import json
from typing import Any, ClassVar, cast
from uuid import uuid4

import pytest

from app.common.errors import ApiError
from app.core.config import Settings
from app.ingestion.embedding import FakeEmbeddingProvider
from app.rag.llm import FakeLlmProvider, LlmError, LlmProvider
from app.rag.models import ModelSnapshot, RetrievalCandidate, Turn
from app.rag.repository import RagRepository
from app.rag.rerank import FakeRerankProvider
from app.services.rag import RagService

DOC_A = uuid4()
DOC_B = uuid4()
TITLE_A = "故障解决文档11—如何U盘升级PDC"
TITLE_B = "故障解决文档10—如何更换故障PDC"

DOC_A_ELEMENTS = {
    "document_id": str(DOC_A),
    "title": TITLE_A,
    "elements": [
        {
            "element_id": uuid4(),
            "element_type": "TEXT",
            "content": "更换单个存储体之后，需要对更换的存储体进行update升级。",
            "section_path": [],
            "image_asset_id": None,
            "image_caption": "",
        },
        {
            "element_id": uuid4(),
            "element_type": "TEXT",
            "content": "将U盘插入VDR主机箱内的USB端口上。",
            "section_path": [],
            "image_asset_id": None,
            "image_caption": "",
        },
        {
            "element_id": uuid4(),
            "element_type": "IMAGE",
            "content": "",
            "section_path": [],
            "image_asset_id": uuid4(),
            "image_caption": "Telnet终端截图",
        },
    ],
}


def _candidate(
    document_id: Any,
    rank: int,
    *,
    title: str = TITLE_A,
    tokens: int = 20,
) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=uuid4(),
        document_id=document_id,
        document=title,
        chunk_type="TEXT",
        content="操作步骤内容",
        token_count=tokens,
        section_path=[],
        element_ids=[uuid4()],
        distance=None,
        similarity=None,
        rank=rank,
        sequence_no=rank,
    )


class _RecordingLlmProvider(FakeLlmProvider):
    def __init__(self, chunks: list[str]) -> None:
        super().__init__(chunks)
        self.prompts: list[str] = []

    async def stream(self, prompt: str):
        self.prompts.append(prompt)
        async for chunk in super().stream(prompt):
            yield chunk


class _RoutingRepository:
    """Mock repository covering the doc-routing surface of RagRepository."""

    def __init__(
        self,
        *,
        turn: Turn,
        candidates: list[RetrievalCandidate],
        document_blocks: dict[str, Any] | None = None,
        catalog: list[dict[str, Any]] | None = None,
    ) -> None:
        self.turn = turn
        self.candidates = candidates
        self.document_blocks = document_blocks or {
            str(DOC_A): DOC_A_ELEMENTS,
            str(DOC_B): {
                "document_id": str(DOC_B),
                "title": TITLE_B,
                "elements": [
                    {
                        "element_id": uuid4(),
                        "element_type": "TEXT",
                        "content": "准备工具并断电。",
                        "section_path": [],
                        "image_asset_id": None,
                        "image_caption": "",
                    }
                ],
            },
        }
        self.catalog = catalog
        self.completed: dict[str, Any] | None = None
        self.failed: dict[str, Any] | None = None
        self.saved: dict[str, Any] | None = None
        self.focus_updates: list[Any] = []
        self.pending_updates: list[dict[str, Any]] = []
        self.pending_clears: int = 0

    async def begin_turn(self, **kwargs: Any) -> Turn:
        return self.turn

    async def vector_search(self, **kwargs: Any) -> list[RetrievalCandidate]:
        return self.candidates

    async def bm25_search(self, **kwargs: Any) -> list[RetrievalCandidate]:
        return self.candidates

    async def save_prepared(self, turn: Turn, **kwargs: Any) -> None:
        self.saved = kwargs

    async def complete_turn(self, turn: Turn, **kwargs: Any) -> None:
        self.completed = kwargs

    async def fail_turn(self, turn: Turn, **kwargs: Any) -> None:
        self.failed = kwargs

    async def get_rerank_image_assets(self, **kwargs: Any) -> dict:
        return {}

    async def get_chunks_by_ids(self, **kwargs: Any) -> list:
        return []

    async def set_conversation_focus(self, conversation_id: Any, document_id: Any) -> None:
        self.focus_updates.append(document_id)

    async def clear_conversation_focus(self, conversation_id: Any) -> None:
        self.focus_updates.append(None)

    async def set_conversation_pending(
        self,
        conversation_id: Any,
        *,
        pending_options: list[dict[str, Any]],
        pending_query: str,
    ) -> None:
        self.pending_updates.append({"options": pending_options, "query": pending_query})

    async def clear_conversation_pending(self, conversation_id: Any) -> None:
        self.pending_clears += 1

    async def list_kb_documents(self, knowledge_id: Any) -> list[dict[str, Any]]:
        return (
            self.catalog
            if self.catalog is not None
            else [
                {"document_id": str(DOC_A), "title": TITLE_A},
                {"document_id": str(DOC_B), "title": TITLE_B},
            ]
        )

    async def get_document_blocks(
        self, *, knowledge_id: Any, index_id: Any, document_id: Any
    ) -> dict[str, Any]:
        blocks = self.document_blocks.get(str(document_id))
        if blocks is None:
            from app.common.errors import ApiError

            raise ApiError("DOCUMENT_NOT_FOUND", "文档不存在或已删除", 404)
        return blocks

    async def get_document_source(self, document_id: Any) -> dict[str, Any]:
        return {"display_name": TITLE_A, "filename": "a.docx"}


def _turn(
    *,
    focus_document_id: Any = None,
    chat_context: dict[str, Any] | None = None,
) -> Turn:
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
        focus_document_id=focus_document_id,
        chat_context=chat_context or {},
    )


def _service(
    repo: _RoutingRepository,
    llm: LlmProvider,
) -> RagService:
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
async def test_aligning_clear_winner_delivers_document_without_llm():
    # FakeRerankProvider scores 1.0 and 0.5 for the top two chunks, both from
    # DOC_A: hits=2, score=1.5 >= t_high, no runner-up -> LOCK and DELIVER.
    repo = _RoutingRepository(
        turn=_turn(),
        candidates=[_candidate(DOC_A, 1), _candidate(DOC_A, 2, title=TITLE_A)],
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
    assert TITLE_A in result["answer"]
    assert llm.prompts == []  # zero-LLM delivery path
    assert result["usage"] == {}
    types = [block["type"] for block in result["content"]]
    assert types == ["text", "text", "image"]
    image_block = result["content"][2]
    assert image_block["image_asset_id"]
    assert result["references"][0]["document_id"] == str(DOC_A)
    assert result["references"][0]["download_url"].endswith("/content?download=true")
    assert repo.focus_updates == [DOC_A]
    assert repo.completed is not None
    assert repo.completed["doc_routing"]["decision"] == "DELIVER"
    # delivery stores the summary (not the full blocks) as message content
    assert TITLE_A in repo.completed["answer"]
    assert repo.completed["usage"] == {}
    assert repo.failed is None


@pytest.mark.asyncio
async def test_doc_focus_stay_answers_from_full_document():
    repo = _RoutingRepository(
        turn=_turn(focus_document_id=DOC_A),
        candidates=[_candidate(DOC_A, 1)],
    )
    llm = _RecordingLlmProvider(
        [
            "[MODE:PRODUCT_KNOWLEDGE]\n第一步：将U盘插入VDR主机箱内的USB端口上。",
            "插入后等待2分钟。[IMG:S1]",
        ]
    )
    service = _service(repo, llm)

    result = await service.generate(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=repo.turn.conversation_id,
        question="第一步怎么做",
        request_id="req-stay",
    )

    assert result["response_type"] == "ANSWERED"
    assert result["answer_mode"] == "PRODUCT_KNOWLEDGE"
    # full document content is in the prompt
    assert "update升级" in llm.prompts[0]
    assert "当前文档" in llm.prompts[0]
    # image marker resolves to the doc image asset even without [S1] citations
    image_blocks = [b for b in result["content"] if b["type"] == "image"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["image_asset_id"] == str(DOC_A_ELEMENTS["elements"][2]["image_asset_id"])
    # references always include the focus document
    assert result["references"][0]["document_id"] == str(DOC_A)
    assert repo.focus_updates == []
    assert repo.completed["doc_routing"]["decision"] == "STAY"


@pytest.mark.asyncio
async def test_doc_focus_stay_without_citations_stays_answered():
    repo = _RoutingRepository(
        turn=_turn(focus_document_id=DOC_A),
        candidates=[_candidate(DOC_A, 1)],
    )
    llm = _RecordingLlmProvider(["[MODE:PRODUCT_KNOWLEDGE]\n将U盘插入USB端口。"])
    service = _service(repo, llm)

    result = await service.generate(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=repo.turn.conversation_id,
        question="怎么插U盘",
        request_id="req-stay-2",
    )

    # no citations in doc-QA mode must not downgrade to UNCONFIRMED
    assert result["response_type"] == "ANSWERED"
    assert result["answer_mode"] == "PRODUCT_KNOWLEDGE"


@pytest.mark.asyncio
async def test_ambiguous_match_clarifies_then_reply_resolves():
    repo = _RoutingRepository(
        turn=_turn(),
        candidates=[
            _candidate(DOC_A, 1),
            _candidate(DOC_B, 2, title=TITLE_B),
        ],
    )
    llm = _RecordingLlmProvider(["你想解决升级问题还是更换硬件问题？"])
    service = _service(repo, llm)

    result = await service.generate(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=None,
        question="PDC怎么处理",
        request_id="req-clarify",
    )

    assert result["response_type"] == "ANSWERED"
    assert result["references"] == []
    assert len(repo.pending_updates) == 1
    assert repo.pending_updates[0]["query"] == "PDC怎么处理"
    options = repo.pending_updates[0]["options"]
    assert [option["document_id"] for option in options] == [str(DOC_A), str(DOC_B)]
    assert "候选文档" in llm.prompts[0]
    assert TITLE_A in llm.prompts[0]

    # second turn: user picks option 2 -> deliver DOC_B without LLM
    repo.turn = _turn(chat_context={"pending_options": options, "pending_query": "PDC怎么处理"})
    repo.candidates = [_candidate(DOC_B, 1, title=TITLE_B)]
    llm.prompts.clear()
    result = await service.generate(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=repo.turn.conversation_id,
        question="第二个",
        request_id="req-clarify-reply",
    )
    assert result["response_type"] == "DOC_DELIVERED"
    assert result["references"][0]["document_id"] == str(DOC_B)
    assert llm.prompts == []
    assert repo.focus_updates == [DOC_B]
    assert repo.completed["doc_routing"]["resolved_from"] == "CLARIFICATION_REPLY"


@pytest.mark.asyncio
async def test_doc_focus_offer_switch_and_affirmative_reply():
    # FakeRerankProvider scores by index: B=1.0, C=0.5, A=0.33. The focus
    # doc (A) falls below stay_score while B passes t_low without reaching
    # the lock band (single hit) -> OFFER_SWITCH.
    doc_c = uuid4()
    repo = _RoutingRepository(
        turn=_turn(focus_document_id=DOC_A),
        candidates=[
            _candidate(DOC_B, 1, title=TITLE_B),
            _candidate(doc_c, 2, title="其他文档"),
            _candidate(DOC_A, 3),
        ],
    )
    llm = _RecordingLlmProvider(["这属于《更换故障PDC》，要切换查看吗？"])
    service = _service(repo, llm)

    result = await service.generate(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=repo.turn.conversation_id,
        question="怎么拆下故障PDC",
        request_id="req-offer",
    )

    assert result["response_type"] == "ANSWERED"
    assert len(repo.pending_updates) == 1
    offer_options = repo.pending_updates[0]["options"]
    assert offer_options[0]["document_id"] == str(DOC_B)
    assert repo.completed["doc_routing"]["decision"] == "OFFER_SWITCH"

    # user affirms -> switch to DOC_B
    repo.turn = _turn(
        focus_document_id=DOC_A,
        chat_context={"pending_options": offer_options, "pending_query": "怎么拆下故障PDC"},
    )
    result = await service.generate(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=repo.turn.conversation_id,
        question="好的",
        request_id="req-offer-reply",
    )
    assert result["response_type"] == "DOC_DELIVERED"
    assert result["references"][0]["document_id"] == str(DOC_B)
    assert repo.focus_updates == [DOC_B]


@pytest.mark.asyncio
async def test_doc_focus_switches_on_strong_other_document():
    repo = _RoutingRepository(
        turn=_turn(focus_document_id=DOC_A),
        candidates=[
            _candidate(DOC_B, 1, title=TITLE_B),
            _candidate(DOC_B, 2, title=TITLE_B),
            _candidate(DOC_A, 3),
        ],
    )
    llm = _RecordingLlmProvider(["不应被调用"])
    service = _service(repo, llm)

    result = await service.generate(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=repo.turn.conversation_id,
        question="如何更换故障PDC",
        request_id="req-switch",
    )

    assert result["response_type"] == "DOC_DELIVERED"
    assert result["references"][0]["document_id"] == str(DOC_B)
    assert llm.prompts == []
    assert repo.focus_updates == [DOC_B]


@pytest.mark.asyncio
async def test_no_match_empty_catalog_returns_fixed_unconfirmed():
    repo = _RoutingRepository(
        turn=_turn(),
        candidates=[],
        catalog=[],
    )
    llm = _RecordingLlmProvider(["不应被调用"])
    service = _service(repo, llm)

    result = await service.generate(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=None,
        question="今天天气如何",
        request_id="req-no-match",
    )

    assert result["response_type"] == "ANSWERED"
    assert "当前知识库没有足够的可靠信息" in result["answer"]
    assert llm.prompts == []
    assert repo.completed["doc_routing"]["decision"] == "NO_MATCH"


@pytest.mark.asyncio
async def test_single_eligible_candidate_clarifies_with_pending():
    # one candidate passes t_low but cannot lock (single hit): the routing
    # answer asks once and records the pending option, so the user's next
    # reply (affirmative or the title) delivers the document
    repo = _RoutingRepository(
        turn=_turn(),
        candidates=[_candidate(DOC_A, 1)],
    )
    llm = _RecordingLlmProvider(
        ["您是想了解《故障解决文档11—如何U盘升级PDC》的内容吗？"]
    )
    service = _service(repo, llm)

    result = await service.generate(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=None,
        question="你可以帮我做什么",
        request_id="req-catalog",
    )

    assert result["response_type"] == "ANSWERED"
    assert "候选文档" in llm.prompts[0]
    assert TITLE_A in llm.prompts[0]
    assert len(repo.pending_updates) == 1
    assert repo.pending_updates[0]["options"][0]["document_id"] == str(DOC_A)

    # second turn: the user replies with the document title (no spaces) and
    # the pending resolves to DELIVER without asking again
    pending_options = repo.pending_updates[0]["options"]
    repo.turn = _turn(
        chat_context={
            "pending_options": pending_options,
            "pending_query": "你可以帮我做什么",
        }
    )
    repo.candidates = []
    llm.prompts.clear()
    result = await service.generate(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=repo.turn.conversation_id,
        question="如何U盘升级PDC",
        request_id="req-catalog-reply",
    )
    assert result["response_type"] == "DOC_DELIVERED"
    assert result["references"][0]["document_id"] == str(DOC_A)
    assert llm.prompts == []
    assert repo.completed["doc_routing"]["resolved_from"] == "CLARIFICATION_REPLY"


@pytest.mark.asyncio
async def test_delivery_truncates_large_document():
    huge = dict(DOC_A_ELEMENTS)
    huge["elements"] = [
        {
            "element_id": uuid4(),
            "element_type": "TEXT",
            "content": "长段落" * 10000,
            "section_path": [],
            "image_asset_id": None,
            "image_caption": "",
        }
    ]
    repo = _RoutingRepository(
        turn=_turn(),
        candidates=[_candidate(DOC_A, 1), _candidate(DOC_A, 2)],
        document_blocks={str(DOC_A): huge},
    )
    service = _service(repo, _RecordingLlmProvider(["不应被调用"]))

    result = await service.generate(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=None,
        question="如何U盘升级PDC",
        request_id="req-truncate",
    )

    assert result["response_type"] == "DOC_DELIVERED"
    texts = [b for b in result["content"] if b["type"] == "text"]
    assert any("已截断" in (b["text"] or "") for b in texts)
    assert result["references"][0]["download_url"]


@pytest.mark.asyncio
async def test_delivery_summary_message_kept_out_of_history_payload():
    repo = _RoutingRepository(
        turn=_turn(),
        candidates=[_candidate(DOC_A, 1), _candidate(DOC_A, 2)],
    )
    service = _service(repo, _RecordingLlmProvider(["不应被调用"]))

    result = await service.generate(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=None,
        question="如何U盘升级PDC",
        request_id="req-summary",
    )

    # the persisted message is a short summary, never the full element blocks
    answer = repo.completed["answer"]
    assert len(answer) < 200
    assert "长" not in answer
    assert json.dumps(result["content"])  # blocks serialized in the response only


async def _collect(frames) -> list[tuple[str, dict[str, Any]]]:
    parsed = []
    for frame in frames:
        text = frame.decode()
        event_name = text.splitlines()[0].removeprefix("event: ")
        payload = json.loads(text.splitlines()[1].removeprefix("data: "))
        parsed.append((event_name, payload))
    return parsed


@pytest.mark.asyncio
async def test_chat_stream_delivers_document_without_message_events():
    repo = _RoutingRepository(
        turn=_turn(),
        candidates=[_candidate(DOC_A, 1), _candidate(DOC_A, 2)],
    )
    service = _service(repo, _RecordingLlmProvider(["不应被调用"]))

    frames = [
        frame
        async for frame in service.chat_stream(
            knowledge_id=repo.turn.knowledge_id,
            conversation_id=None,
            question="如何U盘升级PDC",
            request_id="req-sse-deliver",
        )
    ]
    events = await _collect(frames)

    assert [name for name, _ in events] == ["trace", "source", "done"]
    trace_payload = events[0][1]
    assert trace_payload["conversation_id"] == str(repo.turn.conversation_id)
    source_payload = events[1][1]
    assert source_payload["sources"][0]["source_id"] == "S1"
    done_payload = events[2][1]
    assert done_payload["response_type"] == "DOC_DELIVERED"
    assert [block["type"] for block in done_payload["content"]] == [
        "text",
        "text",
        "image",
    ]
    assert done_payload["references"][0]["document_id"] == str(DOC_A)
    assert done_payload["usage"] == {}
    assert repo.focus_updates == [DOC_A]


@pytest.mark.asyncio
async def test_catalog_title_reply_delivers_document_without_asking_again():
    # live regression: turn 1 offered a document via the open-ended catalog
    # answer (no pending recorded); turn 2 the user echoed the document
    # title verbatim and the system asked AGAIN. The catalog-title match
    # must deliver the document directly.
    repo = _RoutingRepository(
        turn=_turn(),
        candidates=[],  # nothing lock-grade; decision is NO_MATCH
        catalog=[
            {"document_id": str(DOC_A), "title": "故障解决文档11—如何U盘升级PDC"},
            {"document_id": str(DOC_B), "title": TITLE_B},
        ],
    )
    llm = _RecordingLlmProvider(["不应被调用"])
    service = _service(repo, llm)

    # trailing question mark (e.g. a welcome starter chip) behaves the same
    result = await service.generate(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=None,
        question="如何U盘升级PDC？",
        request_id="req-title-match",
    )

    assert result["response_type"] == "DOC_DELIVERED"
    assert result["references"][0]["document_id"] == str(DOC_A)
    assert llm.prompts == []
    assert repo.focus_updates == [DOC_A]
    assert repo.completed["doc_routing"]["resolved_from"] == "TITLE_MATCH"
    assert repo.failed is None


@pytest.mark.asyncio
async def test_catalog_title_match_requires_meaningful_length():
    repo = _RoutingRepository(
        turn=_turn(),
        candidates=[],
        catalog=[{"document_id": str(DOC_A), "title": "故障解决文档11—如何U盘升级PDC"}],
    )
    llm = _RecordingLlmProvider(["我可以帮你解决故障问题，请描述具体目标。"])
    service = _service(repo, llm)

    result = await service.generate(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=None,
        question="升级",  # only 2 normalized chars: below the 4-char guard
        request_id="req-title-guard",
    )

    # too short to be an explicit document name: normal catalog routing runs
    assert result["response_type"] == "ANSWERED"
    assert "文档目录" in llm.prompts[0]
    assert repo.focus_updates == []


@pytest.mark.asyncio
async def test_chat_stream_doc_qa_strips_mode_tag_and_emits_deltas():
    repo = _RoutingRepository(
        turn=_turn(focus_document_id=DOC_A),
        candidates=[_candidate(DOC_A, 1)],
    )
    llm = _RecordingLlmProvider(
        [
            "[MODE:PRODUCT_KNOWLEDGE]\n将U盘插入",
            "USB端口。[IMG:S1]",
        ]
    )
    service = _service(repo, llm)

    frames = [
        frame
        async for frame in service.chat_stream(
            knowledge_id=repo.turn.knowledge_id,
            conversation_id=repo.turn.conversation_id,
            question="怎么插U盘",
            request_id="req-sse-stay",
        )
    ]
    events = await _collect(frames)

    assert [name for name, _ in events][:3] == ["trace", "source", "message"]
    deltas = [payload["delta"] for name, payload in events if name == "message"]
    assert "".join(deltas) == "将U盘插入USB端口。[IMG:S1]"
    done_payload = events[-1][1]
    assert done_payload["response_type"] == "ANSWERED"
    assert done_payload["answer_mode"] == "PRODUCT_KNOWLEDGE"
    image_blocks = [b for b in done_payload["content"] if b["type"] == "image"]
    assert len(image_blocks) == 1
    assert done_payload["references"][0]["document_id"] == str(DOC_A)


@pytest.mark.asyncio
async def test_chat_stream_routing_answer_streams_without_mode_tag():
    repo = _RoutingRepository(
        turn=_turn(),
        candidates=[
            _candidate(DOC_A, 1),
            _candidate(DOC_B, 2, title=TITLE_B),
        ],
    )
    llm = _RecordingLlmProvider(["你想", "解决升级还是更换问题？"])
    service = _service(repo, llm)

    frames = [
        frame
        async for frame in service.chat_stream(
            knowledge_id=repo.turn.knowledge_id,
            conversation_id=None,
            question="PDC怎么处理",
            request_id="req-sse-clarify",
        )
    ]
    events = await _collect(frames)

    deltas = [payload["delta"] for name, payload in events if name == "message"]
    assert "".join(deltas) == "你想解决升级还是更换问题？"
    done_payload = events[-1][1]
    assert done_payload["response_type"] == "ANSWERED"
    assert done_payload["references"] == []
    assert repo.pending_updates[0]["options"][0]["document_id"] == str(DOC_A)


def _huge_document() -> dict[str, Any]:
    return {
        "document_id": str(DOC_A),
        "title": TITLE_A,
        "elements": [
            {
                "element_id": uuid4(),
                "element_type": "TEXT",
                "content": "长" * 60_001,
                "section_path": [],
                "image_asset_id": None,
                "image_caption": "",
            }
        ],
    }


@pytest.mark.asyncio
async def test_doc_focus_stay_degrades_to_chunk_rag_when_document_too_large():
    repo = _RoutingRepository(
        turn=_turn(focus_document_id=DOC_A),
        candidates=[_candidate(DOC_A, 1)],
        document_blocks={str(DOC_A): _huge_document()},
    )
    llm = _RecordingLlmProvider(["[MODE:PRODUCT_KNOWLEDGE]\n将U盘插入USB端口。[S1]"])
    service = _service(repo, llm)

    result = await service.generate(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=repo.turn.conversation_id,
        question="怎么插U盘",
        request_id="req-stay-degrade",
    )

    assert result["response_type"] == "ANSWERED"
    assert result["answer_mode"] == "PRODUCT_KNOWLEDGE"
    # degraded prompt is the chunk-QA variant: retrieved chunk context plus
    # the mode-tag rules, never the oversized full document
    assert "操作步骤内容" in llm.prompts[0]
    assert "资料片段" in llm.prompts[0]
    assert "[MODE:PRODUCT_KNOWLEDGE]" in llm.prompts[0]
    assert "长" * 100 not in llm.prompts[0]
    assert "历史" in llm.prompts[0]
    # chunk citation resolves to the retrieved candidate source
    assert repo.completed["sources"][0]["source_id"] == "S1"
    assert repo.completed["sources"][0]["chunk_id"] == str(repo.candidates[0].chunk_id)
    # citations are stripped from content blocks
    assert [block["type"] for block in result["content"]] == ["text"]
    assert "将U盘插入USB端口" in result["content"][0]["text"]
    assert "[S1]" not in result["content"][0]["text"]
    # references still point at the focus document
    assert result["references"][0]["document_id"] == str(DOC_A)
    assert repo.completed["doc_routing"]["decision"] == "STAY"
    assert repo.completed["doc_routing"]["degraded_to"] == "CHUNK_RAG"
    assert (
        repo.completed["doc_routing"]["degradation_reason"]
        == "DOCUMENT_EXCEEDS_PROMPT_BUDGET"
    )
    assert repo.failed is None


@pytest.mark.asyncio
async def test_degraded_chunk_rag_keeps_general_answer_mode():
    # the degraded path must keep PRODUCT_GENERAL: product-related questions
    # that do not depend on retrieved chunks get a general answer + disclaimer
    repo = _RoutingRepository(
        turn=_turn(focus_document_id=DOC_A),
        candidates=[_candidate(DOC_A, 1)],
        document_blocks={str(DOC_A): _huge_document()},
    )
    llm = _RecordingLlmProvider(["[MODE:PRODUCT_GENERAL]\nU盘升级通常需要格式化为FAT32。"])
    service = _service(repo, llm)

    result = await service.generate(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=repo.turn.conversation_id,
        question="升级U盘一般用什么格式",
        request_id="req-stay-degrade-general",
    )

    assert result["response_type"] == "ANSWERED"
    assert result["answer_mode"] == "PRODUCT_GENERAL"
    assert result["disclaimer"] is not None
    assert "不代表" in result["disclaimer"]
    assert repo.completed["sources"] == []
    assert repo.failed is None


@pytest.mark.asyncio
async def test_chat_stream_degraded_chunk_rag_strips_mode_tag():
    repo = _RoutingRepository(
        turn=_turn(focus_document_id=DOC_A),
        candidates=[_candidate(DOC_A, 1)],
        document_blocks={str(DOC_A): _huge_document()},
    )
    llm = _RecordingLlmProvider(
        ["[MODE:PRODUCT_KNOWLEDGE]\n将U盘插入", "USB端口。[S1]"]
    )
    service = _service(repo, llm)

    frames = [
        frame
        async for frame in service.chat_stream(
            knowledge_id=repo.turn.knowledge_id,
            conversation_id=repo.turn.conversation_id,
            question="怎么插U盘",
            request_id="req-sse-stay-degrade",
        )
    ]
    events = await _collect(frames)

    # mode tag is stripped; the citation marker streams through untouched
    deltas = [payload["delta"] for name, payload in events if name == "message"]
    assert "".join(deltas) == "将U盘插入USB端口。[S1]"
    done_payload = events[-1][1]
    assert done_payload["response_type"] == "ANSWERED"
    assert done_payload["answer_mode"] == "PRODUCT_KNOWLEDGE"
    assert done_payload["sources"][0]["source_id"] == "S1"
    assert done_payload["references"][0]["document_id"] == str(DOC_A)
    assert repo.failed is None


@pytest.mark.asyncio
async def test_generate_llm_failure_raises_and_marks_failed():
    repo = _RoutingRepository(
        turn=_turn(),
        candidates=[_candidate(DOC_A, 1)],
    )

    class _FailProvider:
        usage: ClassVar[dict[str, Any]] = {}

        async def stream(self, prompt: str) -> Any:
            if False:
                yield ""
            raise LlmError("UPSTREAM_TIMEOUT", "timeout", retryable=True)

        async def aclose(self) -> None:
            return None

    settings = Settings(
        _env_file=None,
        app_env="test",
        m2_embedding_provider="fake",
    )
    service = RagService(
        cast(RagRepository, repo),
        settings,
        embedding_factory=lambda _: FakeEmbeddingProvider(1024),
        llm_factory=lambda s, snap: cast(LlmProvider, _FailProvider()),
        rerank_factory=lambda s, snap: FakeRerankProvider(),
    )

    with pytest.raises(ApiError) as exc_info:
        await service.generate(
            knowledge_id=repo.turn.knowledge_id,
            conversation_id=None,
            question="如何升级",
            request_id="req-llm-fail",
        )

    assert exc_info.value.code == "UPSTREAM_TIMEOUT"
    assert repo.failed is not None
    assert repo.failed["status"] == "FAILED"
    assert repo.pending_updates == []
