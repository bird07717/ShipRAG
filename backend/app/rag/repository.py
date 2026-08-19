from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.common.errors import ApiError
from app.core.config import Settings
from app.rag.models import ModelSnapshot, RetrievalCandidate, Turn


class RagRepository:
    def __init__(self, engine: AsyncEngine, settings: Settings) -> None:
        self.engine = engine
        self.settings = settings

    async def begin_turn(
        self,
        *,
        knowledge_id: UUID,
        conversation_id: UUID | None,
        question: str,
        request_id: str,
        mode: str = "CHAT",
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
                        SELECT knowledge_id FROM conversation
                        WHERE id = :id FOR UPDATE
                        """
                    ),
                    {"id": conversation_id},
                )
                conversation_kb = conversation_result.scalar_one_or_none()
                if conversation_kb is None:
                    raise ApiError("CONVERSATION_NOT_FOUND", "会话不存在", 404)
                if conversation_kb != knowledge_id:
                    raise ApiError("CONVERSATION_KB_MISMATCH", "会话不属于指定知识库", 409)

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
                    SELECT provider, model_name, parameters
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
                    SELECT provider, model_name, parameters
                    FROM model_config WHERE model_type = 'RERANK' AND enabled
                    """
                )
            )
            rerank_row = rerank_result.mappings().one_or_none()
            if rerank_row is None:
                raise ApiError("MODEL_NOT_CONFIGURED", "Rerank 模型未配置", 503)

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
                        message_id, question, mode, status
                    ) VALUES (
                        :id, :trace_id, :request_id, :kb_id, :index_id, :conversation_id,
                        :message_id, :question, :mode, 'RUNNING'
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
            ),
            history=history,
            rerank=ModelSnapshot(
                provider=str(rerank_row["provider"]),
                model_name=str(rerank_row["model_name"]),
                parameters=dict(rerank_parameters),
            ),
        )

    def _budget_history(self, messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        selected: list[dict[str, str]] = []
        remaining = self.settings.m3_history_token_budget
        for item in reversed(messages):
            content = str(item["content"])
            estimate = max(1, (len(content) + 1) // 2)
            if estimate > remaining:
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
    ) -> None:
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
                        status = 'COMPLETED', finished_at = now()
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
    ) -> None:
        message_status = "CANCELLED" if status == "CANCELLED" else "FAILED"
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
                    "error": json.dumps({"code": code, "message": message}, ensure_ascii=False),
                    "latency": json.dumps(timings),
                    "status": status,
                },
            )

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
        query = f"""
            SELECT trace_id, request_id, mode, kb_id, index_id, question, status,
                   latency, error, created_at, finished_at
            FROM rag_trace
            WHERE {" AND ".join(clauses)}
            ORDER BY created_at DESC
            LIMIT :limit
        """
        async with self.engine.connect() as connection:
            result = await connection.execute(text(query), parameters)
            return [dict(row) for row in result.mappings()]

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
                environment_key = {
                    "LLM": self.settings.zhipu_api_key,
                    "VISION": self.settings.zhipu_api_key,
                    "EMBEDDING": self.settings.siliconflow_api_key,
                    "RERANK": self.settings.siliconflow_api_key,
                    "OCR": self.settings.siliconflow_api_key,
                }.get(model_type)
                row["api_key_configured"] = bool(row.pop("stored_key_configured")) or (
                    environment_key is not None and bool(environment_key.get_secret_value().strip())
                )
                rows.append(row)
            return rows

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
                           created_at, updated_at
                    FROM message WHERE conversation_id = :id
                    ORDER BY sequence_no
                    """
                ),
                {"id": conversation_id},
            )
            return [dict(row) for row in result.mappings()]
