from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.common.errors import ApiError
from app.core.config import Settings
from app.core.secrets import ModelSecretBox
from app.rag.document_cards import DOCUMENT_CARD_SCHEMA_VERSION
from app.rag.model_providers import environment_api_key, supported_providers
from app.rag.models import (
    DocumentCardCandidate,
    ModelSnapshot,
    RetrievalCandidate,
    RetrievalConfig,
    Turn,
)
from app.rag.scope import build_scope_snapshot, normalize_scope_description

# Converted documents ("conv") export the original file's highlighted text
# regions as flat images: solid-colour strips (all <= 65px tall in this
# corpus, real screenshots start at 220px). Their OCR text is hallucinated
# garbage on a pure-colour rectangle, so they are dropped from delivery and
# doc-QA context entirely instead of shown as colour blocks.
_DECORATIVE_IMAGE_MAX_PX = 80


class SearchQueryUnsupportedError(RuntimeError):
    """A lexical query shape rejected by ParadeDB, safe to degrade per channel."""


def _raise_normalized_search_error(exc: DBAPIError) -> None:
    if "unsupported query shape" in str(exc).casefold():
        raise SearchQueryUnsupportedError("PARADEDB_UNSUPPORTED_QUERY_SHAPE") from exc
    raise exc


def elements_from_rows(rows: Any) -> list[dict[str, Any]]:
    """Map element rows to delivery blocks, dropping decorative image strips."""
    elements: list[dict[str, Any]] = []
    for row in rows:
        asset_id = UUID(str(row["asset_id"])) if row["asset_id"] else None
        if asset_id is not None:
            width = row.get("asset_width")
            height = row.get("asset_height")
            too_small = (
                width is not None
                and height is not None
                and (
                    int(width) < _DECORATIVE_IMAGE_MAX_PX or int(height) < _DECORATIVE_IMAGE_MAX_PX
                )
            )
            if too_small:
                continue
        elements.append(
            {
                "element_id": UUID(str(row["id"])),
                "element_type": str(row["element_type"]),
                "content": str(row["content"] or ""),
                "section_path": list(row["section_path"] or []),
                "image_asset_id": asset_id,
                "image_caption": (
                    str(row["vision_caption"] or row["ocr_text"] or "") if asset_id else ""
                ),
            }
        )
    return elements


