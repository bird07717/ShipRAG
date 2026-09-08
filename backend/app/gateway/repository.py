from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.common.errors import ApiError
from app.core.config import Settings
from app.gateway.models import ChatEngine, ConversationMode, ExecutionPolicy, ResolvedPolicy
from app.rag.model_providers import environment_api_key

_DEFAULT_FALLBACK_CONDITIONS = ("NO_MATCH",)


class GatewayRepository:
    def __init__(self, engine: AsyncEngine, settings: Settings) -> None:
        self.engine = engine
        self.settings = settings

    def _default_policy(self) -> ExecutionPolicy:
        if self.settings.chat_execution_mode == "legacy_rag":
            return ExecutionPolicy.RAG_ONLY
        # Compatibility mode: the curated Support workflow handles the turn
        # first and a NO_MATCH continues in Generic RAG.
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
                        policy=ExecutionPolicy(str(conversation["execution_policy_snapshot"])),
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
                        WHERE enabled AND model_type IN ('LLM', 'RERANK', 'EMBEDDING')
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
            or self.settings.has_secret(
                environment_api_key(self.settings, "LLM", str(llm["provider"]))
            )
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
            or self.settings.has_secret(
                environment_api_key(self.settings, "RERANK", str(rerank["provider"]))
            )
        ):
            rag_blockers.append("Rerank API Key 未配置")

        embedding = models.get("EMBEDDING")
        if embedding is None:
            rag_blockers.append("Embedding 模型未启用")
            query_embedding_model = ""
        else:
            embedding_uses_fake = self.settings.m2_embedding_provider == "fake" or (
                self.settings.m2_embedding_provider == "auto"
                and not (
                    bool(embedding["stored_key_configured"])
                    or self.settings.has_secret(
                        environment_api_key(self.settings, "EMBEDDING", str(embedding["provider"]))
                    )
                )
            )
            query_embedding_model = (
                "deterministic-shake256" if embedding_uses_fake else str(embedding["model_name"])
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

    async def list_conversations(
        self, knowledge_id: UUID, user_id: str | None, limit: int
    ) -> list[dict[str, Any]]:
        """List conversations for platform-side history, newest activity first.

        ``user_id`` is the caller-side isolation key; when provided only
        conversations owned by that user are returned.  Anonymous rows
        (DemoChat, legacy) are visible only without the filter.
        """
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, knowledge_id, user_id, title, last_execution_engine,
                           conversation_mode, created_at, updated_at,
                           (SELECT count(*) FROM message m
                            WHERE m.conversation_id = conversation.id
                              AND m.status = 'COMPLETED') AS message_count
                    FROM conversation
                    WHERE knowledge_id = :knowledge_id
                      AND (CAST(:user_id AS varchar) IS NULL OR user_id = :user_id)
                    ORDER BY updated_at DESC
                    LIMIT :limit
                    """
                ),
                {"knowledge_id": knowledge_id, "user_id": user_id, "limit": limit},
            )
            return [dict(row) for row in result.mappings()]

    async def get_conversation(self, conversation_id: UUID) -> dict[str, Any] | None:
        """Fetch one conversation row for ownership checks and detail views."""
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, knowledge_id, user_id, title, last_execution_engine,
                           conversation_mode, rag_index_id, support_index_id,
                           focus_index_id, focus_document_id, focus_epoch,
                           created_at, updated_at,
                           (SELECT count(*) FROM message m
                            WHERE m.conversation_id = conversation.id
                              AND m.status = 'COMPLETED') AS message_count
                    FROM conversation
                    WHERE id = :conversation_id
                    """
                ),
                {"conversation_id": conversation_id},
            )
            row = result.mappings().one_or_none()
            return dict(row) if row is not None else None

    async def list_conversation_messages(
        self, conversation_id: UUID, limit: int
    ) -> list[dict[str, Any]]:
        """Return the newest finished messages in chronological order.

        STREAMING rows (interrupted turns) are never exposed; FAILED turns
        are included so the platform can render what the user actually saw.
        """
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, conversation_id, role, content, sources, status,
                           content_blocks, response_type, support_mode,
                           created_at, updated_at
                    FROM (
                        SELECT id, conversation_id, role, content, sources, status,
                               content_blocks, response_type, support_mode,
                               created_at, updated_at, sequence_no
                        FROM message
                        WHERE conversation_id = :conversation_id
                          AND status IN ('COMPLETED', 'FAILED')
                        ORDER BY sequence_no DESC
                        LIMIT :limit
                    ) recent
                    ORDER BY sequence_no
                    """
                ),
                {"conversation_id": conversation_id, "limit": limit},
            )
            return [dict(row) for row in result.mappings()]

    async def delete_conversation(self, conversation_id: UUID) -> bool:
        """Delete a conversation row; messages cascade and traces survive.

        rag_trace rows keep their payload with ``conversation_id`` set to
        NULL (ON DELETE SET NULL), so observability outlives user deletions
        and the frozen index references die with the row.
        """
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text("DELETE FROM conversation WHERE id = :id RETURNING id"),
                {"id": conversation_id},
            )
            return result.scalar_one_or_none() is not None

    # External Assistant API -------------------------------------------------

    async def list_assistant_knowledge_bases(self) -> list[dict[str, Any]]:
        """Return only enabled knowledge bases with a usable RAG index."""
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT kb.id AS knowledge_id, kb.name, kb.description,
                           (kb.status = 'ENABLED' AND i.status = 'ACTIVE') AS ready
                    FROM knowledge_base kb
                    LEFT JOIN knowledge_index i
                      ON i.id = COALESCE(kb.rag_active_index_id, kb.active_index_id)
                    WHERE kb.status = 'ENABLED'
                    ORDER BY kb.name, kb.id
                    """
                )
            )
            return [dict(row) for row in result.mappings()]

    async def get_assistant_conversation(
        self, *, conversation_id: UUID, external_user_id: str, knowledge_id: UUID | None = None
    ) -> dict[str, Any] | None:
        """Return one visible, unexpired conversation without leaking ownership."""
        retention_clause = (
            "AND c.last_activity_at >= now() - INTERVAL '1 day' * :retention_days"
            if self.settings.conversation_retention_days > 0
            else ""
        )
        knowledge_clause = "AND c.knowledge_id = :knowledge_id" if knowledge_id else ""
        params: dict[str, Any] = {
            "conversation_id": conversation_id,
            "external_user_id": external_user_id,
            "retention_days": self.settings.conversation_retention_days,
        }
        if knowledge_id is not None:
            params["knowledge_id"] = knowledge_id
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    f"""
                    SELECT c.id AS conversation_id, c.knowledge_id, kb.name AS knowledge_name,
                           c.user_id, c.title, c.created_at, c.updated_at,
                           c.last_activity_at,
                           (SELECT count(*) FROM message m WHERE m.conversation_id = c.id)
                               AS message_count
                    FROM conversation c
                    JOIN knowledge_base kb ON kb.id = c.knowledge_id
                    WHERE c.id = :conversation_id
                      AND c.user_id = :external_user_id
                      {knowledge_clause}
                      {retention_clause}
                    """
                ),
                params,
            )
            row = result.mappings().one_or_none()
            return dict(row) if row is not None else None

    async def list_assistant_conversations(
        self,
        *,
        knowledge_id: UUID,
        external_user_id: str,
        limit: int,
        before_activity: datetime | None = None,
        before_id: UUID | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["c.knowledge_id = :knowledge_id", "c.user_id = :external_user_id"]
        if self.settings.conversation_retention_days > 0:
            clauses.append("c.last_activity_at >= now() - INTERVAL '1 day' * :retention_days")
        params: dict[str, Any] = {
            "knowledge_id": knowledge_id,
            "external_user_id": external_user_id,
            "retention_days": self.settings.conversation_retention_days,
            "limit": limit,
        }
        if before_activity is not None and before_id is not None:
            clauses.append("(c.last_activity_at, c.id) < (:before_activity, :before_id)")
            params["before_activity"] = before_activity
            params["before_id"] = before_id
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    f"""
                    SELECT c.id AS conversation_id, c.knowledge_id, kb.name AS knowledge_name,
                           c.title, c.created_at, c.last_activity_at,
                           (SELECT count(*) FROM message m WHERE m.conversation_id = c.id)
                               AS message_count
                    FROM conversation c
                    JOIN knowledge_base kb ON kb.id = c.knowledge_id
                    WHERE {" AND ".join(clauses)}
                    ORDER BY c.last_activity_at DESC, c.id DESC
                    LIMIT :limit
                    """
                ),
                params,
            )
            return [dict(row) for row in result.mappings()]

    async def list_assistant_messages(
        self,
        *,
        conversation_id: UUID,
        limit: int,
        before_sequence: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["m.conversation_id = :conversation_id"]
        params: dict[str, Any] = {"conversation_id": conversation_id, "limit": limit}
        if before_sequence is not None:
            clauses.append("m.sequence_no < :before_sequence")
            params["before_sequence"] = before_sequence
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    f"""
                    SELECT id, conversation_id, sequence_no, role, content, status,
                           response_type, content_blocks, response_payload,
                           created_at, updated_at,
                           (SELECT trace_id FROM rag_trace t
                            WHERE t.message_id = m.id
                            ORDER BY t.created_at DESC LIMIT 1) AS trace_id,
                           (SELECT error FROM rag_trace t
                            WHERE t.message_id = m.id
                            ORDER BY t.created_at DESC LIMIT 1) AS trace_error
                    FROM (
                        SELECT * FROM message m
                        WHERE {" AND ".join(clauses)}
                        ORDER BY m.sequence_no DESC
                        LIMIT :limit
                    ) m
                    ORDER BY sequence_no
                    """
                ),
                params,
            )
            return [dict(row) for row in result.mappings()]

    async def assistant_turn_context(
        self, *, conversation_id: UUID, trace_id: UUID
    ) -> dict[str, Any]:
        """Resolve one turn's public identifiers from an internal trace."""
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT t.conversation_id, t.message_id AS assistant_message_id,
                           (SELECT m.id FROM message m
                            WHERE m.conversation_id = t.conversation_id
                              AND m.role = 'USER' AND m.sequence_no < a.sequence_no
                            ORDER BY m.sequence_no DESC LIMIT 1) AS user_message_id,
                           t.trace_id
                    FROM rag_trace t
                    JOIN message a ON a.id = t.message_id
                    WHERE t.trace_id = :trace_id AND t.conversation_id = :conversation_id
                    """
                ),
                {"trace_id": trace_id, "conversation_id": conversation_id},
            )
            row = result.mappings().one_or_none()
            return dict(row) if row is not None else {}

    async def mark_assistant_trace_runtime(
        self, *, request_id: str, policy: ResolvedPolicy
    ) -> None:
        """Mark all traces in an external request, including Support→RAG fallback pairs."""
        snapshot = {
            "execution": {
                "policy": policy.policy.value,
                "policy_config_version": policy.config_version,
                "fallback_conditions": list(policy.fallback_conditions),
            }
        }
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE rag_trace
                    SET source = 'ASSISTANT_API',
                        runtime_snapshot = COALESCE(runtime_snapshot, '{}'::jsonb)
                            || CAST(:snapshot AS jsonb)
                    WHERE request_id = :request_id
                    """
                ),
                {"request_id": request_id, "snapshot": json.dumps(snapshot)},
            )

    async def reserve_assistant_request(
        self,
        *,
        external_user_id: str,
        knowledge_id: UUID,
        idempotency_key: str,
        request_hash: str,
        conversation_id: UUID | None,
    ) -> tuple[str, dict[str, Any]]:
        """Atomically reserve an external request or return its durable replay."""
        request_id = uuid4()
        try:
            async with self.engine.begin() as connection:
                existing = (
                    (
                        await connection.execute(
                            text(
                                """
                                SELECT * FROM assistant_request
                                WHERE external_user_id = :user_id
                                  AND knowledge_id = :knowledge_id
                                  AND idempotency_key = :idempotency_key
                                FOR UPDATE
                                """
                            ),
                            {
                                "user_id": external_user_id,
                                "knowledge_id": knowledge_id,
                                "idempotency_key": idempotency_key,
                            },
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing is not None:
                    row = dict(existing)
                    if str(row["request_hash"]) != request_hash:
                        raise ApiError("IDEMPOTENCY_KEY_REUSED", "幂等键已用于不同请求", 409)
                    if row["status"] == "RUNNING":
                        raise ApiError("REQUEST_IN_PROGRESS", "相同请求仍在处理中", 409)
                    return "REPLAY", row
                if conversation_id is not None:
                    running = await connection.execute(
                        text(
                            """
                            SELECT 1 FROM assistant_request
                            WHERE conversation_id = :conversation_id AND status = 'RUNNING'
                            FOR UPDATE
                            """
                        ),
                        {"conversation_id": conversation_id},
                    )
                    if running.scalar_one_or_none() is not None:
                        raise ApiError("CONVERSATION_BUSY", "会话正在生成回答", 409)
                await connection.execute(
                    text(
                        """
                        INSERT INTO assistant_request (
                            id, external_user_id, knowledge_id, idempotency_key,
                            request_hash, conversation_id, status
                        ) VALUES (
                            :id, :user_id, :knowledge_id, :idempotency_key,
                            :request_hash, :conversation_id, 'RUNNING'
                        )
                        """
                    ),
                    {
                        "id": request_id,
                        "user_id": external_user_id,
                        "knowledge_id": knowledge_id,
                        "idempotency_key": idempotency_key,
                        "request_hash": request_hash,
                        "conversation_id": conversation_id,
                    },
                )
                return "NEW", {"id": request_id, "status": "RUNNING"}
        except IntegrityError as exc:
            # The partial running-conversation index is intentionally the
            # final arbiter under a cross-process race.
            raise ApiError("CONVERSATION_BUSY", "会话正在生成回答", 409) from exc

    async def set_assistant_request_context(
        self,
        *,
        request_id: UUID,
        conversation_id: UUID,
        user_message_id: UUID | None,
        assistant_message_id: UUID | None,
        trace_id: UUID | None,
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE assistant_request
                    SET conversation_id = :conversation_id,
                        user_message_id = :user_message_id,
                        assistant_message_id = :assistant_message_id,
                        trace_id = :trace_id,
                        updated_at = now()
                    WHERE id = :id AND status = 'RUNNING'
                    """
                ),
                {
                    "id": request_id,
                    "conversation_id": conversation_id,
                    "user_message_id": user_message_id,
                    "assistant_message_id": assistant_message_id,
                    "trace_id": trace_id,
                },
            )

    async def complete_assistant_request(
        self,
        *,
        request_id: UUID,
        response: dict[str, Any],
        message_id: UUID,
        conversation_id: UUID,
        trace_id: UUID | None,
    ) -> None:
        payload = json.dumps(response, ensure_ascii=False)
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE assistant_request
                    SET status = 'COMPLETED', conversation_id = :conversation_id,
                        assistant_message_id = :message_id, trace_id = :trace_id,
                        terminal_response = CAST(:payload AS jsonb), updated_at = now()
                    WHERE id = :id AND status = 'RUNNING'
                    """
                ),
                {
                    "id": request_id,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "trace_id": trace_id,
                    "payload": payload,
                },
            )
            await connection.execute(
                text(
                    """
                    UPDATE message
                    SET response_payload = CAST(:payload AS jsonb), updated_at = now()
                    WHERE id = :message_id
                    """
                ),
                {"message_id": message_id, "payload": payload},
            )
            await connection.execute(
                text(
                    """
                    UPDATE conversation
                    SET last_activity_at = now()
                    WHERE id = :conversation_id
                    """
                ),
                {"conversation_id": conversation_id},
            )

    async def fail_assistant_request(
        self,
        *,
        request_id: UUID,
        payload: dict[str, Any],
        cancelled: bool = False,
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE assistant_request
                    SET status = :status,
                        terminal_error = CAST(:payload AS jsonb), updated_at = now()
                    WHERE id = :id AND status = 'RUNNING'
                    """
                ),
                {
                    "id": request_id,
                    "status": "CANCELLED" if cancelled else "FAILED",
                    "payload": json.dumps(payload, ensure_ascii=False),
                },
            )

    async def assistant_document(self, *, knowledge_id: UUID, document_id: UUID) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, kb_id, display_name, filename, minio_bucket, minio_object_key,
                           mime_type, file_size
                    FROM document_source
                    WHERE id = :document_id AND kb_id = :knowledge_id AND status = 'STORED'
                    """
                ),
                {"document_id": document_id, "knowledge_id": knowledge_id},
            )
            row = result.mappings().one_or_none()
            if row is None:
                raise ApiError("DOCUMENT_NOT_FOUND", "文档不存在", 404)
            return dict(row)

    async def assistant_image_asset(
        self, *, knowledge_id: UUID, image_asset_id: UUID
    ) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT a.id, a.mime_type, a.minio_bucket, a.minio_object_key
                    FROM image_asset a
                    JOIN document_element e ON e.id = a.element_id
                    JOIN document_source d ON d.id = e.document_id
                    WHERE a.id = :image_asset_id AND d.kb_id = :knowledge_id
                    """
                ),
                {"image_asset_id": image_asset_id, "knowledge_id": knowledge_id},
            )
            row = result.mappings().one_or_none()
            if row is None:
                raise ApiError("IMAGE_ASSET_NOT_FOUND", "图片不存在", 404)
            return dict(row)
