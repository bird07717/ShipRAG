from __future__ import annotations

import asyncio
from typing import cast
from uuid import uuid4

import httpx
import pytest
from test_assistant_content_consistency import message_row
from test_assistant_facade import _Repository
from test_chat_doc_routing_flow import (
    DOC_A,
    DOC_B,
    TITLE_B,
    _candidate,
    _RecordingLlmProvider,
    _RoutingRepository,
    _service,
    _turn,
)
from test_chat_routing import _sources

from app.api.assistant import _message
from app.assistant.service import AssistantFacadeService, parse_sse
from app.core.config import Settings
from app.gateway.repository import GatewayRepository
from app.gateway.service import ChatGatewayService
from app.rag.llm import LlmError, ZhipuLlmProvider
from app.rag.models import ModelSnapshot
from app.rag.presentation import ModelTextStream, clean_model_text
from app.rag.query_rewrite import document_followup_excerpt
from app.rag.routing import AnswerMode, post_validate
from app.services.rag import _is_explanation_question


@pytest.mark.parametrize(
    "question,expected",
    [
        ("文档说打开电脑的运行功能，Windows里如何打开？", "打开电脑的运行功能"),
        (
            "当前文档提到“将U盘插入VDR主机箱内的USB端口上”，具体怎么操作？",
            "将U盘插入VDR主机箱内的USB端口上",
        ),
        ("文档说打开电脑的运行功能，如何升级FFC？", None),
        ("文档说打开电脑的运行功能，今天天气怎样？", None),
        ("文档说打开电脑的运行功能，改为查看其他文档", None),
        ("打开电脑的运行功能，Windows里如何打开？", None),
    ],
)
def test_document_excerpt_only_accepts_object_free_clarifications(question, expected):
    assert document_followup_excerpt(question) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("strong_other", [False, True])
@pytest.mark.parametrize("excerpt_exists", [False, True])
async def test_literal_document_followup_uses_only_verified_frozen_document(
    strong_other, excerpt_exists
):
    candidates = [_candidate(DOC_B, 1, title=TITLE_B)]
    candidates += [
        _candidate(
            DOC_B if strong_other else uuid4(), 2, title=TITLE_B if strong_other else "其他文档"
        ),
        _candidate(DOC_A, 3),
    ]
    repo = _RoutingRepository(turn=_turn(focus_document_id=DOC_A), candidates=candidates)
    rag = _service(repo, _RecordingLlmProvider([]))
    excerpt = "将U盘插入VDR主机箱内的USB端口上" if excerpt_exists else "按下未记载的系统复位按钮"
    question = f"文档说{excerpt}，具体怎么操作？"
    prepared = await rag.prepare(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=repo.turn.conversation_id,
        question=question,
        request_id="excerpt",
    )
    plan, error = await rag._safely_plan_chat_turn(prepared, question, "产品", "支持")
    assert error is None
    if excerpt_exists:
        assert plan.kind == "DOC_QA"
        assert plan.routing["resolved_from"] == "FOCUS_EXCERPT"
        assert plan.routing["decision"] == "STAY"
        assert plan.references[0]["document_id"] == str(DOC_A)
        assert repo.focus_updates == [] and repo.pending_updates == []
    else:
        assert plan.kind == ("DELIVER" if strong_other else "ROUTING")
        assert plan.routing.get("resolved_from") != "FOCUS_EXCERPT"


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["pending", "degraded"])
async def test_document_excerpt_does_not_override_confirmation_or_degraded_retrieval(boundary):
    context = {"pending_options": [{"document_id": str(DOC_B), "title": TITLE_B}]}
    repo = _RoutingRepository(
        turn=_turn(focus_document_id=DOC_A, chat_context=context if boundary == "pending" else {}),
        candidates=[_candidate(DOC_B, 1, title=TITLE_B)],
    )
    rag = _service(repo, _RecordingLlmProvider([]))
    question = "文档说将U盘插入VDR主机箱内的USB端口上，具体怎么操作？"
    prepared = await rag.prepare(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=repo.turn.conversation_id,
        question=question,
        request_id="excerpt-boundary",
    )
    if boundary == "degraded":
        prepared.rerank_status = "DEGRADED"
    plan, error = await rag._safely_plan_chat_turn(prepared, question, "产品", "支持")
    assert error is None
    assert plan.routing.get("resolved_from") != "FOCUS_EXCERPT"
    assert repo.focus_updates == []


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 7, 20, 1000])
def test_text_stream_hides_split_controls_and_preserves_markdown(chunk_size):
    raw = "\n[MODE:PRODUCT_EXPLAINED]\n**说明**：检查[S1]图[IMG:S12]。\x02[注意]保留。\n[x](https://example.test)"
    cleaner = ModelTextStream()
    visible = "".join(cleaner.feed(raw[i : i + chunk_size]) for i in range(0, len(raw), chunk_size))
    visible += cleaner.feed("", final=True)
    assert visible == "**说明**：检查图。[注意]保留。\n[x](https://example.test)"
    assert visible == clean_model_text(raw)
    assert cleaner.pending == ""


