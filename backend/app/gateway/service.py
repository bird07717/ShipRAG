from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from app.common.errors import ApiError
from app.gateway.models import ChatEngine, ResolvedPolicy
from app.gateway.repository import GatewayRepository
from app.services.rag import RagService
from app.support.service import TechnicalSupportService

_PRODUCT_SCOPE = "本产品相关的问题，包括参数、功能、使用步骤、维护和故障排查"


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
            )
            await self._freeze(response, policy, ChatEngine.RAG)
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
        )
        response_type = str(support_response.get("response_type", ""))
        if not policy.allows_fallback(response_type):
            await self._freeze(support_response, policy, ChatEngine.SUPPORT)
            support_response["execution_engine"] = ChatEngine.SUPPORT.value
            return support_response

        rag_response = await self._rag_generate(
            knowledge_id=knowledge_id,
            conversation_id=UUID(str(support_response["conversation_id"])),
            question=question,
            request_id=request_id,
            user_id=user_id,
        )
        await self.repository.link_fallback(
            support_trace_id=UUID(str(support_response["trace_id"])),
            rag_trace_id=UUID(str(rag_response["trace_id"])),
            reason=response_type,
        )
        await self._freeze(rag_response, policy, ChatEngine.RAG)
        rag_response["fallback"] = {
            "from": "SUPPORT",
            "reason": response_type,
            "support_trace_id": str(support_response["trace_id"]),
        }
        rag_response["execution_engine"] = ChatEngine.RAG.value
        return rag_response

    async def stream(self, **kwargs: Any) -> AsyncIterator[bytes]:
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
            support_response = await self.support.handle(**kwargs)
            response_type = str(support_response.get("response_type", ""))
            if not policy.allows_fallback(response_type):
                await self._freeze(support_response, policy, ChatEngine.SUPPORT)
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
            ):
                yield frame
        except ApiError as exc:
            yield _sse("error", {"code": exc.code, "message": exc.message})
        except Exception:
            yield _sse("error", {"code": "INTERNAL_ERROR", "message": "问答网关处理失败"})

    async def _rag_stream(
        self,
        *,
        policy: ResolvedPolicy,
        knowledge_id: UUID,
        conversation_id: UUID | None,
        question: str,
        request_id: str,
        user_id: str | None,
        parent_trace_id: UUID | None = None,
        fallback_reason: str | None = None,
        **_unused: Any,
    ) -> AsyncIterator[bytes]:
        async for frame in self.rag.chat_stream(
            knowledge_id=knowledge_id,
            conversation_id=conversation_id,
            question=question,
            request_id=request_id,
            product_name="",
            scope_description=_PRODUCT_SCOPE,
            user_id=user_id,
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
                await self._freeze(done, policy, ChatEngine.RAG)
                yield _sse("done", done)
            else:
                yield frame

    async def _rag_generate(self, **kwargs: Any) -> dict[str, Any]:
        return await self.rag.generate(
            **kwargs,
            product_name="",
            scope_description=_PRODUCT_SCOPE,
        )

    async def _freeze(
        self, response: dict[str, Any], policy: ResolvedPolicy, engine: ChatEngine
    ) -> None:
        await self.repository.freeze_conversation(
            conversation_id=UUID(str(response["conversation_id"])),
            policy=policy,
            engine=engine,
        )
