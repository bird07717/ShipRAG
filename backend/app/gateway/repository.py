from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.common.errors import ApiError
from app.core.config import Settings
from app.gateway.models import ChatEngine, ConversationMode, ExecutionPolicy, ResolvedPolicy

_DEFAULT_FALLBACK_CONDITIONS = ("NO_MATCH",)


class GatewayRepository:
    def __init__(self, engine: AsyncEngine, settings: Settings) -> None:
        self.engine = engine
        self.settings = settings

    def _default_policy(self) -> ExecutionPolicy:
        if self.settings.chat_execution_mode == "legacy_rag":
            return ExecutionPolicy.RAG_ONLY
        # The normal operating mode starts with the curated Support workflow.
        # A NO_MATCH then continues in Generic RAG, instead of ending the
        # conversation prematurely when a Profile has no suitable match.
        return ExecutionPolicy.SUPPORT_WITH_RAG_FALLBACK

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
                                SELECT knowledge_id, execution_policy_snapshot,
                                       conversation_mode, engine_config_version
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
                if conversation["execution_policy_snapshot"]:
                    return ResolvedPolicy(
                        knowledge_id=knowledge_id,
                        policy=ExecutionPolicy(
                            str(conversation["execution_policy_snapshot"])
                        ),
                        config_version=int(conversation["engine_config_version"] or 0),
                        fallback_conditions=_DEFAULT_FALLBACK_CONDITIONS,
                        conversation_mode=(
                            ConversationMode(str(conversation["conversation_mode"]))
                            if conversation["conversation_mode"]
                            else None
                        ),
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
        readiness = await self.get_readiness(knowledge_id)
        return {
            "knowledge_id": resolved.knowledge_id,
            "execution_policy": resolved.policy,
            "config_version": resolved.config_version,
            "fallback_conditions": list(resolved.fallback_conditions),
            "source": "DATABASE" if resolved.config_version else "DEFAULT",
            "readiness": readiness,
        }

    async def get_readiness(self, knowledge_id: UUID) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            kb = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT kb.status,
                                   COALESCE(kb.rag_active_index_id, kb.active_index_id)
                                       AS rag_active_index_id,
                                   COALESCE(kb.rag_active_index_id, kb.active_index_id)
                                       AS support_active_index_id,
                                   ri.status AS rag_index_status,
                                   ri.embedding_model_name,
                                   si.status AS support_index_status,
                                   (SELECT count(*) FROM document_profile p
                                    WHERE p.kb_id = kb.id AND p.active_payload IS NOT NULL
                                      AND p.enabled) AS profile_count,
                                   (SELECT count(*)
                                    FROM document_profile p
                                    JOIN document_source d
                                      ON d.id = p.document_id AND d.kb_id = p.kb_id
                                    WHERE p.kb_id = kb.id AND p.active_payload IS NOT NULL
                                      AND p.enabled
                                      AND d.status = 'STORED'
                                      AND COALESCE(kb.rag_active_index_id, kb.active_index_id)
                                          IS NOT NULL
                                      AND EXISTS (
                                          SELECT 1 FROM document_element e
                                          WHERE e.index_id = COALESCE(
                                              kb.rag_active_index_id, kb.active_index_id
                                          )
                                            AND e.document_id = p.document_id
                                      )
                                      AND NOT EXISTS (
                                          SELECT 1 FROM document_element e
                                          LEFT JOIN image_asset a ON a.element_id = e.id
                                          WHERE e.index_id = COALESCE(
                                              kb.rag_active_index_id, kb.active_index_id
                                          )
                                            AND e.document_id = p.document_id
                                            AND e.element_type = 'IMAGE' AND a.id IS NULL
                                      )) AS ready_profile_count,
                                   EXISTS (
                                       SELECT 1 FROM prompt_template WHERE active
                                   ) AS prompt_ready
                            FROM knowledge_base kb
                            LEFT JOIN knowledge_index ri ON ri.id = COALESCE(
                                kb.rag_active_index_id, kb.active_index_id
                            )
                            LEFT JOIN knowledge_index si ON si.id = COALESCE(
                                kb.rag_active_index_id, kb.active_index_id
                            )
                            WHERE kb.id = :kb_id
                            """
                        ),
                        {"kb_id": knowledge_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if kb is None:
                raise ApiError("KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在", 404)
            model_rows = (
                await connection.execute(
                    text(
                        """
                        SELECT model_type, provider, model_name,
                               api_key_ciphertext IS NOT NULL AS stored_key_configured
                        FROM model_config
                        WHERE enabled AND model_type IN ('LLM', 'RERANK')
                        """
                    )
                )
            ).mappings()

        models = {str(row["model_type"]): dict(row) for row in model_rows}
        support_blockers: list[str] = []
        rag_blockers: list[str] = []
        if kb["status"] != "ENABLED":
            support_blockers.append("知识库已停用")
            rag_blockers.append("知识库已停用")
        if int(kb["ready_profile_count"] or 0) == 0:
            support_blockers.append("没有已发布就绪的 Problem Profile")
        if kb["support_active_index_id"] is None:
            support_blockers.append("知识库没有 Active Index")
        elif kb["support_index_status"] != "ACTIVE":
            support_blockers.append("Active Index 状态异常")

        llm = models.get("LLM")
        if llm is None:
            support_blockers.append("LLM 模型未启用")
            rag_blockers.append("LLM 模型未启用")
        elif str(llm["provider"]) != "fake" and not (
            bool(llm["stored_key_configured"])
            or self.settings.has_secret(self.settings.zhipu_api_key)
        ):
            support_blockers.append("LLM API Key 未配置")
            rag_blockers.append("LLM API Key 未配置")

        if kb["rag_active_index_id"] is None:
            rag_blockers.append("知识库没有 Active Index")
        elif kb["rag_index_status"] != "ACTIVE":
            rag_blockers.append("Active Index 状态异常")
        if not kb["prompt_ready"]:
            rag_blockers.append("Active Prompt 未配置")

        rerank = models.get("RERANK")
        if rerank is None:
            rag_blockers.append("Rerank 模型未启用")
        elif str(rerank["provider"]) != "fake" and not (
            bool(rerank["stored_key_configured"])
            or self.settings.has_secret(self.settings.siliconflow_api_key)
        ):
            rag_blockers.append("Rerank API Key 未配置")

        embedding_uses_fake = self.settings.m2_embedding_provider == "fake" or (
            self.settings.m2_embedding_provider == "auto"
            and not self.settings.has_secret(self.settings.siliconflow_api_key)
        )
        query_embedding_model = (
            "deterministic-shake256"
            if embedding_uses_fake
            else self.settings.siliconflow_embedding_model
        )
        if (
            kb["rag_active_index_id"] is not None
            and str(kb["embedding_model_name"]) != query_embedding_model
        ):
            rag_blockers.append("查询 Embedding 模型与 Active Index 快照不一致")

        return {
            "support": {
                "ready": not support_blockers,
                "blockers": support_blockers,
                "profile_count": int(kb["profile_count"] or 0),
                "ready_profile_count": int(kb["ready_profile_count"] or 0),
            },
            "rag": {"ready": not rag_blockers, "blockers": rag_blockers},
        }

    @staticmethod
    def _policy_blockers(execution_policy: ExecutionPolicy, readiness: dict[str, Any]) -> list[str]:
        blockers: list[str] = []
        if execution_policy != ExecutionPolicy.RAG_ONLY:
            blockers.extend(str(item) for item in readiness["support"]["blockers"])
        blockers.extend(str(item) for item in readiness["rag"]["blockers"])
        return list(dict.fromkeys(blockers))

    async def update_policy(
        self, knowledge_id: UUID, execution_policy: ExecutionPolicy
    ) -> dict[str, Any]:
        readiness = await self.get_readiness(knowledge_id)
        blockers = self._policy_blockers(execution_policy, readiness)
        if blockers:
            raise ApiError(
                "ENGINE_POLICY_NOT_READY",
                "问答引擎尚未满足运行条件",
                409,
                {"blockers": blockers},
            )
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
            "readiness": readiness,
        }

    async def record_conversation_execution(
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
                    SET execution_policy_snapshot = COALESCE(execution_policy_snapshot, :policy),
                        last_execution_engine = :engine,
                        engine_config_version = COALESCE(engine_config_version, :version),
                        conversation_mode = CASE
                            WHEN :policy = 'RAG_ONLY' THEN 'RAG_ONLY'
                            WHEN focus_document_id IS NOT NULL THEN 'FOCUSED_SUPPORT'
                            ELSE 'SUPPORT_DISCOVERY'
                        END,
                        updated_at = now()
                    WHERE id = :id
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
                raise ApiError("CONVERSATION_NOT_FOUND", "会话不存在", 404)

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
                    UPDATE rag_trace SET message_id = NULL
                    WHERE trace_id = :parent_trace_id AND engine = 'SUPPORT'
                    """
                ),
                {"parent_trace_id": support_trace_id},
            )
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