def test_image_punctuation_and_public_answer_never_contain_control_markers():
    sources = _sources(with_images=True)
    result = post_validate(
        "[MODE:PRODUCT_KNOWLEDGE]查看示意图[IMG:S1]。[S1]继续检查。",
        sources,
        True,
    )
    assert result.answer == "查看示意图。继续检查。"
    assert [b.type for b in result.content] == ["text", "image", "text"]
    assert result.content[0].text == "查看示意图。"
    assert result.content[2].text == "继续检查。"
    assert all(ord(c) >= 32 for c in result.answer)


def test_mixed_explanation_keeps_document_evidence_but_not_general_citations():
    sources = _sources(2, with_images=True)
    result = post_validate(
        "[MODE:PRODUCT_EXPLAINED]\n**文档依据**\n先拍照标记。[S1]\n"
        "**分析与推断**\n由此可辅助复原接线。\n"
        "**通用建议**\n也可给线缆贴标签。[S2][IMG:S2]",
        sources,
        True,
    )
    assert result.answer_mode == AnswerMode.PRODUCT_EXPLAINED
    assert [s["source_id"] for s in result.valid_sources] == ["S1"]
    assert "通用建议" in result.answer and "贴标签" in result.answer
    assert "文档引用仅支持" in result.disclaimer
    assert all(b.type == "text" for b in result.content)


def test_general_section_cannot_supply_missing_document_evidence():
    result = post_validate(
        "[MODE:PRODUCT_EXPLAINED]\n**通用建议**\n可能是连接异常。[S1]",
        _sources(),
        True,
    )
    assert result.response_type.value == "UNCONFIRMED"
    assert result.valid_sources == []
    general = post_validate("[MODE:PRODUCT_GENERAL]可使用Win+R。[S99][IMG:S1]", _sources(), False)
    assert general.response_type.value == "ANSWERED"
    assert general.answer == "可使用Win+R。"
    assert general.citation_result["valid_source_ids"] == []
    assert general.citation_result["citation_missing"] is False