class RagRepository:
    def __init__(self, engine: AsyncEngine, settings: Settings) -> None:
        self.engine = engine
        self.settings = settings
        self._secret_box: ModelSecretBox | None = None

    def _require_secret_box(self) -> ModelSecretBox:
        if self._secret_box is None:
            if self.settings.model_secret_key is None:
                raise ApiError(
                    "MODEL_SECRET_NOT_CONFIGURED",
                    "未配置 MODEL_SECRET_KEY，无法管理数据库 API 密钥",
                    409,
                )
            self._secret_box = ModelSecretBox(
                self.settings.model_secret_key,
                version=self.settings.model_secret_key_version,
                previous_key=self.settings.model_secret_key_previous,
                previous_version=self.settings.model_secret_key_previous_version,
            )
        return self._secret_box

    def _decrypt_api_key(self, row: dict[str, Any]) -> SecretStr | None:
        ciphertext = row.get("api_key_ciphertext")
        if ciphertext is None:
            return None
        box = self._require_secret_box()
        return box.decrypt(
            bytes(ciphertext),
            bytes(row["api_key_nonce"]),
            int(row["api_key_key_version"] or 1),
        )

    def _api_key_source(self, model_type: str, provider: str, stored_key: bool) -> str | None:
        if stored_key:
            return "DATABASE"
        environment_key = self._environment_key_for(model_type, provider)
        if environment_key is not None and environment_key.get_secret_value().strip():
            return "ENV"
        return None

    async def begin_turn(
        self,
        *,
        knowledge_id: UUID,
        conversation_id: UUID | None,
        question: str,
        request_id: str,
        mode: str = "CHAT",
        user_id: str | None = None,
        reuse_user_message_id: UUID | None = None,
        reuse_assistant_message_id: UUID | None = None,
        parent_trace_id: UUID | None = None,
        fallback_reason: str | None = None,
    ) -> Turn:
        trace_id = uuid4()
        if (reuse_user_message_id is None) != (reuse_assistant_message_id is None):
            raise ValueError("fallback message IDs must be provided together")
        if (parent_trace_id is None) != (fallback_reason is None):
            raise ValueError("fallback trace metadata must be provided together")
        if parent_trace_id is not None and reuse_user_message_id is None:
            raise ValueError("fallback trace metadata requires reusable messages")
        reuse_messages = reuse_user_message_id is not None
        user_message_id = reuse_user_message_id or uuid4()
        assistant_message_id = reuse_assistant_message_id or uuid4()
        async with self.engine.begin() as connection:
            kb_result = await connection.execute(
                text(
                    """
                    SELECT kb.status AS kb_status, kb.name AS kb_name,
                           kb.description AS kb_description, kb.updated_at AS kb_updated_at,
                           COALESCE(kb.rag_active_index_id, kb.active_index_id)
                               AS rag_active_index_id,
                           i.status AS index_status, i.kb_id AS index_kb_id,
                           i.embedding_model_name
                    FROM knowledge_base kb
                    LEFT JOIN knowledge_index i
                      ON i.id = COALESCE(kb.rag_active_index_id, kb.active_index_id)
                    WHERE kb.id = :kb_id
                    FOR UPDATE OF kb
                    """
                ),
                {"kb_id": knowledge_id},
            )
            kb = kb_result.mappings().one_or_none()
            if kb is None:
                raise ApiError("KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在", 404)
            if kb["kb_status"] != "ENABLED":
                raise ApiError("FORBIDDEN", "知识库已停用", 403)
            if kb["rag_active_index_id"] is None:
                raise ApiError("KNOWLEDGE_BASE_NOT_READY", "知识库尚无 Active Index", 409)
            if kb["index_status"] != "ACTIVE" or kb["index_kb_id"] != knowledge_id:
                raise ApiError("INDEX_NOT_READY", "知识库 Active Index 状态异常", 409)

            resolved_conversation_id = conversation_id or uuid4()
            effective_index_id = UUID(str(kb["rag_active_index_id"]))
            embedding_model_name = str(kb["embedding_model_name"])
            scope = build_scope_snapshot(
                kb["kb_description"], max_tokens=self.settings.m3_rag_scope_max_tokens
            )
            if conversation_id is None:
                await connection.execute(
                    text(
                        """
                        INSERT INTO conversation (
                            id, knowledge_id, rag_index_id, user_id, title
                        ) VALUES (
                            :id, :knowledge_id, :rag_index_id, :user_id, :title
                        )
                        """
                    ),
                    {
                        "id": resolved_conversation_id,
                        "knowledge_id": knowledge_id,
                        "rag_index_id": effective_index_id,
                        "user_id": user_id,
                        "title": question.strip()[:200] or None,
                    },
                )
            else:
                conversation_result = await connection.execute(
                    text(
                        "SELECT knowledge_id, rag_index_id "
                        "FROM conversation WHERE id = :id FOR UPDATE"
                    ),
                    {"id": conversation_id},
                )
                conversation_row = conversation_result.mappings().one_or_none()
                if conversation_row is None:
                    raise ApiError("CONVERSATION_NOT_FOUND", "会话不存在", 404)
                if conversation_row["knowledge_id"] != knowledge_id:
                    raise ApiError("CONVERSATION_KB_MISMATCH", "会话不属于指定知识库", 409)
                if conversation_row["rag_index_id"] is not None:
                    effective_index_id = UUID(str(conversation_row["rag_index_id"]))
                    frozen_result = await connection.execute(
                        text(
                            """
                            SELECT kb_id, status, embedding_model_name
                            FROM knowledge_index WHERE id = :index_id
                            """
                        ),
                        {"index_id": effective_index_id},
                    )
                    frozen = frozen_result.mappings().one_or_none()
                    if (
                        frozen is None
                        or frozen["kb_id"] != knowledge_id
                        or frozen["status"] not in {"ACTIVE", "DEPRECATED"}
                    ):
                        raise ApiError(
                            "CONVERSATION_RAG_VERSION_INVALID",
                            "会话绑定的 RAG 版本不可用",
                            409,
                        )
                    embedding_model_name = str(frozen["embedding_model_name"])
                else:
                    await connection.execute(
                        text("UPDATE conversation SET rag_index_id = :index_id WHERE id = :id"),
                        {"index_id": effective_index_id, "id": conversation_id},
                    )

            if reuse_messages:
                if conversation_id is None:
                    raise ApiError("CONVERSATION_NOT_FOUND", "降级消息缺少所属会话", 409)
                pair_result = await connection.execute(
                    text(
                        """
                        SELECT id, role, status FROM message
                        WHERE conversation_id = :conversation_id
                          AND id IN (:user_message_id, :assistant_message_id)
                        FOR UPDATE
                        """
                    ),
                    {
                        "conversation_id": resolved_conversation_id,
                        "user_message_id": user_message_id,
                        "assistant_message_id": assistant_message_id,
                    },
                )
                pair = {UUID(str(row["id"])): dict(row) for row in pair_result.mappings()}
                if (
                    pair.get(user_message_id, {}).get("role") != "USER"
                    or pair.get(assistant_message_id, {}).get("role") != "ASSISTANT"
                    or pair.get(assistant_message_id, {}).get("status")
                    not in {"COMPLETED", "FAILED"}
                ):
                    raise ApiError("FALLBACK_MESSAGE_CONFLICT", "降级消息状态不一致", 409)

            history, history_diagnostics = await self._v2_history(
                connection,
                resolved_conversation_id,
                assistant_message_id if reuse_messages else None,
            )
            previous_document_ids: list[UUID] = []
            for value in history_diagnostics.get("previous_document_ids") or []:
                try:
                    document_id = UUID(str(value))
                except (TypeError, ValueError):
                    continue
                if document_id not in previous_document_ids:
                    previous_document_ids.append(document_id)

            llm_result = await connection.execute(
                text(
                    """
                    SELECT provider, model_name, parameters, base_url,
                           api_key_ciphertext, api_key_nonce, api_key_key_version
                    FROM model_config WHERE model_type = 'LLM' AND enabled
                    """
                )
            )
            llm_row = llm_result.mappings().one_or_none()
            if llm_row is None:
                raise ApiError("MODEL_NOT_CONFIGURED", "LLM 模型未配置", 503)

            rerank_result = await connection.execute(
                text(
                    """
                    SELECT provider, model_name, parameters, base_url,
                           api_key_ciphertext, api_key_nonce, api_key_key_version
                    FROM model_config WHERE model_type = 'RERANK' AND enabled
                    """
                )
            )
            rerank_row = rerank_result.mappings().one_or_none()
            if rerank_row is None:
                raise ApiError("MODEL_NOT_CONFIGURED", "Rerank 模型未配置", 503)

            # Optional: the enabled EMBEDDING row lets the query side use the
            # admin-managed provider/key instead of env-only settings.  A
            # missing row keeps the env fallback.
            embedding_result = await connection.execute(
                text(
                    """
                    SELECT provider, model_name, base_url,
                           api_key_ciphertext, api_key_nonce, api_key_key_version
                    FROM model_config WHERE model_type = 'EMBEDDING' AND enabled
                    """
                )
            )
            embedding_row = embedding_result.mappings().one_or_none()
            embedding_snapshot = (
                ModelSnapshot(
                    provider=str(embedding_row["provider"]),
                    model_name=str(embedding_row["model_name"]),
                    parameters={},
                    base_url=(
                        str(embedding_row["base_url"])
                        if embedding_row["base_url"] is not None
                        else None
                    ),
                    api_key=self._decrypt_api_key(dict(embedding_row)),
                )
                if embedding_row is not None
                else None
            )

            config_result = await connection.execute(
                text(
                    """
                    SELECT vector_top_k, bm25_top_k, fusion_top_k, rerank_top_n,
                           context_max_chunks
                    FROM rag_config WHERE id
                    """
                )
            )
            config_row = config_result.mappings().one_or_none()
            if config_row is None:
                retrieval = RetrievalConfig(
                    vector_top_k=self.settings.m3_vector_top_k,
                    bm25_top_k=self.settings.m5_bm25_top_k,
                    fusion_top_k=self.settings.m5_fusion_top_k,
                    rerank_top_n=self.settings.m5_rerank_top_n,
                    context_max_chunks=self.settings.m3_context_max_chunks,
                )

            else:
                retrieval = RetrievalConfig(
                    vector_top_k=int(config_row["vector_top_k"]),
                    bm25_top_k=int(config_row["bm25_top_k"]),
                    fusion_top_k=int(config_row["fusion_top_k"]),
                    rerank_top_n=int(config_row["rerank_top_n"]),
                    context_max_chunks=int(config_row["context_max_chunks"]),
                )

            def model_snapshot(model: ModelSnapshot | None) -> dict[str, Any] | None:
                if model is None:
                    return None
                return {
                    "provider": model.provider,
                    "model_name": model.model_name,
                    "base_url": model.base_url,
                    "parameters": model.parameters,
                }

            runtime_snapshot = {
                "schema_version": 1,
                "history": history_diagnostics,
                "engine": "RAG",
                "scope": {
                    "source": "KNOWLEDGE_BASE_SNAPSHOT",
                    "knowledge_updated_at": kb["kb_updated_at"].isoformat(),
                    "sha256": scope.sha256,
                    "chars": scope.chars,
                    "tokens": scope.tokens,
                    "truncated": scope.truncated,
                },
                "index": {
                    "id": str(effective_index_id),
                    "embedding_model_name": embedding_model_name,
                },
                "models": {
                    "llm": {
                        "provider": str(llm_row["provider"]),
                        "model_name": str(llm_row["model_name"]),
                        "base_url": str(llm_row["base_url"])
                        if llm_row["base_url"] is not None
                        else None,
                        "parameters": dict(llm_row["parameters"] or {}),
                    },
                    "rerank": {
                        "provider": str(rerank_row["provider"]),
                        "model_name": str(rerank_row["model_name"]),
                        "base_url": str(rerank_row["base_url"])
                        if rerank_row["base_url"] is not None
                        else None,
                        "parameters": dict(rerank_row["parameters"] or {}),
                    },
                    "embedding": model_snapshot(embedding_snapshot),
                },
                "retrieval": asdict(retrieval),
                "pipeline": "V2",
            }

            if reuse_messages:
                if parent_trace_id is not None:
                    parent_result = await connection.execute(
                        text(
                            """
                            UPDATE rag_trace
                            SET message_id = NULL
                            WHERE trace_id = :parent_trace_id AND engine = 'SUPPORT'
                              AND conversation_id = :conversation_id
                              AND (
                                  message_id = :assistant_message_id OR message_id IS NULL
                              )
                            RETURNING trace_id
                            """
                        ),
                        {
                            "parent_trace_id": parent_trace_id,
                            "conversation_id": resolved_conversation_id,
                            "assistant_message_id": assistant_message_id,
                        },
                    )
                    if parent_result.scalar_one_or_none() is None:
                        raise ApiError("FALLBACK_TRACE_CONFLICT", "降级 Trace 状态不一致", 409)
                await connection.execute(
                    text(
                        """
                        UPDATE message
                        SET content = '', sources = '[]'::jsonb,
                            content_blocks = '[]'::jsonb, response_type = NULL,
                            support_mode = NULL, status = 'STREAMING', updated_at = now(),
                            rag_user_message_id = :user_message_id
                        WHERE id = :assistant_id
                        """
                    ),
                    {"assistant_id": assistant_message_id, "user_message_id": user_message_id},
                )
            else:
                sequence_result = await connection.execute(
                    text(
                        """
                        SELECT COALESCE(max(sequence_no), 0)
                        FROM message WHERE conversation_id = :conversation_id
                        """
                    ),
                    {"conversation_id": resolved_conversation_id},
                )
                first_sequence = int(sequence_result.scalar_one()) + 1
                await connection.execute(
                    text(
                        """
                        INSERT INTO message (
                            id, conversation_id, sequence_no, role, content, status, focus_epoch,
                            rag_user_message_id
                        ) VALUES (
                            :user_id, :conversation_id, :user_sequence,
                            'USER', :question, 'COMPLETED', :focus_epoch, NULL
                        ), (
                            :assistant_id, :conversation_id, :assistant_sequence,
                            'ASSISTANT', '', 'STREAMING', :focus_epoch, :user_id
                        )
                        """
                    ),
                    {
                        "user_id": user_message_id,
                        "assistant_id": assistant_message_id,
                        "conversation_id": resolved_conversation_id,
                        "question": question,
                        "user_sequence": first_sequence,
                        "assistant_sequence": first_sequence + 1,
                        # RAG V2 does not own Support's conversation focus state.
                        "focus_epoch": 0,
                    },
                )
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_trace (
                        id, trace_id, request_id, kb_id, index_id, conversation_id,
                        message_id, question, mode, status, user_id, engine, stage,
                        parent_trace_id, fallback_from, fallback_reason, source,
                        runtime_snapshot
                    ) VALUES (
                        :id, :trace_id, :request_id, :kb_id, :index_id, :conversation_id,
                        :message_id, :question, :mode, 'RUNNING', :user_id,
                        'RAG', 'PREPARE', :parent_trace_id,
                        CASE
                            WHEN CAST(:parent_trace_id AS uuid) IS NULL THEN NULL
                            ELSE 'SUPPORT'
                        END,
                        :fallback_reason, :source, CAST(:runtime_snapshot AS jsonb)
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "trace_id": trace_id,
                    "request_id": request_id,
                    "kb_id": knowledge_id,
                    "index_id": effective_index_id,
                    "conversation_id": resolved_conversation_id,
                    "message_id": assistant_message_id,
                    "question": question,
                    "mode": mode,
                    "user_id": user_id,
                    "parent_trace_id": parent_trace_id,
                    "fallback_reason": fallback_reason,
                    "source": "PLAYGROUND" if mode == "PLAYGROUND" else "INTERNAL_CHAT",
                    "runtime_snapshot": json.dumps(runtime_snapshot, ensure_ascii=False),
                },
            )
            await connection.execute(
                text("UPDATE conversation SET updated_at = now() WHERE id = :id"),
                {"id": resolved_conversation_id},
            )

        parameters = llm_row["parameters"] if isinstance(llm_row["parameters"], dict) else {}
        rerank_parameters = (
            rerank_row["parameters"] if isinstance(rerank_row["parameters"], dict) else {}
        )
        return Turn(
            trace_id=trace_id,
            conversation_id=resolved_conversation_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            knowledge_id=knowledge_id,
            index_id=effective_index_id,
            embedding_model_name=embedding_model_name,
            llm=ModelSnapshot(
                provider=str(llm_row["provider"]),
                model_name=str(llm_row["model_name"]),
                parameters=dict(parameters),
                base_url=(str(llm_row["base_url"]) if llm_row["base_url"] is not None else None),
                api_key=self._decrypt_api_key(dict(llm_row)),
            ),
            history=history,
            history_diagnostics=history_diagnostics,
            previous_document_ids=tuple(previous_document_ids[:3]),
            rerank=ModelSnapshot(
                provider=str(rerank_row["provider"]),
                model_name=str(rerank_row["model_name"]),
                parameters=dict(rerank_parameters),
                base_url=(
                    str(rerank_row["base_url"]) if rerank_row["base_url"] is not None else None
                ),
                api_key=self._decrypt_api_key(dict(rerank_row)),
            ),
            embedding=embedding_snapshot,
            retrieval=retrieval,
            knowledge_name=normalize_scope_description(str(kb["kb_name"])),
            knowledge_description=(
                str(kb["kb_description"]) if kb["kb_description"] is not None else None
            ),
            knowledge_updated_at=kb["kb_updated_at"],
            scope_description=scope.description,
            scope_sha256=scope.sha256,
            scope_chars=scope.chars,
            scope_tokens=scope.tokens,
            scope_truncated=scope.truncated,
        )

    async def _v2_history(
        self,
        connection: Any,
        conversation_id: UUID,
        excluded_message_id: UUID | None = None,
    ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        from app.rag.v2.history import select_complete_round_history

        result = await connection.execute(
            text("""
            SELECT a.id, a.content AS answer, COALESCE(ar.status, a.status) AS status,
                   u.id AS user_id, u.content AS question, u.status AS user_status,
                   previous_trace.used_document_ids,
                   COALESCE(
                       a.rag_history_context #>> '{input_guard}',
                       previous_trace.input_guard_reason
                   ) AS input_guard_reason,
                   COALESCE(
                       a.rag_history_context #>> '{response_path}',
                       previous_trace.response_path
                   ) AS response_path
            FROM (
                SELECT * FROM message
                WHERE conversation_id = :conversation_id AND role = 'ASSISTANT'
                  AND (CAST(:excluded AS uuid) IS NULL OR id != :excluded)
                ORDER BY sequence_no DESC LIMIT :scan_limit
            ) a
            LEFT JOIN LATERAL (
                SELECT user_message_id, status FROM assistant_request
                WHERE assistant_message_id = a.id AND conversation_id = a.conversation_id
                ORDER BY created_at DESC, id DESC LIMIT 1
            ) ar ON true
            LEFT JOIN message u
              ON u.id = COALESCE(a.rag_user_message_id, ar.user_message_id)
             AND u.conversation_id = a.conversation_id AND u.role = 'USER'
             AND u.sequence_no < a.sequence_no
            LEFT JOIN LATERAL (
                SELECT retrieval_result #>
                           '{doc_routing,generation,answer,used_document_ids}'
                           AS used_document_ids,
                       COALESCE(
                           retrieval_result #>> '{doc_routing,generation,input_guard}',
                           citation_result ->> 'shortcut'
                       ) AS input_guard_reason,
                       COALESCE(
                           retrieval_result #>> '{doc_routing,generation,response_path}',
                           citation_result ->> 'response_path'
                       ) AS response_path
                FROM rag_trace
                WHERE message_id = a.id AND conversation_id = a.conversation_id
                  AND engine = 'RAG' AND status = 'COMPLETED'
                ORDER BY finished_at DESC NULLS LAST, created_at DESC, id DESC
                LIMIT 1
            ) previous_trace ON true
            ORDER BY a.sequence_no DESC
        """),
            {
                "conversation_id": conversation_id,
                "excluded": excluded_message_id,
                "scan_limit": max(20, self.settings.m3_history_max_messages * 4),
            },
        )
        return select_complete_round_history(
            [dict(row) for row in result.mappings()],
            max_rounds=self.settings.m3_rag_history_max_rounds,
            max_messages=self.settings.m3_history_max_messages,
            token_budget=self.settings.m3_history_token_budget,
        )

    async def get_document_retrieval_seeds(
        self,
        *,
        knowledge_id: UUID,
        index_id: UUID,
        document_ids: tuple[UUID, ...],
    ) -> list[dict[str, Any]]:
        """Load lightweight, index-bound metadata for follow-up soft candidates."""

        if not document_ids:
            return []
        async with self.engine.connect() as connection:
            rows = list(
                (
                    await connection.execute(
                        text(
                            """
                            SELECT d.id AS document_id, d.display_name AS document,
                                   card.document_type, card.card_payload,
                                   ARRAY(
                                       SELECT c.content
                                       FROM document_chunk c
                                       WHERE c.kb_id = :knowledge_id
                                         AND c.index_id = :index_id
                                         AND c.document_id = d.id
                                       ORDER BY c.sequence_no, c.id
                                       LIMIT 3
                                   ) AS representative_texts
                            FROM document_source d
                            JOIN index_document idoc
                              ON idoc.document_id = d.id AND idoc.index_id = :index_id
                             AND idoc.status = 'READY'
                            LEFT JOIN LATERAL (
                                SELECT rdc.document_type, rdc.card_payload
                                FROM rag_document_card rdc
                                WHERE rdc.knowledge_id = :knowledge_id
                                  AND rdc.index_id = :index_id
                                  AND rdc.document_id = d.id
                                  AND rdc.generation_status = 'READY'
                                  AND rdc.schema_version = :schema_version
                                  AND rdc.review_status <> 'REJECTED'
                                ORDER BY (rdc.review_status = 'CONFIRMED') DESC,
                                         rdc.updated_at DESC, rdc.id DESC
                                LIMIT 1
                            ) card ON true
                            WHERE d.kb_id = :knowledge_id AND d.status = 'STORED'
                              AND d.id = ANY(CAST(:document_ids AS uuid[]))
                            """
                        ),
                        {
                            "knowledge_id": knowledge_id,
                            "index_id": index_id,
                            "document_ids": list(document_ids),
                            "schema_version": DOCUMENT_CARD_SCHEMA_VERSION,
                        },
                    )
                ).mappings()
            )
        order = {document_id: index for index, document_id in enumerate(document_ids)}
        seeds: list[dict[str, Any]] = []
        for row in rows:
            payload = row.get("card_payload")
            card = dict(payload) if isinstance(payload, dict) else {}
            seeds.append(
                {
                    "document_id": str(row["document_id"]),
                    "document": str(row["document"]),
                    "score": 0.0,
                    "chunk_ids": [],
                    "representative_texts": [
                        str(value) for value in row.get("representative_texts") or [] if value
                    ],
                    "card": {
                        "document_type": str(row.get("document_type") or ""),
                        "summary": str(card.get("summary") or "")[:500],
                        "tasks": [str(value)[:160] for value in card.get("tasks") or []][:12],
                    },
                    "candidate_source": "PREVIOUS_EVIDENCE",
                }
            )
        seeds.sort(key=lambda item: order.get(UUID(str(item["document_id"])), len(order)))
        return seeds

    async def vector_search(
        self,
        *,
        knowledge_id: UUID,
        index_id: UUID,
        embedding: list[float],
        limit: int,
        document_id: UUID | None = None,
    ) -> list[RetrievalCandidate]:
        vector_literal = "[" + ",".join(format(value, ".9g") for value in embedding) + "]"
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT c.id, c.document_id, d.display_name AS document,
                           c.chunk_type, c.sequence_no, c.content, c.token_count, c.section_path,
                           c.parent_id, c.previous_chunk_id, c.next_chunk_id,
                           c.suspected_incomplete, c.incomplete_reasons, c.is_procedural,
                           ARRAY(
                               SELECT ce.element_id::text FROM chunk_element ce
                               WHERE ce.chunk_id = c.id ORDER BY ce.ordinal
                           ) AS element_ids,
                           ARRAY(
                               SELECT a.id::text
                               FROM chunk_element ce
                               JOIN image_asset a ON a.element_id = ce.element_id
                               WHERE ce.chunk_id = c.id ORDER BY ce.ordinal
                           ) AS image_asset_ids,
                           c.embedding <=> CAST(:embedding AS vector) AS distance
                    FROM document_chunk c
                    JOIN document_source d ON d.id = c.document_id
                    WHERE c.kb_id = :kb_id AND c.index_id = :index_id
                      AND d.status = 'STORED'
                      AND (CAST(:document_id AS uuid) IS NULL OR c.document_id = :document_id)
                    ORDER BY c.embedding <=> CAST(:embedding AS vector), c.id
                    LIMIT :limit
                    """
                ),
                {
                    "embedding": vector_literal,
                    "kb_id": knowledge_id,
                    "index_id": index_id,
                    "limit": limit,
                    "document_id": document_id,
                },
            )
            rows = list(result.mappings())
        candidates: list[RetrievalCandidate] = []
        for rank, row in enumerate(rows, start=1):
            distance = float(row["distance"])
            candidates.append(
                RetrievalCandidate(
                    chunk_id=UUID(str(row["id"])),
                    document_id=UUID(str(row["document_id"])),
                    document=str(row["document"]),
                    chunk_type=str(row["chunk_type"]),
                    content=str(row["content"]),
                    token_count=int(row["token_count"]),
                    section_path=list(row["section_path"]),
                    element_ids=[UUID(item) for item in row["element_ids"]],
                    distance=distance,
                    similarity=max(-1.0, min(1.0, 1.0 - distance)),
                    rank=rank,
                    sequence_no=int(row["sequence_no"]),
                    parent_id=UUID(str(row["parent_id"])) if row["parent_id"] else None,
                    previous_chunk_id=(
                        UUID(str(row["previous_chunk_id"])) if row["previous_chunk_id"] else None
                    ),
                    next_chunk_id=(
                        UUID(str(row["next_chunk_id"])) if row["next_chunk_id"] else None
                    ),
                    suspected_incomplete=bool(row["suspected_incomplete"]),
                    incomplete_reasons=list(row["incomplete_reasons"]),
                    is_procedural=bool(row["is_procedural"]),
                    image_asset_ids=[UUID(item) for item in row["image_asset_ids"]],
                    vector_rank=rank,
                )
            )
        return candidates

    async def bm25_search(
        self,
        *,
        knowledge_id: UUID,
        index_id: UUID,
        query: str,
        limit: int,
        document_id: UUID | None = None,
    ) -> list[RetrievalCandidate]:
        try:
            async with self.engine.connect() as connection:
                result = await connection.execute(
                    text(
                        """
                    SELECT document_chunk.id, document_chunk.document_id,
                           d.display_name AS document, document_chunk.chunk_type,
                           document_chunk.sequence_no,
                           document_chunk.content, document_chunk.token_count,
                           document_chunk.section_path,
                           document_chunk.parent_id, document_chunk.previous_chunk_id,
                           document_chunk.next_chunk_id, document_chunk.suspected_incomplete,
                           document_chunk.incomplete_reasons, document_chunk.is_procedural,
                           ARRAY(
                               SELECT ce.element_id::text FROM chunk_element ce
                               WHERE ce.chunk_id = document_chunk.id ORDER BY ce.ordinal
                           ) AS element_ids,
                           ARRAY(
                               SELECT a.id::text
                               FROM chunk_element ce
                               JOIN image_asset a ON a.element_id = ce.element_id
                               WHERE ce.chunk_id = document_chunk.id ORDER BY ce.ordinal
                           ) AS image_asset_ids,
                           pdb.score(document_chunk.id) AS bm25_score
                    FROM document_chunk
                    JOIN document_source d ON d.id = document_chunk.document_id
                    WHERE document_chunk.search_text ||| :query
                      AND document_chunk.kb_id = :kb_id
                      AND document_chunk.index_id = :index_id
                      AND d.status = 'STORED'
                      AND (CAST(:document_id AS uuid) IS NULL OR
                           document_chunk.document_id = :document_id)
                    ORDER BY pdb.score(document_chunk.id) DESC, document_chunk.id
                    LIMIT :limit
                    """
                    ),
                    {
                        "query": query,
                        "kb_id": knowledge_id,
                        "index_id": index_id,
                        "limit": limit,
                        "document_id": document_id,
                    },
                )
                rows = list(result.mappings())
        except DBAPIError as exc:
            _raise_normalized_search_error(exc)
        return [
            RetrievalCandidate(
                chunk_id=UUID(str(row["id"])),
                document_id=UUID(str(row["document_id"])),
                document=str(row["document"]),
                chunk_type=str(row["chunk_type"]),
                content=str(row["content"]),
                token_count=int(row["token_count"]),
                section_path=list(row["section_path"]),
                element_ids=[UUID(item) for item in row["element_ids"]],
                distance=None,
                similarity=None,
                rank=rank,
                sequence_no=int(row["sequence_no"]),
                parent_id=UUID(str(row["parent_id"])) if row["parent_id"] else None,
                previous_chunk_id=(
                    UUID(str(row["previous_chunk_id"])) if row["previous_chunk_id"] else None
                ),
                next_chunk_id=(UUID(str(row["next_chunk_id"])) if row["next_chunk_id"] else None),
                suspected_incomplete=bool(row["suspected_incomplete"]),
                incomplete_reasons=list(row["incomplete_reasons"]),
                is_procedural=bool(row["is_procedural"]),
                image_asset_ids=[UUID(item) for item in row["image_asset_ids"]],
                bm25_rank=rank,
                bm25_score=float(row["bm25_score"]),
            )
            for rank, row in enumerate(rows, start=1)
        ]

    async def document_card_vector_search(
        self,
        *,
        knowledge_id: UUID,
        index_id: UUID,
        embedding: list[float],
        limit: int,
    ) -> list[DocumentCardCandidate]:
        vector_literal = "[" + ",".join(format(value, ".9g") for value in embedding) + "]"
        async with self.engine.connect() as connection:
            rows = list(
                (
                    await connection.execute(
                        text(
                            """
                            SELECT c.id, c.document_id, d.display_name AS document,
                                   c.document_type, c.card_payload, c.review_status,
                                   c.routing_embedding <=> CAST(:embedding AS vector) AS distance
                            FROM rag_document_card c
                            JOIN document_source d ON d.id = c.document_id
                            JOIN index_document idoc
                              ON idoc.index_id = c.index_id AND idoc.document_id = c.document_id
                            WHERE c.knowledge_id = :knowledge_id AND c.index_id = :index_id
                              AND c.generation_status = 'READY'
                              AND c.schema_version = :schema_version
                              AND c.review_status <> 'REJECTED'
                              AND c.routing_embedding IS NOT NULL
                              AND d.kb_id = :knowledge_id AND d.status = 'STORED'
                              AND idoc.status = 'READY'
                            ORDER BY c.routing_embedding <=> CAST(:embedding AS vector), c.id
                            LIMIT :limit
                            """
                        ),
                        {
                            "knowledge_id": knowledge_id,
                            "index_id": index_id,
                            "embedding": vector_literal,
                            "schema_version": DOCUMENT_CARD_SCHEMA_VERSION,
                            "limit": limit,
                        },
                    )
                ).mappings()
            )
        return [self._document_card_candidate(dict(row), rank) for rank, row in enumerate(rows, 1)]

    async def document_card_bm25_search(
        self,
        *,
        knowledge_id: UUID,
        index_id: UUID,
        query: str,
        limit: int,
    ) -> list[DocumentCardCandidate]:
        try:
            async with self.engine.connect() as connection:
                rows = list(
                    (
                        await connection.execute(
                            text(
                                """
                            SELECT rag_document_card.id, rag_document_card.document_id,
                                   d.display_name AS document,
                                   rag_document_card.document_type,
                                   rag_document_card.card_payload,
                                   rag_document_card.review_status,
                                   pdb.score(rag_document_card.id) AS bm25_score
                            FROM rag_document_card
                            JOIN document_source d ON d.id = rag_document_card.document_id
                            JOIN index_document idoc
                              ON idoc.index_id = rag_document_card.index_id
                             AND idoc.document_id = rag_document_card.document_id
                            WHERE rag_document_card.routing_text ||| :query
                              AND rag_document_card.knowledge_id = :knowledge_id
                              AND rag_document_card.index_id = :index_id
                              AND rag_document_card.generation_status = 'READY'
                              AND rag_document_card.schema_version = :schema_version
                              AND rag_document_card.review_status <> 'REJECTED'
                              AND d.kb_id = :knowledge_id AND d.status = 'STORED'
                              AND idoc.status = 'READY'
                            ORDER BY pdb.score(rag_document_card.id) DESC,
                                     rag_document_card.id
                            LIMIT :limit
                            """
                            ),
                            {
                                "knowledge_id": knowledge_id,
                                "index_id": index_id,
                                "query": query,
                                "schema_version": DOCUMENT_CARD_SCHEMA_VERSION,
                                "limit": limit,
                            },
                        )
                    ).mappings()
                )
        except DBAPIError as exc:
            _raise_normalized_search_error(exc)
        return [self._document_card_candidate(dict(row), rank) for rank, row in enumerate(rows, 1)]

    @staticmethod
    def _document_card_candidate(row: dict[str, Any], rank: int) -> DocumentCardCandidate:
        payload = row.get("card_payload")
        card = dict(payload) if isinstance(payload, dict) else {}
        distance = float(row["distance"]) if row.get("distance") is not None else None
        return DocumentCardCandidate(
            card_id=UUID(str(row["id"])),
            document_id=UUID(str(row["document_id"])),
            document=str(row["document"]),
            document_type=str(row["document_type"]),
            summary=str(card.get("summary") or "")[:500],
            tasks=[str(item)[:160] for item in card.get("tasks") or []][:12],
            review_status=str(row["review_status"]),
            rank=rank,
            distance=distance,
            similarity=(max(-1.0, min(1.0, 1.0 - distance)) if distance is not None else None),
            bm25_score=(float(row["bm25_score"]) if row.get("bm25_score") is not None else None),
        )

    async def save_v2_progress(
        self,
        turn: Turn,
        *,
        generation: dict[str, Any],
        selected_context: list[dict[str, Any]],
        timings: dict[str, Any],
    ) -> None:
        """Persist privacy-reduced V2 retrieval/context before answer generation."""

        doc_routing = {
            "resolved_from": "RAG_V2",
            "pipeline": "V2",
            "generation": generation,
        }
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE rag_trace
                    SET retrieval_result = jsonb_set(
                            COALESCE(retrieval_result, '{}'::jsonb),
                            '{doc_routing}', CAST(:doc_routing AS jsonb)
                        ),
                        selected_context = CAST(:context AS jsonb),
                        runtime_snapshot = jsonb_set(
                            COALESCE(runtime_snapshot, '{}'::jsonb),
                            '{retrieval}', CAST(:effective_retrieval AS jsonb)
                        ),
                        latency = CAST(:latency AS jsonb)
                    WHERE trace_id = :trace_id AND status = 'RUNNING'
                    """
                ),
                {
                    "trace_id": turn.trace_id,
                    "doc_routing": json.dumps(doc_routing, ensure_ascii=False),
                    "context": json.dumps(selected_context, ensure_ascii=False),
                    "effective_retrieval": json.dumps(asdict(turn.retrieval)),
                    "latency": json.dumps(timings),
                },
            )

    async def complete_turn(
        self,
        turn: Turn,
        *,
        answer: str,
        sources: list[dict[str, Any]],
        usage: dict[str, Any],
        citation_result: dict[str, Any],
        timings: dict[str, int],
        doc_routing: dict[str, Any] | None = None,
        response_type: str | None = None,
        answer_mode: str | None = None,
    ) -> None:
        routing_patch = ""
        routing_params: dict[str, Any] = {}
        if doc_routing is not None:
            routing_patch = (
                ", retrieval_result = jsonb_set(COALESCE(retrieval_result, '{}'::jsonb),"
                " '{doc_routing}', CAST(:doc_routing AS jsonb))"
            )
            routing_params["doc_routing"] = json.dumps(doc_routing, ensure_ascii=False)
            generation = doc_routing.get("generation")
            if generation is not None:
                routing_patch += (
                    ", runtime_snapshot = jsonb_set(COALESCE(runtime_snapshot, '{}'::jsonb),"
                    " '{generation}', CAST(:generation AS jsonb))"
                )
                routing_params["generation"] = json.dumps(generation)
        history_context: dict[str, Any] = {}
        if doc_routing is not None and isinstance(doc_routing.get("generation"), dict):
            input_guard = doc_routing["generation"].get("input_guard")
            if isinstance(input_guard, str) and input_guard:
                history_context["input_guard"] = input_guard
            response_path = doc_routing["generation"].get("response_path")
            if response_path in {"KNOWLEDGE", "CONVERSATION"}:
                history_context["response_path"] = response_path
        async with self.engine.begin() as connection:
            message_result = await connection.execute(
                text(
                    """
                    UPDATE message SET content = :answer, sources = CAST(:sources AS jsonb),
                        rag_history_context = (
                            COALESCE(rag_history_context, '{}'::jsonb)
                            - 'input_guard' - 'response_path'
                        ) || CAST(:history_context AS jsonb),
                        status = 'COMPLETED', updated_at = now()
                    WHERE id = :message_id
                      AND conversation_id = :conversation_id
                      AND role = 'ASSISTANT'
                      AND status = 'STREAMING'
                    """
                ),
                {
                    "message_id": turn.assistant_message_id,
                    "conversation_id": turn.conversation_id,
                    "answer": answer,
                    "sources": json.dumps(sources, ensure_ascii=False),
                    "history_context": json.dumps(history_context, ensure_ascii=False),
                },
            )
            trace_result = await connection.execute(
                text(
                    """
                    UPDATE rag_trace SET answer = :answer, sources = CAST(:sources AS jsonb),
                        model_usage = CAST(:usage AS jsonb),
                        latency = COALESCE(latency, '{}'::jsonb) || CAST(:latency AS jsonb),
                        citation_result = CAST(:citation_result AS jsonb),
                        response_type = :response_type, answer_mode = :answer_mode,
                        status = 'COMPLETED', finished_at = now()
                    """
                    + routing_patch
                    + """
                    WHERE trace_id = :trace_id
                      AND conversation_id = :conversation_id
                      AND message_id = :message_id
                      AND status = 'RUNNING'
                    """
                ),
                {
                    "trace_id": turn.trace_id,
                    "conversation_id": turn.conversation_id,
                    "message_id": turn.assistant_message_id,
                    "answer": answer,
                    "sources": json.dumps(sources, ensure_ascii=False),
                    "usage": json.dumps(usage),
                    "citation_result": json.dumps(citation_result, ensure_ascii=False),
                    "latency": json.dumps(timings),
                    "response_type": response_type,
                    "answer_mode": answer_mode,
                    **routing_params,
                },
            )
            if message_result.rowcount != 1 or trace_result.rowcount != 1:
                # Report the state of whichever row refused the terminal
                # write; a row this transaction did update would read back as
                # its new value and tell the operator nothing.
                state = (
                    (
                        await connection.execute(
                            text(
                                """
                                SELECT
                                    (SELECT status FROM message
                                      WHERE id = :message_id
                                        AND conversation_id = :conversation_id)
                                        AS message_status,
                                    (SELECT status FROM rag_trace
                                      WHERE trace_id = :trace_id
                                        AND conversation_id = :conversation_id)
                                        AS trace_status
                                """
                            ),
                            {
                                "message_id": turn.assistant_message_id,
                                "conversation_id": turn.conversation_id,
                                "trace_id": turn.trace_id,
                            },
                        )
                    )
                    .mappings()
                    .one()
                )
                details: dict[str, Any] = {}
                if message_result.rowcount != 1:
                    details["message_status"] = state["message_status"]
                if trace_result.rowcount != 1:
                    details["trace_status"] = state["trace_status"]
                raise ApiError("TURN_STATE_CONFLICT", "问答轮次状态已变化", 409, details)
            await connection.execute(
                text(
                    "UPDATE conversation SET updated_at = now(), last_activity_at = now() "
                    "WHERE id = :id"
                ),
                {"id": turn.conversation_id},
            )

    async def fail_turn(
        self,
        turn: Turn,
        *,
        status: str,
        code: str,
        message: str,
        partial_answer: str,
        timings: dict[str, int],
        stage: str | None = None,
        retryable: bool | None = None,
        detail: str | None = None,
    ) -> None:
        message_status = "CANCELLED" if status == "CANCELLED" else "FAILED"
        error: dict[str, Any] = {"code": code, "message": message}
        if stage is not None:
            error["stage"] = stage
        if retryable is not None:
            error["retryable"] = retryable
        if detail is not None:
            error["detail"] = detail
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE message SET content = :answer, status = :status, updated_at = now()
                    WHERE id = :message_id AND status = 'STREAMING'
                    """
                ),
                {
                    "message_id": turn.assistant_message_id,
                    "answer": partial_answer,
                    "status": message_status,
                },
            )
            await connection.execute(
                text(
                    """
                    UPDATE rag_trace SET answer = :answer, error = CAST(:error AS jsonb),
                        latency = COALESCE(latency, '{}'::jsonb) || CAST(:latency AS jsonb),
                        status = :status, finished_at = now()
                    WHERE trace_id = :trace_id AND status = 'RUNNING'
                    """
                ),
                {
                    "trace_id": turn.trace_id,
                    "answer": partial_answer,
                    "error": json.dumps(error, ensure_ascii=False),
                    "latency": json.dumps(timings),
                    "status": status,
                },
            )

    async def get_document_source(self, document_id: UUID) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, kb_id AS knowledge_id, filename, display_name,
                           minio_bucket, minio_object_key, file_size, status
                    FROM document_source
                    WHERE id = :document_id AND status = :status
                    """
                ),
                {"document_id": document_id, "status": "STORED"},
            )
            row = result.mappings().one_or_none()
        if row is None:
            raise ApiError("DOCUMENT_NOT_FOUND", "文档不存在或已删除", 404)
        return dict(row)

    async def get_knowledge_base_summary(
        self,
        knowledge_id: UUID,
        *,
        enabled_only: bool = False,
        assistant_online_only: bool = False,
    ) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, name, description, status,
                           COALESCE(rag_active_index_id, active_index_id) AS active_index_id
                    FROM knowledge_base
                    WHERE id = :kb_id
                      AND (:enabled_only = FALSE OR status = 'ENABLED')
                      AND (:assistant_online_only = FALSE OR assistant_online)
                    """
                ),
                {
                    "kb_id": knowledge_id,
                    "enabled_only": enabled_only,
                    "assistant_online_only": assistant_online_only,
                },
            )
            row = result.mappings().one_or_none()
        if row is None:
            raise ApiError("KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在", 404)
        return {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "description": row["description"],
            "status": str(row["status"]),
            "active_index_id": (str(row["active_index_id"]) if row["active_index_id"] else None),
        }

    async def list_kb_documents(self, knowledge_id: UUID) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, filename, display_name
                    FROM document_source
                    WHERE kb_id = :kb_id AND status = 'STORED'
                    ORDER BY created_at
                    """
                ),
                {"kb_id": knowledge_id},
            )
            rows = result.mappings().all()
        return [
            {
                "document_id": str(row["id"]),
                "title": str(row["display_name"] or row["filename"]),
            }
            for row in rows
        ]

    async def get_document_blocks(
        self, *, knowledge_id: UUID, index_id: UUID, document_id: UUID
    ) -> dict[str, Any]:
        """Ordered document elements with image assets for delivery / doc-QA."""
        async with self.engine.connect() as connection:
            doc_result = await connection.execute(
                text(
                    """
                    SELECT id, filename, display_name
                    FROM document_source
                    WHERE id = :document_id AND kb_id = :kb_id AND status = 'STORED'
                    """
                ),
                {
                    "document_id": document_id,
                    "kb_id": knowledge_id,
                },
            )
            doc_row = doc_result.mappings().one_or_none()
            if doc_row is None:
                raise ApiError("DOCUMENT_NOT_FOUND", "文档不存在或已删除", 404)
            element_result = await connection.execute(
                text(
                    """
                    SELECT e.id, e.element_type, e.content, e.section_path,
                           a.id AS asset_id, a.ocr_text, a.vision_caption,
                           a.width AS asset_width, a.height AS asset_height
                    FROM document_element e
                    LEFT JOIN image_asset a ON a.element_id = e.id
                    WHERE e.index_id = :index_id AND e.document_id = :document_id
                    ORDER BY e.sequence_no
                    """
                ),
                {"index_id": index_id, "document_id": document_id},
            )
            rows = element_result.mappings().all()
        return {
            "document_id": str(doc_row["id"]),
            "title": str(doc_row["display_name"] or doc_row["filename"]),
            "elements": elements_from_rows(rows),
        }

    async def get_trace(self, trace_id: UUID) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text("SELECT * FROM rag_trace WHERE trace_id = :trace_id"),
                {"trace_id": trace_id},
            )
            row = result.mappings().one_or_none()
        if row is None:
            raise ApiError("TRACE_NOT_FOUND", "RAG Trace 不存在", 404)
        return dict(row)

    async def list_traces(
        self,
        *,
        knowledge_id: UUID | None = None,
        status: str | None = None,
        mode: str | None = None,
        user_id: str | None = None,
        response_type: str | None = None,
        engine: str | None = None,
        source: str | None = None,
        error_code: str | None = None,
        conversation_id: UUID | None = None,
        request_id: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses = ["1 = 1"]
        parameters: dict[str, Any] = {"limit": limit}
        if knowledge_id is not None:
            clauses.append("kb_id = :kb_id")
            parameters["kb_id"] = knowledge_id
        if status is not None:
            clauses.append("status = :status")
            parameters["status"] = status
        if mode is not None:
            clauses.append("mode = :mode")
            parameters["mode"] = mode
        if user_id is not None:
            clauses.append("user_id = :user_id")
            parameters["user_id"] = user_id
        if response_type is not None:
            clauses.append("response_type = :response_type")
            parameters["response_type"] = response_type
        if engine is not None:
            clauses.append("engine = :engine")
            parameters["engine"] = engine
        if source is not None:
            clauses.append("source = :source")
            parameters["source"] = source
        if error_code is not None:
            clauses.append("error ->> 'code' = :error_code")
            parameters["error_code"] = error_code
        if conversation_id is not None:
            clauses.append("conversation_id = :conversation_id")
            parameters["conversation_id"] = conversation_id
        if request_id is not None:
            clauses.append("request_id = :request_id")
            parameters["request_id"] = request_id
        if created_from is not None:
            clauses.append("created_at >= :created_from")
            parameters["created_from"] = created_from
        if created_to is not None:
            clauses.append("created_at < :created_to")
            parameters["created_to"] = created_to
        query = f"""
            SELECT trace_id, request_id, source, mode, kb_id, index_id, question, status,
                   user_id, response_type, answer_mode, latency, error,
                   created_at, finished_at, engine, stage, parent_trace_id,
                   fallback_from, fallback_reason
            FROM rag_trace
            WHERE {" AND ".join(clauses)}
            ORDER BY created_at DESC
            LIMIT :limit
        """
        async with self.engine.connect() as connection:
            result = await connection.execute(text(query), parameters)
            return [dict(row) for row in result.mappings()]

    def _environment_key_for(self, model_type: str, provider: str) -> Any:
        return environment_api_key(self.settings, model_type, provider)

    def _api_key_configured(
        self, model_type: str, provider: str, stored_key_configured: bool
    ) -> bool:
        environment_key = self._environment_key_for(model_type, provider)
        return stored_key_configured or (
            environment_key is not None and bool(environment_key.get_secret_value().strip())
        )

    async def list_models(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, name, model_type, provider, base_url, model_name,
                           parameters, enabled,
                           (api_key_ciphertext IS NOT NULL) AS stored_key_configured,
                           created_at, updated_at
                    FROM model_config
                    ORDER BY model_type, name
                    """
                )
            )
            rows = []
            for raw in result.mappings():
                row = dict(raw)
                model_type = str(row["model_type"])
                provider = str(row["provider"])
                stored_key = bool(row.pop("stored_key_configured"))
                row["api_key_configured"] = self._api_key_configured(
                    model_type, provider, stored_key
                )
                row["api_key_source"] = self._api_key_source(model_type, provider, stored_key)
                row["supported_providers"] = list(supported_providers(model_type))
                rows.append(row)
            return rows

    async def _record_history(
        self,
        connection: Any,
        model_id: UUID,
        action: str,
        changed_fields: list[str],
        actor: str,
    ) -> None:
        await connection.execute(
            text(
                """
                INSERT INTO model_config_history (
                    id, model_config_id, action, changed_fields, actor
                ) VALUES (
                    :id, :model_config_id, :action, CAST(:changed_fields AS jsonb), :actor
                )
                """
            ),
            {
                "id": uuid4(),
                "model_config_id": model_id,
                "action": action,
                "changed_fields": json.dumps(changed_fields),
                "actor": actor[:120],
            },
        )

    _MODEL_ROW_SELECT = (
        "SELECT id, name, model_type, provider, base_url, model_name, parameters,"
        " enabled, api_key_ciphertext, api_key_nonce, api_key_key_version,"
        " created_at, updated_at FROM model_config"
    )

    def _public_model_row(self, row: dict[str, Any]) -> dict[str, Any]:
        model_type = str(row["model_type"])
        provider = str(row["provider"])
        stored_key = row.get("api_key_ciphertext") is not None
        public = {key: value for key, value in row.items() if not key.startswith("api_key_")}
        public["api_key_configured"] = self._api_key_configured(model_type, provider, stored_key)
        public["api_key_source"] = self._api_key_source(model_type, provider, stored_key)
        public["supported_providers"] = list(supported_providers(model_type))
        return public

    async def set_api_key(self, model_id: UUID, api_key: str, *, actor: str) -> dict[str, Any]:
        normalized = api_key.strip()
        if not normalized:
            raise ApiError("VALIDATION_ERROR", "API 密钥不能为空", 422)
        if len(normalized) > 4096:
            raise ApiError("VALIDATION_ERROR", "API 密钥超过长度限制", 422)
        box = self._require_secret_box()
        ciphertext, nonce = box.encrypt(normalized)
        async with self.engine.begin() as connection:
            updated = await connection.execute(
                text(
                    "UPDATE model_config SET api_key_ciphertext = :ciphertext,"
                    " api_key_nonce = :nonce, api_key_key_version = :key_version,"
                    " updated_at = now() WHERE id = :id"
                    " RETURNING id, name, model_type, provider, base_url, model_name,"
                    " parameters, enabled, api_key_ciphertext, api_key_nonce,"
                    " api_key_key_version, created_at, updated_at"
                ),
                {
                    "ciphertext": ciphertext,
                    "nonce": nonce,
                    "key_version": box.version,
                    "id": model_id,
                },
            )
            row = updated.mappings().one_or_none()
            if row is None:
                raise ApiError("MODEL_NOT_FOUND", "模型配置不存在", 404)
            await self._record_history(connection, model_id, "SET_API_KEY", ["api_key"], actor)
            if str(row["model_type"]) in self._BUILD_BOUND_MODEL_TYPES:
                await connection.execute(
                    text("UPDATE knowledge_base SET rebuild_required = true, updated_at = now()")
                )
            return self._public_model_row(dict(row))

    async def clear_api_key(self, model_id: UUID, *, actor: str) -> dict[str, Any]:
        async with self.engine.begin() as connection:
            existing = await connection.execute(
                text("SELECT api_key_ciphertext FROM model_config WHERE id = :id"),
                {"id": model_id},
            )
            row = existing.mappings().one_or_none()
            if row is None:
                raise ApiError("MODEL_NOT_FOUND", "模型配置不存在", 404)
            had_key = row["api_key_ciphertext"] is not None
            updated = await connection.execute(
                text(
                    "UPDATE model_config SET api_key_ciphertext = NULL,"
                    " api_key_nonce = NULL, api_key_key_version = NULL, updated_at = now()"
                    " WHERE id = :id"
                    " RETURNING id, name, model_type, provider, base_url, model_name,"
                    " parameters, enabled, api_key_ciphertext, api_key_nonce,"
                    " api_key_key_version, created_at, updated_at"
                ),
                {"id": model_id},
            )
            result = dict(updated.mappings().one())
            if had_key:
                await self._record_history(
                    connection, model_id, "CLEAR_API_KEY", ["api_key"], actor
                )
                if str(result["model_type"]) in self._BUILD_BOUND_MODEL_TYPES:
                    await connection.execute(
                        text(
                            "UPDATE knowledge_base SET rebuild_required = true, updated_at = now()"
                        )
                    )
            return self._public_model_row(result)

    async def read_model_with_key(self, model_id: UUID) -> dict[str, Any]:
        """Internal: full row plus the decrypted key. Never return via API."""
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(self._MODEL_ROW_SELECT + " WHERE id = :id"),
                {"id": model_id},
            )
            row = result.mappings().one_or_none()
            if row is None:
                raise ApiError("MODEL_NOT_FOUND", "模型配置不存在", 404)
            data = dict(row)
            data["api_key"] = self._decrypt_api_key(data)
            return data

    # All five model types are editable online. Per-turn consumers
    # (LLM/RERANK) take effect on the next chat request; build-bound types
    # (EMBEDDING/OCR/VISION) on the next index build. EMBEDDING edits that
    # change model_name/base_url additionally pass the dimension gate at the
    # API layer (see docs/api-contract.md §9).
    # Build-bound types: config changes only affect the NEXT index build, so
    # knowledge bases must be flagged for rebuild.
    _BUILD_BOUND_MODEL_TYPES = ("EMBEDDING", "OCR", "VISION")

    async def update_model_config(
        self,
        model_id: UUID,
        *,
        provider: str | None = None,
        model_name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        parameters: dict[str, Any] | None = None,
        enabled: bool | None = None,
        actor: str = "system",
    ) -> dict[str, Any]:
        """Admin write path for runtime model settings (LLM / RERANK only).

        begin_turn snapshots provider/model_name/parameters/base_url every
        turn, so committed changes take effect on the next request without
        restarts. Embedding/OCR/VISION stay env-managed: they are bound to
        index builds, not to the chat turn.
        """
        async with self.engine.begin() as connection:
            existing_result = await connection.execute(
                text(
                    """
                    SELECT id, model_type FROM model_config
                    WHERE id = :id FOR UPDATE
                    """
                ),
                {"id": model_id},
            )
            existing = existing_result.mappings().one_or_none()
            if existing is None:
                raise ApiError("MODEL_NOT_FOUND", "模型配置不存在", 404)
            model_type = str(existing["model_type"])
            assignments: list[str] = []
            params: dict[str, Any] = {"id": model_id}
            changed_fields: list[str] = []
            if provider is not None:
                assignments.append("provider = :provider")
                params["provider"] = provider
                changed_fields.append("provider")
            if model_name is not None:
                assignments.append("model_name = :model_name")
                params["model_name"] = model_name
                changed_fields.append("model_name")
            if base_url is not None:
                assignments.append("base_url = :base_url")
                params["base_url"] = base_url
                changed_fields.append("base_url")
            if api_key is not None:
                box = self._require_secret_box()
                ciphertext, nonce = box.encrypt(api_key)
                assignments.extend(
                    [
                        "api_key_ciphertext = :api_key_ciphertext",
                        "api_key_nonce = :api_key_nonce",
                        "api_key_key_version = :api_key_key_version",
                    ]
                )
                params.update(
                    {
                        "api_key_ciphertext": ciphertext,
                        "api_key_nonce": nonce,
                        "api_key_key_version": box.version,
                    }
                )
                changed_fields.append("api_key")
            if parameters is not None:
                assignments.append("parameters = CAST(:parameters AS jsonb)")
                params["parameters"] = json.dumps(parameters, ensure_ascii=False)
                changed_fields.append("parameters")
            if enabled is not None:
                assignments.append("enabled = :enabled")
                params["enabled"] = enabled
                changed_fields.append("enabled")
            assignments.append("updated_at = now()")
            try:
                updated_result = await connection.execute(
                    text(
                        "UPDATE model_config SET "
                        + ", ".join(assignments)
                        + " WHERE id = :id RETURNING id, name, model_type, provider,"
                        " base_url, model_name, parameters, enabled,"
                        " api_key_ciphertext, api_key_nonce, api_key_key_version,"
                        " created_at, updated_at"
                    ),
                    params,
                )
            except IntegrityError as exc:
                raise ApiError(
                    "MODEL_UPDATE_CONFLICT", "同一模型类型只能启用一个配置", 409
                ) from exc
            await self._record_history(connection, model_id, "UPDATE_CONFIG", changed_fields, actor)
            if model_type in self._BUILD_BOUND_MODEL_TYPES and changed_fields:
                # Config changes only take effect on the next build; flag
                # every knowledge base so the console surfaces the drift.
                await connection.execute(
                    text("UPDATE knowledge_base SET rebuild_required = true, updated_at = now()")
                )
            row = dict(updated_result.mappings().one())
        return self._public_model_row(row)

    _RAG_CONFIG_FIELDS = (
        "vector_top_k",
        "bm25_top_k",
        "fusion_top_k",
        "rerank_top_n",
        "context_max_chunks",
    )

    def _rag_config_defaults(self) -> dict[str, Any]:
        return {
            "vector_top_k": self.settings.m3_vector_top_k,
            "bm25_top_k": self.settings.m5_bm25_top_k,
            "fusion_top_k": self.settings.m5_fusion_top_k,
            "rerank_top_n": self.settings.m5_rerank_top_n,
            "context_max_chunks": self.settings.m3_context_max_chunks,
            "updated_at": None,
        }

    async def get_rag_config(self) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT vector_top_k, bm25_top_k, fusion_top_k, rerank_top_n,
                           context_max_chunks, updated_at
                    FROM rag_config WHERE id
                    """
                )
            )
            row = result.mappings().one_or_none()
        return dict(row) if row is not None else self._rag_config_defaults()

    async def get_engine_retrieval_config(self, execution_policy: str) -> RetrievalConfig:
        async with self.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """SELECT vector_top_k, bm25_top_k, fusion_top_k, rerank_top_n,
                                  context_max_chunks
                           FROM engine_retrieval_config WHERE execution_policy = :policy"""
                        ),
                        {"policy": execution_policy},
                    )
                )
                .mappings()
                .one_or_none()
            )
        data = dict(row) if row else self._rag_config_defaults()
        return RetrievalConfig(**{key: int(data[key]) for key in self._RAG_CONFIG_FIELDS})

    async def list_engine_retrieval_configs(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            """SELECT execution_policy, vector_top_k, bm25_top_k, fusion_top_k,
                                  rerank_top_n, context_max_chunks, updated_at
                           FROM engine_retrieval_config ORDER BY execution_policy"""
                        )
                    )
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]

    async def update_engine_retrieval_config(
        self, execution_policy: str, **values: int | None
    ) -> dict[str, Any]:
        allowed = {
            key: value
            for key, value in values.items()
            if key in self._RAG_CONFIG_FIELDS and value is not None
        }
        if not allowed:
            raise ApiError("VALIDATION_ERROR", "没有需要更新的字段", 422)
        assignments = ", ".join(f"{key} = :{key}" for key in allowed)
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO engine_retrieval_config (execution_policy) VALUES (:policy) "
                    "ON CONFLICT (execution_policy) DO NOTHING"
                ),
                {"policy": execution_policy},
            )
            result = await connection.execute(
                text(
                    "UPDATE engine_retrieval_config SET "
                    + assignments
                    + ", updated_at = now() WHERE execution_policy = :policy "
                    "RETURNING execution_policy, vector_top_k, bm25_top_k, fusion_top_k, "
                    "rerank_top_n, context_max_chunks, updated_at"
                ),
                {"policy": execution_policy, **allowed},
            )
            row = result.mappings().one()
        return dict(row)

    async def update_rag_config(
        self,
        *,
        vector_top_k: int | None = None,
        bm25_top_k: int | None = None,
        fusion_top_k: int | None = None,
        rerank_top_n: int | None = None,
        context_max_chunks: int | None = None,
    ) -> dict[str, Any]:
        values = {
            "vector_top_k": vector_top_k,
            "bm25_top_k": bm25_top_k,
            "fusion_top_k": fusion_top_k,
            "rerank_top_n": rerank_top_n,
            "context_max_chunks": context_max_chunks,
        }
        assignments = [
            f"{field} = :{field}" for field, value in values.items() if value is not None
        ]
        if not assignments:
            raise ApiError("VALIDATION_ERROR", "没有需要更新的字段", 422)
        params: dict[str, Any] = {
            field: value for field, value in values.items() if value is not None
        }
        async with self.engine.begin() as connection:
            # Seed the singleton row on first write in case it was removed.
            await connection.execute(
                text("INSERT INTO rag_config (id) VALUES (true) ON CONFLICT (id) DO NOTHING")
            )
            result = await connection.execute(
                text(
                    "UPDATE rag_config SET "
                    + ", ".join(assignments)
                    + ", updated_at = now() WHERE id RETURNING vector_top_k, bm25_top_k,"
                    " fusion_top_k, rerank_top_n, context_max_chunks, updated_at"
                ),
                params,
            )
            row = result.mappings().one_or_none()
        return dict(row) if row is not None else self._rag_config_defaults()

    async def list_prompts(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, name, version, content, active, created_at, updated_at
                    FROM prompt_template ORDER BY name, version DESC
                    """
                )
            )
            return [dict(row) for row in result.mappings()]
