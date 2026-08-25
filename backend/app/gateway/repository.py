from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.common.errors import ApiError
from app.core.config import Settings
from app.gateway.models import ChatEngine, ExecutionPolicy, ResolvedPolicy

_DEFAULT_FALLBACK_CONDITIONS = ("NO_MATCH",)


class GatewayRepository:
    def __init__(self, engine: AsyncEngine, settings: Settings) -> None:
        self.engine = engine
        self.settings = settings

    def _default_policy(self) -> ExecutionPolicy:
        if self.settings.chat_execution_mode == "legacy_rag":
            return ExecutionPolicy.RAG_ONLY
        return ExecutionPolicy.SUPPORT_ONLY

    async def resolve_policy(
        self, *, knowledge_id: UUID, conversation_id: UUID | None
    ) -> ResolvedPolicy:
        async with self.engine.connect() as connection:
            if conversation_id is not None:
                conversation = (
                    (
                        await connection.execute(
                            text(
                                """
                                SELECT knowledge_id, execution_policy, execution_engine,
                                       engine_config_version
                                FROM conversation WHERE id = :id
                                """
                            ),
                            {"id": conversation_id},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if conversation is None:
                    raise ApiError("CONVERSATION_NOT_FOUND", "会话不存在", 404)
                if conversation["knowledge_id"] != knowledge_id:
                    raise ApiError("CONVERSATION_KB_MISMATCH", "会话不属于指定知识库", 409)
                if conversation["execution_engine"]:
                    return ResolvedPolicy(
                        knowledge_id=knowledge_id,
                        policy=ExecutionPolicy(
                            str(conversation["execution_policy"] or self._default_policy())
                        ),
                        config_version=int(conversation["engine_config_version"] or 0),
                        fallback_conditions=_DEFAULT_FALLBACK_CONDITIONS,
                        frozen_engine=ChatEngine(str(conversation["execution_engine"])),
                    )

            policy = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT execution_policy, config_version, fallback_conditions
                            FROM engine_runtime_policy WHERE kb_id = :kb_id
                            """
                        ),
                        {"kb_id": knowledge_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if policy is None:
                exists = await connection.execute(
                    text("SELECT 1 FROM knowledge_base WHERE id = :kb_id"),
                    {"kb_id": knowledge_id},
                )
                if exists.scalar_one_or_none() is None:
                    raise ApiError("KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在", 404)
                return ResolvedPolicy(
                    knowledge_id=knowledge_id,
                    policy=self._default_policy(),
                    config_version=0,
                    fallback_conditions=_DEFAULT_FALLBACK_CONDITIONS,
                )
            raw_conditions = policy["fallback_conditions"]
            conditions = (
                tuple(str(value) for value in raw_conditions)
                if isinstance(raw_conditions, list)
                else _DEFAULT_FALLBACK_CONDITIONS
            )
            return ResolvedPolicy(
                knowledge_id=knowledge_id,
                policy=ExecutionPolicy(str(policy["execution_policy"])),
                config_version=int(policy["config_version"]),
                fallback_conditions=conditions,
            )

    async def get_policy(self, knowledge_id: UUID) -> dict[str, Any]:
        resolved = await self.resolve_policy(knowledge_id=knowledge_id, conversation_id=None)
        return {
            "knowledge_id": resolved.knowledge_id,
            "execution_policy": resolved.policy,
            "config_version": resolved.config_version,
            "fallback_conditions": list(resolved.fallback_conditions),
            "source": "DATABASE" if resolved.config_version else "DEFAULT",
        }

    async def update_policy(
        self, knowledge_id: UUID, execution_policy: ExecutionPolicy
    ) -> dict[str, Any]:
        async with self.engine.begin() as connection:
            exists = await connection.execute(
                text("SELECT 1 FROM knowledge_base WHERE id = :kb_id FOR UPDATE"),
                {"kb_id": knowledge_id},
            )
            if exists.scalar_one_or_none() is None:
                raise ApiError("KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在", 404)
            row = (
                (
                    await connection.execute(
                        text(
                            """
                            INSERT INTO engine_runtime_policy (
                                id, kb_id, execution_policy, fallback_conditions
                            ) VALUES (
                                :id, :kb_id, :policy, '["NO_MATCH"]'::jsonb
                            )
                            ON CONFLICT (kb_id) DO UPDATE
                            SET execution_policy = EXCLUDED.execution_policy,
                                fallback_conditions = EXCLUDED.fallback_conditions,
                                config_version = engine_runtime_policy.config_version + 1,
                                updated_at = now()
                            RETURNING execution_policy, config_version,
                                      fallback_conditions, updated_at
                            """
                        ),
                        {"id": uuid4(), "kb_id": knowledge_id, "policy": execution_policy.value},
                    )
                )
                .mappings()
                .one()
            )
        return {
            "knowledge_id": knowledge_id,
            "execution_policy": row["execution_policy"],
            "config_version": row["config_version"],
            "fallback_conditions": list(row["fallback_conditions"] or []),
            "source": "DATABASE",
            "updated_at": row["updated_at"],
        }

    async def freeze_conversation(
        self,
        *,
        conversation_id: UUID,
        policy: ResolvedPolicy,
        engine: ChatEngine,
    ) -> None:
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE conversation
                    SET execution_policy = COALESCE(execution_policy, :policy),
                        execution_engine = COALESCE(execution_engine, :engine),
                        engine_config_version = COALESCE(engine_config_version, :version),
                        updated_at = now()
                    WHERE id = :id
                      AND (execution_engine IS NULL OR execution_engine = :engine)
                    """
                ),
                {
                    "id": conversation_id,
                    "policy": policy.policy.value,
                    "engine": engine.value,
                    "version": policy.config_version,
                },
            )
            if result.rowcount != 1:
                raise ApiError("ENGINE_POLICY_CONFLICT", "会话已冻结到另一问答引擎", 409)

    async def link_fallback(
        self,
        *,
        support_trace_id: UUID,
        rag_trace_id: UUID,
        reason: str,
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE rag_trace
                    SET parent_trace_id = :parent_trace_id,
                        fallback_from = 'SUPPORT',
                        fallback_reason = :reason
                    WHERE trace_id = :rag_trace_id AND engine = 'RAG'
                    """
                ),
                {
                    "parent_trace_id": support_trace_id,
                    "rag_trace_id": rag_trace_id,
                    "reason": reason,
                },
            )
