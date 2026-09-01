from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from app.common.errors import ApiError
from app.gateway.models import ChatEngine, ConversationMode, ExecutionPolicy, ResolvedPolicy
from app.gateway.repository import GatewayRepository
from app.gateway.service import ChatGatewayService
from app.rag.models import RetrievalConfig


class FakeGatewayRepository:
    def __init__(self) -> None:
        self.executions: list[tuple[UUID, ChatEngine]] = []
        self.links: list[tuple[UUID, UUID, str]] = []

    async def record_conversation_execution(
        self, *, conversation_id: UUID, policy: ResolvedPolicy, engine: ChatEngine
    ) -> None:
        del policy
        self.executions.append((conversation_id, engine))

    async def link_fallback(
        self, *, support_trace_id: UUID, rag_trace_id: UUID, reason: str
    ) -> None:
        self.links.append((support_trace_id, rag_trace_id, reason))


class FakeSupport:
    def __init__(self, response_type: str) -> None:
        self.response_type = response_type
        self.calls = 0
        self.conversation_id = uuid4()
        self.trace_id = uuid4()
        self.user_message_id = uuid4()
        self.assistant_message_id = uuid4()
        self.gateway_response: dict[str, Any] | None = None
        self.saved_gateway_responses: list[dict[str, Any]] = []

    async def handle(self, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        response = {
            "message_id": str(uuid4()),
            "conversation_id": str(self.conversation_id),
            "trace_id": str(self.trace_id),
            "index_id": str(uuid4()),
            "response_type": self.response_type,
            "answer": "support",
            "content": [],
            "references": [],
            "usage": {},
            "latency_ms": 1,
        }
        if kwargs.get("return_internal_ids") and self.response_type == "NO_MATCH":
            response["_fallback_message_pair"] = {
                "user_message_id": str(self.user_message_id),
                "assistant_message_id": str(self.assistant_message_id),
            }
        if self.gateway_response is not None:
            response["_gateway_response"] = self.gateway_response
        return response

    async def save_gateway_response(self, **kwargs: Any) -> None:
        self.saved_gateway_responses.append(dict(kwargs["response"]))


class FakeRag:
    def __init__(self) -> None:
        self.calls = 0
        self.conversation_id = uuid4()
        self.trace_id = uuid4()
        self.last_kwargs: dict[str, Any] = {}
        self.repository = _FakeRagConfigRepository()

    async def generate(self, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        self.last_kwargs = kwargs
        conversation_id = kwargs.get("conversation_id") or self.conversation_id
        return {
            "message_id": str(uuid4()),
            "conversation_id": str(conversation_id),
            "trace_id": str(self.trace_id),
            "index_id": str(uuid4()),
            "response_type": "ANSWERED",
            "answer": "rag",
            "content": [],
            "references": [],
            "usage": {},
            "latency_ms": 2,
        }

    async def chat_stream(self, **kwargs: Any):  # type: ignore[no-untyped-def]
        response = await self.generate(**kwargs)
        yield (
            "event: done\ndata: "
            + json.dumps(response, ensure_ascii=False, separators=(",", ":"))
            + "\n\n"
        ).encode()


class _FakeRagConfigRepository:
    async def get_engine_retrieval_config(self, policy: str) -> RetrievalConfig:
        del policy
        return RetrievalConfig(
            vector_top_k=7,
            bm25_top_k=8,
            fusion_top_k=9,
            rerank_top_n=6,
            context_max_chunks=5,
        )


def policy(value: ExecutionPolicy, mode: ConversationMode | None = None) -> ResolvedPolicy:
    return ResolvedPolicy(
        knowledge_id=uuid4(),
        policy=value,
        config_version=3,
        fallback_conditions=("NO_MATCH",),
        conversation_mode=mode,
    )


def test_policy_readiness_requires_only_the_engines_selected_by_policy() -> None:
    readiness = {
        "support": {"blockers": ["support blocked"]},
        "rag": {"blockers": ["rag blocked"]},
    }

    assert GatewayRepository._policy_blockers(ExecutionPolicy.RAG_ONLY, readiness) == [
        "rag blocked"
    ]
    assert GatewayRepository._policy_blockers(
        ExecutionPolicy.SUPPORT_WITH_RAG_FALLBACK, readiness
    ) == ["support blocked", "rag blocked"]


def kwargs(knowledge_id: UUID) -> dict[str, Any]:
    return {
        "knowledge_id": knowledge_id,
        "conversation_id": None,
        "question": "问题",
        "request_id": "request-1",
        "user_id": None,
        "problem_code": None,
        "document_id": None,
        "choice_id": None,
        "include_images": True,
        "idempotency_key": None,
    }


async def test_hybrid_support_result_stays_in_support() -> None:
    repository = FakeGatewayRepository()
    support = FakeSupport("ANSWERED")
    rag = FakeRag()
    resolved = policy(ExecutionPolicy.SUPPORT_WITH_RAG_FALLBACK)
    service = ChatGatewayService(repository, support, rag)  # type: ignore[arg-type]

    response = await service.handle(policy=resolved, **kwargs(resolved.knowledge_id))

    assert response["answer"] == "support"
    assert support.calls == 1
    assert rag.calls == 0
    assert repository.executions == [(support.conversation_id, ChatEngine.SUPPORT)]


async def test_rag_only_bypasses_support() -> None:
    repository = FakeGatewayRepository()
    support = FakeSupport("NO_MATCH")
    rag = FakeRag()
    resolved = policy(ExecutionPolicy.RAG_ONLY)
    service = ChatGatewayService(repository, support, rag)  # type: ignore[arg-type]

    response = await service.handle(policy=resolved, **kwargs(resolved.knowledge_id))

    assert response["answer"] == "rag"
    assert support.calls == 0
    assert rag.calls == 1
    assert repository.executions == [(rag.conversation_id, ChatEngine.RAG)]
    assert rag.last_kwargs["retrieval_overrides"]["vector_top_k"] == 7


async def test_no_match_falls_back_and_links_traces() -> None:
    repository = FakeGatewayRepository()
    support = FakeSupport("NO_MATCH")
    rag = FakeRag()
    resolved = policy(ExecutionPolicy.SUPPORT_WITH_RAG_FALLBACK)
    service = ChatGatewayService(repository, support, rag)  # type: ignore[arg-type]

    response = await service.handle(policy=resolved, **kwargs(resolved.knowledge_id))

    assert response["answer"] == "rag"
    assert response["fallback"]["reason"] == "NO_MATCH"
    assert repository.links == [(support.trace_id, rag.trace_id, "NO_MATCH")]
    assert rag.last_kwargs["reuse_user_message_id"] == support.user_message_id
    assert rag.last_kwargs["reuse_assistant_message_id"] == support.assistant_message_id
    assert rag.last_kwargs["parent_trace_id"] == support.trace_id
    assert rag.last_kwargs["fallback_reason"] == "NO_MATCH"
    assert rag.last_kwargs["retrieval_overrides"]["fusion_top_k"] == 9
    assert repository.executions == [(support.conversation_id, ChatEngine.RAG)]


async def test_fallback_idempotency_replays_final_response_without_second_rag_call() -> None:
    repository = FakeGatewayRepository()
    support = FakeSupport("NO_MATCH")
    rag = FakeRag()
    resolved = policy(ExecutionPolicy.SUPPORT_WITH_RAG_FALLBACK)
    service = ChatGatewayService(repository, support, rag)  # type: ignore[arg-type]
    request = kwargs(resolved.knowledge_id)
    request["idempotency_key"] = "fallback-key"

    first = await service.handle(policy=resolved, **request)
    support.gateway_response = support.saved_gateway_responses[0]
    second = await service.handle(policy=resolved, **request)

    assert second == first
    assert rag.calls == 1
    assert repository.links == [(support.trace_id, rag.trace_id, "NO_MATCH")]


async def test_safety_result_never_falls_back() -> None:
    repository = FakeGatewayRepository()
    support = FakeSupport("ESCALATION_REQUIRED")
    rag = FakeRag()
    resolved = policy(ExecutionPolicy.SUPPORT_WITH_RAG_FALLBACK)
    service = ChatGatewayService(repository, support, rag)  # type: ignore[arg-type]

    response = await service.handle(policy=resolved, **kwargs(resolved.knowledge_id))

    assert response["response_type"] == "ESCALATION_REQUIRED"
    assert rag.calls == 0
    assert repository.links == []


async def test_hybrid_conversation_after_rag_fallback_returns_to_support() -> None:
    repository = FakeGatewayRepository()
    support = FakeSupport("ANSWERED")
    rag = FakeRag()
    resolved = policy(ExecutionPolicy.SUPPORT_WITH_RAG_FALLBACK, ConversationMode.SUPPORT_DISCOVERY)
    service = ChatGatewayService(repository, support, rag)  # type: ignore[arg-type]

    response = await service.handle(policy=resolved, **kwargs(resolved.knowledge_id))

    assert response["answer"] == "support"
    assert support.calls == 1
    assert rag.calls == 0


async def test_focused_support_never_uses_full_rag_as_a_topic_switch() -> None:
    repository = FakeGatewayRepository()
    support = FakeSupport("NO_MATCH")
    rag = FakeRag()
    resolved = policy(
        ExecutionPolicy.SUPPORT_WITH_RAG_FALLBACK,
        ConversationMode.FOCUSED_SUPPORT,
    )
    service = ChatGatewayService(repository, support, rag)  # type: ignore[arg-type]

    response = await service.handle(policy=resolved, **kwargs(resolved.knowledge_id))

    assert response["answer"] == "support"
    assert support.calls == 1
    assert rag.calls == 0


async def test_support_stream_exposes_engine_trace_and_message_events() -> None:
    repository = FakeGatewayRepository()
    support = FakeSupport("ANSWERED")
    resolved = policy(ExecutionPolicy.SUPPORT_WITH_RAG_FALLBACK)
    service = ChatGatewayService(repository, support, FakeRag())  # type: ignore[arg-type]

    frames = [
        frame.decode()
        async for frame in service.stream(policy=resolved, **kwargs(resolved.knowledge_id))
    ]

    assert '"stage":"gateway_routing","engine":"SUPPORT"' in frames[0]
    assert any('"stage":"support_processing","engine":"SUPPORT"' in frame for frame in frames)
    assert any(frame.startswith("event: trace") for frame in frames)
    assert any(frame.startswith("event: message") for frame in frames)
    done = next(frame for frame in frames if frame.startswith("event: done"))
    assert '"execution_engine":"SUPPORT"' in done


async def test_fallback_stream_exposes_transition_and_final_rag_metadata() -> None:
    repository = FakeGatewayRepository()
    support = FakeSupport("NO_MATCH")
    rag = FakeRag()
    resolved = policy(ExecutionPolicy.SUPPORT_WITH_RAG_FALLBACK)
    service = ChatGatewayService(repository, support, rag)  # type: ignore[arg-type]

    frames = [
        frame.decode()
        async for frame in service.stream(policy=resolved, **kwargs(resolved.knowledge_id))
    ]

    fallback = next(frame for frame in frames if frame.startswith("event: fallback"))
    assert '"from":"SUPPORT","to":"RAG","reason":"NO_MATCH"' in fallback
    assert any('"stage":"rag_processing","engine":"RAG"' in frame for frame in frames)
    done = next(frame for frame in frames if frame.startswith("event: done"))
    assert '"execution_engine":"RAG"' in done
    assert '"fallback":{"from":"SUPPORT","reason":"NO_MATCH"' in done
    assert repository.links == [(support.trace_id, rag.trace_id, "NO_MATCH")]
    assert rag.last_kwargs["reuse_user_message_id"] == support.user_message_id
    assert rag.last_kwargs["reuse_assistant_message_id"] == support.assistant_message_id
    assert rag.last_kwargs["parent_trace_id"] == support.trace_id
    assert rag.last_kwargs["fallback_reason"] == "NO_MATCH"


async def test_fallback_stream_idempotency_replays_done_without_second_rag_call() -> None:
    repository = FakeGatewayRepository()
    support = FakeSupport("NO_MATCH")
    rag = FakeRag()
    resolved = policy(ExecutionPolicy.SUPPORT_WITH_RAG_FALLBACK)
    service = ChatGatewayService(repository, support, rag)  # type: ignore[arg-type]
    request = kwargs(resolved.knowledge_id)
    request["idempotency_key"] = "fallback-stream-key"

    first_frames = [frame.decode() async for frame in service.stream(policy=resolved, **request)]
    first_done = next(frame for frame in first_frames if frame.startswith("event: done"))
    support.gateway_response = support.saved_gateway_responses[0]
    second_frames = [frame.decode() async for frame in service.stream(policy=resolved, **request)]
    second_done = next(frame for frame in second_frames if frame.startswith("event: done"))

    assert json.loads(second_done.splitlines()[1][6:]) == json.loads(first_done.splitlines()[1][6:])
    assert rag.calls == 1


class _RaisingRag(FakeRag):
    async def chat_stream(self, **kwargs: Any):  # type: ignore[no-untyped-def]
        raise ApiError("UPSTREAM_UNAVAILABLE", "查询向量生成失败", 503, {"inner": "detail"})
        yield b""  # pragma: no cover


async def test_stream_server_fault_collapses_to_busy_message() -> None:
    # The chat stream's SSE error event is end-user facing: a server fault
    # keeps its machine code but hides the technical message behind the
    # uniform busy copy (full detail stays in the trace).
    repository = FakeGatewayRepository()
    support = FakeSupport("ANSWERED")
    resolved = policy(ExecutionPolicy.RAG_ONLY)
    service = ChatGatewayService(repository, support, _RaisingRag())  # type: ignore[arg-type]

    frames = [
        frame async for frame in service.stream(policy=resolved, **kwargs(resolved.knowledge_id))
    ]

    error_frames = [frame for frame in frames if b"event: error" in frame]
    assert error_frames, frames
    payload = json.loads(error_frames[0].split(b"data: ", 1)[1].split(b"\n", 1)[0])
    assert payload["code"] == "UPSTREAM_UNAVAILABLE"
    assert payload["message"] == "服务繁忙，请稍后重试"