@pytest.mark.parametrize(
    "question,expected",
    [
        ("更换PSU电源板时为什么要给线缆拍照做标记？", True),
        ("VAU图像的Horizontal offset越大，图像会怎样移动？", True),
        ("为什么更换LRU单元后还要update并配置？", True),
        ("为什么要同步面板上的UTC和SYS时间？", True),
        ("请提供完整的操作步骤并解释原因", False),
        ("如何更换PSU电源板", False),
        ("电源电压是多少", False),
        ("查看原文中的故障原因", False),
    ],
)
def test_explanation_routing_distinguishes_full_document_requests(question, expected):
    assert _is_explanation_question(question) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,expected_kind", [("off", "DELIVER"), ("shadow", "DELIVER"), ("enabled", "DOC_QA")]
)
async def test_explanation_uses_confirmed_document_and_existing_focus_cas(mode, expected_kind):
    repo = _RoutingRepository(turn=_turn(), candidates=[_candidate(DOC_A, 1), _candidate(DOC_A, 2)])
    service = _service(repo, _RecordingLlmProvider([]))
    service.settings = service.settings.model_copy(update={"m3_rag_explanation_mode": mode})
    prepared = await service.prepare(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=None,
        question="为什么需要升级",
        request_id="explanation",
    )
    plan, error = await service._safely_plan_chat_turn(prepared, "为什么需要升级", "产品", "支持")
    assert error is None and plan.kind == expected_kind
    assert repo.focus_updates == [DOC_A]
    assert prepared.turn.context_version == 1


@pytest.mark.asyncio
async def test_explanation_does_not_override_clarification_confirmation():
    repo = _RoutingRepository(
        turn=_turn(
            chat_context={
                "pending_options": [{"document_id": str(DOC_A), "title": "为什么需要升级"}]
            }
        ),
        candidates=[_candidate(DOC_A, 1)],
    )
    service = _service(repo, _RecordingLlmProvider([]))
    service.settings = service.settings.model_copy(update={"m3_rag_explanation_mode": "enabled"})
    prepared = await service.prepare(
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=None,
        question="为什么需要升级",
        request_id="confirmation",
    )
    plan, error = await service._safely_plan_chat_turn(prepared, "为什么需要升级", "产品", "支持")
    assert error is None and plan.kind == "DELIVER"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_assistant_facade_stream_terminal_history_and_replay_use_clean_text(failure):
    repo = _RoutingRepository(
        turn=_turn(focus_document_id=DOC_A), candidates=[_candidate(DOC_A, 1)]
    )

    class Provider(_RecordingLlmProvider):
        async def stream(self, prompt):
            for part in ["[MO", "DE:PRODUCT_EXPLAINED]", "\n检查接线", "[IMG:", "S1]", "。"]:
                yield part
            if failure:
                raise LlmError("LLM_UPSTREAM_ERROR", "模型流中断", retryable=True)

    rag = _service(repo, Provider([]))

    class Bridge:
        async def stream(self, **kwargs):
            async for frame in rag.chat_stream(
                knowledge_id=repo.turn.knowledge_id,
                conversation_id=repo.turn.conversation_id,
                question="怎么检查",
                request_id=kwargs["request_id"],
            ):
                yield frame

    class Requests(_Repository):
        async def reserve_assistant_request(self, **kwargs):
            if self.replay:
                return "REPLAY", {
                    "id": uuid4(),
                    "status": "FAILED" if failure else "COMPLETED",
                    "terminal_error": self.failed["payload"] if failure else {},
                    "terminal_response": self.completed["response"] if not failure else {},
                }
            return await super().reserve_assistant_request(**kwargs)

        async def assistant_turn_context(self, **kwargs):
            t = repo.turn
            return {
                "conversation_id": t.conversation_id,
                "user_message_id": t.user_message_id,
                "assistant_message_id": t.assistant_message_id,
                "trace_id": t.trace_id,
            }

    requests = Requests()
    facade = AssistantFacadeService(
        cast(GatewayRepository, requests), cast(ChatGatewayService, Bridge())
    )
    kwargs = dict(
        external_user_id="caller",
        knowledge_id=repo.turn.knowledge_id,
        conversation_id=repo.turn.conversation_id,
        question="怎么检查",
        choice_id=None,
        include_images=True,
        idempotency_key="one",
        request_id="reasoning-contract",
    )
    events = [parse_sse(frame) async for frame in facade.stream(**kwargs)]
    visible = "".join(body["text"] for event, body in events if event == "delta")
    assert visible == "检查接线。"
    assert [event for event, _ in events if event in {"complete", "error"}] == [
        "error" if failure else "complete"
    ]
    if failure:
        error = events[-1][1]
        assert error["partial_content"] == visible and error["retryable"] is True
        assert requests.completed is None and repo.completed is None
        assert requests.failed["payload"] == error
    else:
        complete = events[-1][1]
        assert complete["answer"] == visible
        assert any(b["type"] == "image" for b in complete["content"])
        assert complete["disclaimer"]
        assert requests.completed["response"] == complete
        history = _message(message_row(snapshot=complete, blocks=[]))
        assert history["content"] == complete["answer"]
        assert history["content_blocks"] == complete["content"]
        assert history["disclaimer"] == complete["disclaimer"]
    requests.replay = True
    replay = [
        parse_sse(frame) async for frame in facade.stream(**{**kwargs, "request_id": "retry"})
    ]
    assert [event for event, _ in replay] == ["accepted", "error" if failure else "complete"]
    assert replay[-1][1] == events[-1][1]


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["length", "eof", "stop", "done"])
async def test_provider_requires_successful_stream_termination(ending):
    body = 'data: {"choices":[{"delta":{"content":"部分回答"}}]}\n\n'
    if ending in {"length", "stop"}:
        body += 'data: {"choices":[{"delta":{},"finish_reason":"' + ending + '"}]}\n\n'
    if ending == "done":
        body += "data: [DONE]\n\n"
    async with httpx.AsyncClient(
        base_url="https://model.test/",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body)),
    ) as client:
        provider = ZhipuLlmProvider(
            Settings(_env_file=None, siliconflow_api_key="test-api-key"),
            ModelSnapshot("siliconflow", "test", {}),
            client,
        )
        chunks = []
        if ending in {"length", "eof"}:
            with pytest.raises(LlmError) as error:
                async for chunk in provider.stream("prompt"):
                    chunks.append(chunk)
            assert error.value.code == "LLM_UPSTREAM_ERROR" and error.value.retryable
        else:
            chunks = [chunk async for chunk in provider.stream("prompt")]
        assert chunks == ["部分回答"]


