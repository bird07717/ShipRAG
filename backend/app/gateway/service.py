from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any
from uuid import UUID

from app.common.errors import ApiError, user_safe_chat_message
from app.gateway.models import ChatEngine, ResolvedPolicy
from app.gateway.repository import GatewayRepository
from app.rag.models import RetrievalConfig
from app.services.rag import RagService
from app.support.service import TechnicalSupportService


def _sse(event: str, payload: dict[str, Any]) -> bytes:
    return (
        f"event: {event}\ndata: "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "\n\n"
    ).encode()


def _event_payload(frame: bytes, event_name: str) -> dict[str, Any] | None:
    text = frame.decode("utf-8", errors="replace")
    if f"event: {event_name}\n" not in text:
        return None
    for line in text.splitlines():
        if line.startswith("data: "):
            try:
                value = json.loads(line[6:])
            except json.JSONDecodeError:
                return None
            return value if isinstance(value, dict) else None
    return None


class ChatGatewayService:
    def __init__(
        self,
        repository: GatewayRepository,
        support: TechnicalSupportService,
        rag: RagService,
    ) -> None:
        self.repository = repository
        self.support = support
        self.rag = rag

    async def handle(
        self,
        *,
        policy: ResolvedPolicy,
        knowledge_id: UUID,
        conversation_id: UUID | None,
        question: str,
        request_id: str,
        user_id: str | None,
        problem_code: str | None,
        document_id: UUID | None,
        choice_id: str | None,
        include_images: bool,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        if policy.primary_engine == ChatEngine.RAG:
            response = await self._rag_generate(
                knowledge_id=knowledge_id,
                conversation_id=conversation_id,
                question=question,
                request_id=request_id,
                user_id=user_id,
                include_images=include_images,
                retrieval_overrides=await self._retrieval_overrides(policy),
            )
            await self._record_execution(response, policy, ChatEngine.RAG)
            response["execution_engine"] = ChatEngine.RAG.value
            return response

        support_response = await self.support.handle(
            knowledge_id=knowledge_id,
            conversation_id=conversation_id,
            question=question,
            request_id=request_id,
            user_id=user_id,
            problem_code=problem_code,
            document_id=document_id,
            choice_id=choice_id,
            include_images=include_images,
            idempotency_key=idempotency_key,
            return_internal_ids=True,
            retrieval_config=await self._retrieval_config(policy),
        )
        response_type = str(support_response.get("response_type", ""))
        replayed_gateway = support_response.pop("_gateway_response", None)
        message_pair = support_response.pop("_fallback_message_pair", {})
        if isinstance(replayed_gateway, dict):
            return dict(replayed_gateway)
        if not policy.allows_fallback(response_type):
            await self._record_execution(support_response, policy, ChatEngine.SUPPORT)
            support_response["execution_engine"] = ChatEngine.SUPPORT.value
            return support_response

        rag_response = await self._rag_generate(
            knowledge_id=knowledge_id,
            conversation_id=UUID(str(support_response["conversation_id"])),
            question=question,
            request_id=request_id,
            user_id=user_id,
            reuse_user_message_id=(
                UUID(str(message_pair["user_message_id"]))
                if message_pair.get("user_message_id")
                else None
            ),
            reuse_assistant_message_id=(
                UUID(str(message_pair["assistant_message_id"]))
                if message_pair.get("assistant_message_id")
                else None
            ),
            parent_trace_id=UUID(str(support_response["trace_id"])),
            fallback_reason=response_type,
            include_images=include_images,
            retrieval_overrides=await self._retrieval_overrides(policy),
        )
        await self.repository.link_fallback(
            support_trace_id=UUID(str(support_response["trace_id"])),
            rag_trace_id=UUID(str(rag_response["trace_id"])),
            reason=response_type,
        )
        await self._record_execution(rag_response, policy, ChatEngine.RAG)
        rag_response["fallback"] = {
            "from": "SUPPORT",
            "reason": response_type,
            "support_trace_id": str(support_response["trace_id"]),
        }
        rag_response["execution_engine"] = ChatEngine.RAG.value
        if idempotency_key is not None:
            await self.support.save_gateway_response(
                knowledge_id=knowledge_id,
                idempotency_key=idempotency_key,
                support_trace_id=UUID(str(support_response["trace_id"])),
                response=rag_response,
            )
        return rag_response

    async def stream(self, **kwargs: Any) -> AsyncGenerator[bytes, None]:
        policy: ResolvedPolicy = kwargs.pop("policy")
        yield _sse(
            "status",
            {"stage": "gateway_routing", "engine": policy.primary_engine.value},
        )
        try:
            if policy.primary_engine == ChatEngine.RAG:
                async for frame in self._rag_stream(policy=policy, **kwargs):
                    yield frame
                return

            yield _sse("status", {"stage": "support_processing", "engine": "SUPPORT"})
            support_response = await self.support.handle(
                **kwargs,
                return_internal_ids=True,
                retrieval_config=await self._retrieval_config(policy),
            )
            response_type = str(support_response.get("response_type", ""))
            replayed_gateway = support_response.pop("_gateway_response", None)
            message_pair = support_response.pop("_fallback_message_pair", {})
            if isinstance(replayed_gateway, dict):
                fallback = replayed_gateway.get("fallback")
                if isinstance(fallback, dict):
                    yield _sse(
                        "fallback",
                        {
                            "from": fallback.get("from", "SUPPORT"),
                            "to": replayed_gateway.get("execution_engine", "RAG"),
                            "reason": fallback.get("reason", "NO_MATCH"),
                            "support_trace_id": fallback.get("support_trace_id"),
                        },
                    )
                yield _sse(
                    "trace",
                    {
                        "trace_id": replayed_gateway["trace_id"],
                        "conversation_id": replayed_gateway["conversation_id"],
                    },
                )
                answer = str(replayed_gateway.get("answer", ""))
                for offset in range(0, len(answer), 24):
                    yield _sse("message", {"delta": answer[offset : offset + 24]})
                    await asyncio.sleep(0)
                yield _sse("done", dict(replayed_gateway))
                return
            if not policy.allows_fallback(response_type):
                await self._record_execution(support_response, policy, ChatEngine.SUPPORT)
                support_response["execution_engine"] = ChatEngine.SUPPORT.value
                yield _sse(
                    "trace",
                    {
                        "trace_id": support_response["trace_id"],
                        "conversation_id": support_response["conversation_id"],
                    },
                )
                answer = str(support_response.get("answer", ""))
                for offset in range(0, len(answer), 24):
                    yield _sse("message", {"delta": answer[offset : offset + 24]})
                    await asyncio.sleep(0)
                yield _sse("done", support_response)
                return

            yield _sse(
                "fallback",
                {
                    "from": "SUPPORT",
                    "to": "RAG",
                    "reason": response_type,
                    "support_trace_id": str(support_response["trace_id"]),
                },
            )
            yield _sse("status", {"stage": "rag_processing", "engine": "RAG"})
            async for frame in self._rag_stream(
                policy=policy,
                parent_trace_id=UUID(str(support_response["trace_id"])),
                fallback_reason=response_type,
                knowledge_id=kwargs["knowledge_id"],
                conversation_id=UUID(str(support_response["conversation_id"])),
                question=kwargs["question"],
                request_id=kwargs["request_id"],
                user_id=kwargs.get("user_id"),
                reuse_user_message_id=message_pair.get("user_message_id"),
                reuse_assistant_message_id=message_pair.get("assistant_message_id"),
                fallback_idempotency_key=kwargs.get("idempotency_key"),
                include_images=kwargs.get("include_images", True),
            ):
                yield frame
        except ApiError as exc:
            yield _sse(
                "error",
                {
                    "code": exc.code,
                    # End-user facing: server faults collapse to the busy
                    # message; full detail stays in the trace.
                    "message": user_safe_chat_message(exc),
                    "status_code": exc.status_code,
                },
            )

    async def _rag_stream(
        self,
        *,
        policy: ResolvedPolicy,
        knowledge_id: UUID,
        conversation_id: UUID | None,
        question: str,
        request_id: str,
        user_id: str | None,
        reuse_user_message_id: str | UUID | None = None,
        reuse_assistant_message_id: str | UUID | None = None,
        parent_trace_id: UUID | None = None,
        fallback_reason: str | None = None,
        fallback_idempotency_key: str | None = None,
        include_images: bool = True,
        **_unused: Any,
    ) -> AsyncIterator[bytes]:
        async for frame in self.rag.chat_stream(
            knowledge_id=knowledge_id,
            conversation_id=conversation_id,
            question=question,
            request_id=request_id,
            user_id=user_id,
            include_images=include_images,
            reuse_user_message_id=(
                UUID(str(reuse_user_message_id)) if reuse_user_message_id else None
            ),
            reuse_assistant_message_id=(
                UUID(str(reuse_assistant_message_id)) if reuse_assistant_message_id else None
            ),
            parent_trace_id=parent_trace_id,
            fallback_reason=fallback_reason,
            retrieval_overrides=await self._retrieval_overrides(policy),
        ):
            done = _event_payload(frame, "done")
            if done is not None:
                done["execution_engine"] = ChatEngine.RAG.value
                if parent_trace_id is not None and done.get("trace_id"):
                    await self.repository.link_fallback(
                        support_trace_id=parent_trace_id,
                        rag_trace_id=UUID(str(done["trace_id"])),
                        reason=fallback_reason or "NO_MATCH",
                    )
                    done["fallback"] = {
                        "from": "SUPPORT",
                        "reason": fallback_reason or "NO_MATCH",
                        "support_trace_id": str(parent_trace_id),
                    }
                await self._record_execution(done, policy, ChatEngine.RAG)
                if parent_trace_id is not None and fallback_idempotency_key is not None:
                    await self.support.save_gateway_response(
                        knowledge_id=knowledge_id,
                        idempotency_key=fallback_idempotency_key,
                        support_trace_id=parent_trace_id,
                        response=done,
                    )
                yield _sse("done", done)
            else:
                yield frame

    async def _rag_generate(self, **kwargs: Any) -> dict[str, Any]:
        return await self.rag.generate(**kwargs)

    async def _retrieval_config(self, policy: ResolvedPolicy) -> RetrievalConfig:
        return await self.rag.repository.get_engine_retrieval_config(policy.policy.value)

    async def _retrieval_overrides(self, policy: ResolvedPolicy) -> dict[str, int]:
        config = await self._retrieval_config(policy)
        return {
            "vector_top_k": config.vector_top_k,
            "bm25_top_k": config.bm25_top_k,
            "fusion_top_k": config.fusion_top_k,
            "rerank_top_n": config.rerank_top_n,
            "context_max_chunks": config.context_max_chunks,
        }

    async def _record_execution(
        self, response: dict[str, Any], policy: ResolvedPolicy, engine: ChatEngine
    ) -> None:
        await self.repository.record_conversation_execution(
            conversation_id=UUID(str(response["conversation_id"])),
            policy=policy,
            engine=engine,
        )
