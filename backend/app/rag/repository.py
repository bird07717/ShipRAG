from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.common.errors import ApiError
from app.common.text import estimate_tokens
from app.core.config import Settings
from app.core.secrets import ModelSecretBox
from app.rag.models import ModelSnapshot, RetrievalCandidate, RetrievalConfig, Turn


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

    def _api_key_source(self, model_type: str, stored_key: bool) -> str | None:
        if stored_key:
            return "DATABASE"
        environment_key = self._environment_key_for(model_type)
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
    ) -> Turn:
        trace_id = uuid4()
        user_message_id = uuid4()
        assistant_message_id = uuid4()
        async with self.engine.begin() as connection:
            kb_result = await connection.execute(
                text(
                    """
                    SELECT kb.status AS kb_status, kb.active_index_id,
                           i.status AS index_status, i.kb_id AS index_kb_id,
                           i.embedding_model_name
                    FROM knowledge_base kb
                    LEFT JOIN knowledge_index i ON i.id = kb.active_index_id
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
            if kb["active_index_id"] is None:
                raise ApiError("KNOWLEDGE_BASE_NOT_READY", "知识库尚无 Active Index", 409)
            if kb["index_status"] != "ACTIVE" or kb["index_kb_id"] != knowledge_id:
                raise ApiError("INDEX_NOT_READY", "知识库 Active Index 状态异常", 409)

            resolved_conversation_id = conversation_id or uuid4()
            chat_context: dict[str, Any] = {}
            focus_document_id: UUID | None = None
            if conversation_id is None:
                await connection.execute(
                    text(
                        """
                        INSERT INTO conversation (id, knowledge_id)
                        VALUES (:id, :knowledge_id)
                        """
                    ),
                    {"id": resolved_conversation_id, "knowledge_id": knowledge_id},
                )
            else:
                conversation_result = await connection.execute(
                    text(
                        """
                        SELECT knowledge_id, focus_document_id, chat_context
                        FROM conversation
                        WHERE id = :id FOR UPDATE
                        """
                    ),
                    {"id": conversation_id},
                )
                conversation_row = conversation_result.mappings().one_or_none()
                if conversation_row is None:
                    raise ApiError("CONVERSATION_NOT_FOUND", "会话不存在", 404)
                if conversation_row["knowledge_id"] != knowledge_id:
                    raise ApiError("CONVERSATION_KB_MISMATCH", "会话不属于指定知识库", 409)
                focus_document_id = (
                    UUID(str(conversation_row["focus_document_id"]))
                    if conversation_row["focus_document_id"]
                    else None
                )
                raw_context = conversation_row["chat_context"]
                if isinstance(raw_context, dict):
                    chat_context = dict(raw_context)

            history_result = await connection.execute(
                text(
                    """
                    SELECT role, content FROM (
                        SELECT role, content, sequence_no
                        FROM message
                        WHERE conversation_id = :conversation_id AND status = 'COMPLETED'
                        ORDER BY sequence_no DESC
                        LIMIT :history_limit
                    ) recent
                    ORDER BY sequence_no
                    """
                ),
                {
                    "conversation_id": resolved_conversation_id,
                    "history_limit": self.settings.m3_history_max_messages,
                },
            )
            history = self._budget_history([dict(row) for row in history_result.mappings()])

            prompt_result = await connection.execute(
                text("SELECT content FROM prompt_template WHERE active")
            )
            prompt_template = prompt_result.scalar_one_or_none()
            if prompt_template is None:
                raise ApiError("MODEL_NOT_CONFIGURED", "Active Prompt 未配置", 503)

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
                        id, conversation_id, sequence_no, role, content, status
                    ) VALUES (
                        :user_id, :conversation_id, :user_sequence,
                        'USER', :question, 'COMPLETED'
                    ), (
                        :assistant_id, :conversation_id, :assistant_sequence,
                        'ASSISTANT', '', 'STREAMING'
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
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_trace (
                        id, trace_id, request_id, kb_id, index_id, conversation_id,
                        message_id, question, mode, status, user_id
                    ) VALUES (
                        :id, :trace_id, :request_id, :kb_id, :index_id, :conversation_id,
                        :message_id, :question, :mode, 'RUNNING', :user_id
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "trace_id": trace_id,
                    "request_id": request_id,
                    "kb_id": knowledge_id,
                    "index_id": kb["active_index_id"],
                    "conversation_id": resolved_conversation_id,
                    "message_id": assistant_message_id,
                    "question": question,
                    "mode": mode,
                    "user_id": user_id,
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
            index_id=UUID(str(kb["active_index_id"])),
            embedding_model_name=str(kb["embedding_model_name"]),
            prompt_template=str(prompt_template),
            llm=ModelSnapshot(
                provider=str(llm_row["provider"]),
                model_name=str(llm_row["model_name"]),
                parameters=dict(parameters),
                base_url=(str(llm_row["base_url"]) if llm_row["base_url"] is not None else None),
                api_key=self._decrypt_api_key(dict(llm_row)),
            ),
            history=history,
            rerank=ModelSnapshot(
                provider=str(rerank_row["provider"]),
                model_name=str(rerank_row["model_name"]),
                parameters=dict(rerank_parameters),
                base_url=(
                    str(rerank_row["base_url"]) if rerank_row["base_url"] is not None else None
                ),
                api_key=self._decrypt_api_key(dict(rerank_row)),
            ),
            focus_document_id=focus_document_id,
            chat_context=chat_context,
            retrieval=retrieval,
        )

    def _budget_history(self, messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        # Take-then-stop: oversized leading messages (usually the newest, e.g. a
        # very long assistant answer) are skipped, but once filling starts the
        # first overflow stops selection. This keeps the retained window both
        # as recent as possible and contiguous — a skipped middle message would
        # break reference/ellipsis chains in the rendered history.
        selected: list[dict[str, str]] = []
        remaining = self.settings.m3_history_token_budget
        for item in reversed(messages):
            content = str(item["content"])
            estimate = max(1, estimate_tokens(content))
            if estimate > remaining:
                if selected:
                    break
                continue
            remaining -= estimate
            selected.append({"role": str(item["role"]), "content": content})
        selected.reverse()
        return selected

    async def vector_search(
        self,
        *,
        knowledge_id: UUID,
        index_id: UUID,
        embedding: list[float],
        limit: int,
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
                    ORDER BY c.embedding <=> CAST(:embedding AS vector), c.id
                    LIMIT :limit
                    """
                ),
                {
                    "embedding": vector_literal,
                    "kb_id": knowledge_id,
                    "index_id": index_id,
                    "limit": limit,
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
    ) -> list[RetrievalCandidate]:
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
                    ORDER BY pdb.score(document_chunk.id) DESC, document_chunk.id
                    LIMIT :limit
                    """
                ),
                {
                    "query": query,
                    "kb_id": knowledge_id,
                    "index_id": index_id,
                    "limit": limit,
                },
            )
            rows = list(result.mappings())
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

    async def get_chunks_by_ids(
        self, *, index_id: UUID, chunk_ids: list[UUID]
    ) -> list[RetrievalCandidate]:
        if not chunk_ids:
            return []
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
                           ) AS image_asset_ids
                    FROM document_chunk c
                    JOIN document_source d ON d.id = c.document_id
                    WHERE c.index_id = :index_id AND c.id = ANY(CAST(:chunk_ids AS uuid[]))
                    """
                ),
                {"index_id": index_id, "chunk_ids": chunk_ids},
            )
            rows = list(result.mappings())
        order = {chunk_id: position for position, chunk_id in enumerate(chunk_ids)}
        candidates = [
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
                rank=order[UUID(str(row["id"]))] + 1,
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
            )
            for row in rows
        ]
        return sorted(candidates, key=lambda item: order[item.chunk_id])

    async def get_rerank_image_assets(
        self, *, index_id: UUID, image_asset_ids: list[UUID]
    ) -> dict[UUID, dict[str, Any]]:
        if not image_asset_ids:
            return {}
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, minio_bucket, minio_object_key, mime_type
                    FROM image_asset
                    WHERE index_id = :index_id AND id = ANY(CAST(:ids AS uuid[]))
                    """
                ),
                {"index_id": index_id, "ids": image_asset_ids},
            )
            return {UUID(str(row["id"])): dict(row) for row in result.mappings()}

    async def save_prepared(
        self,
        turn: Turn,
        *,
        vector_candidates: list[RetrievalCandidate],
        bm25_candidates: list[RetrievalCandidate],
        fusion_candidates: list[RetrievalCandidate],
        rerank_candidates: list[RetrievalCandidate],
        rerank_result: dict[str, Any],
        selected_context: list[dict[str, Any]],
        prompt: str,
        timings: dict[str, int],
    ) -> None:
        retrieval_result = {
            "mode": "HYBRID",
            "embedding_model": turn.embedding_model_name,
            "vector_candidates": [item.trace_dict() for item in vector_candidates],
            "lexical_engine": "pg_search",
            "lexical_engine_version": "0.25.0",
            "bm25_candidates": [item.trace_dict() for item in bm25_candidates],
            "fusion_method": "RRF",
            "rrf_k": self.settings.m5_rrf_k,
            "fusion_candidates": [item.trace_dict() for item in fusion_candidates],
        }
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE rag_trace
                    SET retrieval_result = CAST(:retrieval AS jsonb),
                        selected_context = CAST(:context AS jsonb),
                        rerank_result = CAST(:rerank AS jsonb),
                        prompt = :prompt,
                        latency = CAST(:latency AS jsonb)
                    WHERE trace_id = :trace_id AND status = 'RUNNING'
                    """
                ),
                {
                    "trace_id": turn.trace_id,
                    "retrieval": json.dumps(retrieval_result, ensure_ascii=False),
                    "context": json.dumps(selected_context, ensure_ascii=False),
                    "rerank": json.dumps(
                        {
                            **rerank_result,
                            "candidates": [item.trace_dict() for item in rerank_candidates],
                        },
                        ensure_ascii=False,
                    ),
                    "prompt": prompt if self.settings.m3_trace_retain_prompt else None,
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
        prompt: str | None = None,
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
        prompt_patch = ""
        if prompt is not None:
            prompt_patch = ", prompt = :prompt"
            routing_params["prompt"] = prompt if self.settings.m3_trace_retain_prompt else None
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE message SET content = :answer, sources = CAST(:sources AS jsonb),
                        status = 'COMPLETED', updated_at = now()
                    WHERE id = :message_id AND status = 'STREAMING'
                    """
                ),
                {
                    "message_id": turn.assistant_message_id,
                    "answer": answer,
                    "sources": json.dumps(sources, ensure_ascii=False),
                },
            )
            await connection.execute(
                text(
                    """
                    UPDATE rag_trace SET answer = :answer, sources = CAST(:sources AS jsonb),
                        model_usage = CAST(:usage AS jsonb), latency = CAST(:latency AS jsonb),
                        citation_result = CAST(:citation_result AS jsonb),
                        response_type = :response_type, answer_mode = :answer_mode,
                        status = 'COMPLETED', finished_at = now()
                    """
                    + routing_patch
                    + prompt_patch
                    + """
                    WHERE trace_id = :trace_id AND status = 'RUNNING'
                    """
                ),
                {
                    "trace_id": turn.trace_id,
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
            await connection.execute(
                text("UPDATE conversation SET updated_at = now() WHERE id = :id"),
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
                        latency = CAST(:latency AS jsonb), status = :status, finished_at = now()
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

    async def set_conversation_focus(
        self, conversation_id: UUID, document_id: UUID, index_id: UUID
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE conversation
                    SET focus_document_id = :document_id,
                        focus_index_id = :index_id,
                        chat_context = '{}'::jsonb,
                        updated_at = now()
                    WHERE id = :conversation_id
                    """
                ),
                {
                    "conversation_id": conversation_id,
                    "document_id": document_id,
                    "index_id": index_id,
                },
            )

    async def clear_conversation_focus(self, conversation_id: UUID) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE conversation
                    SET focus_document_id = NULL,
                        focus_index_id = NULL,
                        chat_context = '{}'::jsonb,
                        updated_at = now()
                    WHERE id = :conversation_id
                    """
                ),
                {"conversation_id": conversation_id},
            )

    async def set_conversation_pending(
        self,
        conversation_id: UUID,
        *,
        pending_options: list[dict[str, Any]],
        pending_query: str,
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE conversation
                    SET chat_context = CAST(:chat_context AS jsonb),
                        updated_at = now()
                    WHERE id = :conversation_id
                    """
                ),
                {
                    "conversation_id": conversation_id,
                    "chat_context": json.dumps(
                        {
                            "pending_options": pending_options,
                            "pending_query": pending_query,
                        },
                        ensure_ascii=False,
                    ),
                },
            )

    async def clear_conversation_pending(self, conversation_id: UUID) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE conversation
                    SET chat_context = '{}'::jsonb, updated_at = now()
                    WHERE id = :conversation_id
                    """
                ),
                {"conversation_id": conversation_id},
            )

    async def get_knowledge_base_summary(self, knowledge_id: UUID) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, name, description, status, active_index_id
                    FROM knowledge_base WHERE id = :kb_id
                    """
                ),
                {"kb_id": knowledge_id},
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
                           a.id AS asset_id, a.ocr_text, a.vision_caption
                    FROM document_element e
                    LEFT JOIN image_asset a ON a.element_id = e.id
                    WHERE e.index_id = :index_id AND e.document_id = :document_id
                    ORDER BY e.sequence_no
                    """
                ),
                {"index_id": index_id, "document_id": document_id},
            )
            rows = element_result.mappings().all()
        elements: list[dict[str, Any]] = []
        for row in rows:
            asset_id = UUID(str(row["asset_id"])) if row["asset_id"] else None
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
        return {
            "document_id": str(doc_row["id"]),
            "title": str(doc_row["display_name"] or doc_row["filename"]),
            "elements": elements,
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
        error_code: str | None = None,
        conversation_id: UUID | None = None,
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
        if error_code is not None:
            clauses.append("error ->> 'code' = :error_code")
            parameters["error_code"] = error_code
        if conversation_id is not None:
            clauses.append("conversation_id = :conversation_id")
            parameters["conversation_id"] = conversation_id
        if created_from is not None:
            clauses.append("created_at >= :created_from")
            parameters["created_from"] = created_from
        if created_to is not None:
            clauses.append("created_at < :created_to")
            parameters["created_to"] = created_to
        query = f"""
            SELECT trace_id, request_id, mode, kb_id, index_id, question, status,
                   user_id, response_type, answer_mode, latency, error,
                   created_at, finished_at
            FROM rag_trace
            WHERE {" AND ".join(clauses)}
            ORDER BY created_at DESC
            LIMIT :limit
        """
        async with self.engine.connect() as connection:
            result = await connection.execute(text(query), parameters)
            return [dict(row) for row in result.mappings()]

    def _environment_key_for(self, model_type: str) -> Any:
        return {
            "LLM": self.settings.zhipu_api_key,
            "VISION": self.settings.zhipu_api_key,
            "EMBEDDING": self.settings.siliconflow_api_key,
            "RERANK": self.settings.siliconflow_api_key,
            "OCR": self.settings.siliconflow_api_key,
        }.get(model_type)

    def _api_key_configured(self, model_type: str, stored_key_configured: bool) -> bool:
        environment_key = self._environment_key_for(model_type)
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
                stored_key = bool(row.pop("stored_key_configured"))
                row["api_key_configured"] = self._api_key_configured(model_type, stored_key)
                row["api_key_source"] = self._api_key_source(model_type, stored_key)
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
        stored_key = row.get("api_key_ciphertext") is not None
        public = {key: value for key, value in row.items() if not key.startswith("api_key_")}
        public["api_key_configured"] = self._api_key_configured(model_type, stored_key)
        public["api_key_source"] = self._api_key_source(model_type, stored_key)
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
        model_name: str | None = None,
        base_url: str | None = None,
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
            if model_name is not None:
                assignments.append("model_name = :model_name")
                params["model_name"] = model_name
                changed_fields.append("model_name")
            if base_url is not None:
                assignments.append("base_url = :base_url")
                params["base_url"] = base_url
                changed_fields.append("base_url")
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

    async def list_messages(self, conversation_id: UUID) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            exists = await connection.execute(
                text("SELECT 1 FROM conversation WHERE id = :id"), {"id": conversation_id}
            )
            if exists.scalar_one_or_none() is None:
                raise ApiError("CONVERSATION_NOT_FOUND", "会话不存在", 404)
            result = await connection.execute(
                text(
                    """
                    SELECT id, conversation_id, role, content, sources, status,
                           content_blocks, response_type, support_mode,
                           created_at, updated_at
                    FROM message WHERE conversation_id = :id
                    ORDER BY sequence_no
                    """
                ),
                {"id": conversation_id},
            )
            return [dict(row) for row in result.mappings()]