@pytest.mark.asyncio
async def test_stream_deadline_emits_retryable_error_without_done():
    repo = _RoutingRepository(
        turn=_turn(focus_document_id=DOC_A), candidates=[_candidate(DOC_A, 1)]
    )

    class Slow(_RecordingLlmProvider):
        async def stream(self, prompt):
            yield "[MODE:PRODUCT_KNOWLEDGE]已生成的部分。"
            await asyncio.sleep(10)

    rag = _service(repo, Slow([]))
    rag.settings = rag.settings.model_copy(update={"m3_turn_deadline_seconds": 0.05})
    events = [
        parse_sse(frame)
        async for frame in rag.chat_stream(
            knowledge_id=repo.turn.knowledge_id,
            conversation_id=repo.turn.conversation_id,
            question="怎么检查",
            request_id="timeout",
        )
    ]
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "TURN_DEADLINE_EXCEEDED"
    assert events[-1][1]["retryable"] is True
    assert repo.completed is None
    assert repo.failed["partial_answer"] == "已生成的部分。"


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["", "[MODE:PRODUCT_KNOWLEDGE]", "\n[MODE:PRODUCT_GENERAL]\x02"])
async def test_empty_visible_answer_is_retryable_failure(raw):
    repo = _RoutingRepository(
        turn=_turn(focus_document_id=DOC_A), candidates=[_candidate(DOC_A, 1)]
    )
    rag = _service(repo, _RecordingLlmProvider([raw]))
    events = [
        parse_sse(frame)
        async for frame in rag.chat_stream(
            knowledge_id=repo.turn.knowledge_id,
            conversation_id=repo.turn.conversation_id,
            question="怎么检查",
            request_id="empty",
        )
    ]
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "LLM_EMPTY_RESPONSE"
    assert events[-1][1]["retryable"] is True
    assert repo.completed is None
