# ruff: noqa: E501
from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.common.errors import ApiError
from app.common.text import estimate_tokens
from app.core.config import Settings
from app.core.secrets import ModelSecretBox
from app.rag.models import ModelSnapshot
from app.support.models import DocumentContentBlock, FocusedDocument, ProblemProfile, SupportTurn


class SupportRepository:
    def __init__(self, engine: AsyncEngine, settings: Settings) -> None:
        self.engine = engine
        self.settings = settings
        self._secret_box: ModelSecretBox | None = None

    def _decrypt_api_key(self, row: dict[str, Any]) -> SecretStr | None:
        if row.get("api_key_ciphertext") is None:
            return None
        if self.settings.model_secret_key is None:
            raise ApiError("MODEL_SECRET_NOT_CONFIGURED", "未配置模型密钥管理配置", 409)
        if self._secret_box is None:
            self._secret_box = ModelSecretBox(
                self.settings.model_secret_key,
                version=self.settings.model_secret_key_version,
                previous_key=self.settings.model_secret_key_previous,
                previous_version=self.settings.model_secret_key_previous_version,
            )
        return self._secret_box.decrypt(
            bytes(row["api_key_ciphertext"]),
            bytes(row["api_key_nonce"]),
            int(row["api_key_key_version"] or 1),
        )

    def _budget_history(self, messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        remaining = self.settings.m3_history_token_budget
        for message in reversed(messages):
            content = str(message["content"])
            tokens = max(1, estimate_tokens(content))
            if tokens > remaining:
                if result:
                    break
                continue
            remaining -= tokens
            result.append({"role": str(message["role"]), "content": content})
        result.reverse()
        return result

    @staticmethod
    def request_hash(
        *,
        question: str,
        conversation_id: UUID | None,
        document_id: UUID | None,
        problem_code: str | None,
        choice_id: str | None,
    ) -> str:
        payload = json.dumps(
            {
                "question": question,
                "conversation_id": str(conversation_id) if conversation_id else None,
                "document_id": str(document_id) if document_id else None,
                "problem_code": problem_code,
                "choice_id": choice_id,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    async def begin_turn(
        self,
        *,
        knowledge_id: UUID,
        conversation_id: UUID | None,
        question: str,
        request_id: str,
        user_id: str | None,
        idempotency_key: str | None,
        request_hash: str,
    ) -> SupportTurn:
        trace_id, user_message_id, assistant_message_id = uuid4(), uuid4(), uuid4()
        retry_failed_idempotency = False
        async with self.engine.begin() as conn:
            kb = (
                (
                    await conn.execute(
                        text("""
                SELECT kb.status, kb.active_index_id, i.status AS index_status, i.kb_id AS index_kb_id
                FROM knowledge_base kb LEFT JOIN knowledge_index i ON i.id = kb.active_index_id
                WHERE kb.id = :kb_id FOR UPDATE OF kb
            """),
                        {"kb_id": knowledge_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if kb is None:
                raise ApiError("KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在", 404)
            if kb["status"] != "ENABLED":
                raise ApiError("FORBIDDEN", "知识库已停用", 403)

            if idempotency_key:
                existing = (
                    (
                        await conn.execute(
                            text("""
                    SELECT request_hash, response, status FROM support_idempotency
                    WHERE kb_id = :kb_id AND idempotency_key = :key FOR UPDATE
                """),
                            {"kb_id": knowledge_id, "key": idempotency_key},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing is not None:
                    if str(existing["request_hash"]) != request_hash:
                        raise ApiError("IDEMPOTENCY_KEY_REUSED", "幂等键已用于不同请求", 409)
                    if existing["status"] == "COMPLETED" and isinstance(existing["response"], dict):
                        response = dict(existing["response"])
                        return SupportTurn(
                            trace_id=UUID(str(response["trace_id"])),
                            conversation_id=UUID(str(response["conversation_id"])),
                            user_message_id=user_message_id,
                            assistant_message_id=assistant_message_id,
                            knowledge_id=knowledge_id,
                            index_id=UUID(str(response["index_id"])),
                            question=question,
                            focus_document_id=None,
                            chat_context={},
                            context_version=0,
                            history=[],
                            idempotency_key=idempotency_key,
                            replay=response,
                        )
                    if existing["status"] == "RUNNING":
                        raise ApiError("REQUEST_IN_PROGRESS", "相同幂等请求仍在处理中", 409)
                    if existing["status"] != "FAILED":
                        raise ApiError("IDEMPOTENCY_STATE_INVALID", "幂等请求状态无效", 500)
                    retry_failed_idempotency = True

            resolved_conversation_id = conversation_id or uuid4()
            focus_document_id: UUID | None = None
            focus_index_id: UUID | None = None
            chat_context: dict[str, Any] = {}
            if conversation_id is None:
                await conn.execute(
                    text("INSERT INTO conversation (id, knowledge_id) VALUES (:id, :kb_id)"),
                    {"id": resolved_conversation_id, "kb_id": knowledge_id},
                )
            else:
                row = (
                    (
                        await conn.execute(
                            text("""
                    SELECT c.knowledge_id, c.focus_document_id, c.focus_index_id,
                           c.chat_context, c.support_context_version,
                           fi.kb_id AS focus_index_kb_id
                    FROM conversation c
                    LEFT JOIN knowledge_index fi ON fi.id = c.focus_index_id
                    WHERE c.id = :id FOR UPDATE OF c
                """),
                            {"id": conversation_id},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    raise ApiError("CONVERSATION_NOT_FOUND", "会话不存在", 404)
                if row["knowledge_id"] != knowledge_id:
                    raise ApiError("CONVERSATION_KB_MISMATCH", "会话不属于指定知识库", 409)
                focus_document_id = (
                    UUID(str(row["focus_document_id"])) if row["focus_document_id"] else None
                )
                focus_index_id = UUID(str(row["focus_index_id"])) if row["focus_index_id"] else None
                if focus_document_id is not None and (
                    focus_index_id is None or row["focus_index_kb_id"] != knowledge_id
                ):
                    raise ApiError(
                        "CONVERSATION_FOCUS_VERSION_MISSING",
                        "会话绑定的文档版本不可用，请重新选择解决方案",
                        409,
                    )
                chat_context = (
                    dict(row["chat_context"]) if isinstance(row["chat_context"], dict) else {}
                )
                context_version = int(row["support_context_version"])
            if conversation_id is None:
                context_version = 0

            if focus_document_id is not None:
                effective_index_id = focus_index_id
            else:
                if (
                    kb["active_index_id"] is None
                    or kb["index_status"] != "ACTIVE"
                    or kb["index_kb_id"] != knowledge_id
                ):
                    raise ApiError("KNOWLEDGE_BASE_NOT_READY", "知识库尚无可用 Active Index", 409)
                effective_index_id = UUID(str(kb["active_index_id"]))
            if effective_index_id is None:  # narrowed above; protects future query changes
                raise ApiError("KNOWLEDGE_BASE_NOT_READY", "知识库尚无可用文档版本", 409)

            history_rows = (
                (
                    await conn.execute(
                        text("""
                SELECT role, content FROM (
                    SELECT role, content, sequence_no FROM message
                    WHERE conversation_id = :id AND status = 'COMPLETED'
                    ORDER BY sequence_no DESC LIMIT :limit
                ) recent ORDER BY sequence_no
            """),
                        {
                            "id": resolved_conversation_id,
                            "limit": self.settings.support_history_max_messages,
                        },
                    )
                )
                .mappings()
                .all()
            )
            sequence = (
                int(
                    (
                        await conn.execute(
                            text(
                                "SELECT COALESCE(max(sequence_no), 0) FROM message WHERE conversation_id = :id"
                            ),
                            {"id": resolved_conversation_id},
                        )
                    ).scalar_one()
                )
                + 1
            )
            await conn.execute(
                text("""
                INSERT INTO message (id, conversation_id, sequence_no, role, content, status)
                VALUES (:user_id, :conversation_id, :sequence, 'USER', :question, 'COMPLETED'),
                       (:assistant_id, :conversation_id, :assistant_sequence, 'ASSISTANT', '', 'STREAMING')
            """),
                {
                    "user_id": user_message_id,
                    "assistant_id": assistant_message_id,
                    "conversation_id": resolved_conversation_id,
                    "sequence": sequence,
                    "assistant_sequence": sequence + 1,
                    "question": question,
                },
            )
            await conn.execute(
                text("""
                INSERT INTO rag_trace (
                    id, trace_id, request_id, kb_id, index_id, conversation_id,
                    message_id, question, mode, status, user_id, engine, stage
                ) VALUES (
                    :id, :trace_id, :request_id, :kb_id, :index_id, :conversation_id,
                    :message_id, :question, 'CHAT', 'RUNNING', :user_id,
                    'SUPPORT', 'PROFILE_RESOLUTION'
                )
            """),
                {
                    "id": uuid4(),
                    "trace_id": trace_id,
                    "request_id": request_id,
                    "kb_id": knowledge_id,
                    "index_id": effective_index_id,
                    "conversation_id": resolved_conversation_id,
                    "message_id": assistant_message_id,
                    "question": question,
                    "user_id": user_id,
                },
            )
            if idempotency_key:
                if retry_failed_idempotency:
                    await conn.execute(
                        text("""
                        UPDATE support_idempotency
                        SET conversation_id = :conversation_id, trace_id = :trace_id,
                            response = NULL, status = 'RUNNING', updated_at = now()
                        WHERE kb_id = :kb_id AND idempotency_key = :key
                    """),
                        {
                            "kb_id": knowledge_id,
                            "key": idempotency_key,
                            "conversation_id": resolved_conversation_id,
                            "trace_id": trace_id,
                        },
                    )
                else:
                    await conn.execute(
                        text("""
                    INSERT INTO support_idempotency (id, kb_id, idempotency_key, request_hash, conversation_id, trace_id, status)
                    VALUES (:id, :kb_id, :key, :hash, :conversation_id, :trace_id, 'RUNNING')
                    """),
                        {
                            "id": uuid4(),
                            "kb_id": knowledge_id,
                            "key": idempotency_key,
                            "hash": request_hash,
                            "conversation_id": resolved_conversation_id,
                            "trace_id": trace_id,
                        },
                    )
            return SupportTurn(
                trace_id=trace_id,
                conversation_id=resolved_conversation_id,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
                knowledge_id=knowledge_id,
                index_id=effective_index_id,
                question=question,
                focus_document_id=focus_document_id,
                chat_context=chat_context,
                context_version=context_version,
                history=self._budget_history([dict(item) for item in history_rows]),
                idempotency_key=idempotency_key,
            )

    async def get_llm(self) -> ModelSnapshot:
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        text("""
                SELECT provider, model_name, parameters, base_url, api_key_ciphertext, api_key_nonce, api_key_key_version
                FROM model_config WHERE model_type = 'LLM' AND enabled
            """)
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ApiError("MODEL_NOT_CONFIGURED", "LLM 模型未配置", 503)
        data = dict(row)
        return ModelSnapshot(
            provider=str(data["provider"]),
            model_name=str(data["model_name"]),
            parameters=dict(data["parameters"] or {}),
            base_url=str(data["base_url"]) if data["base_url"] else None,
            api_key=self._decrypt_api_key(data),
        )

    async def get_rerank_model(self) -> ModelSnapshot | None:
        """Return the currently enabled reranker for the optional Support fallback."""
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        text(
                            """
                SELECT provider, model_name, parameters, base_url,
                       api_key_ciphertext, api_key_nonce, api_key_key_version
                FROM model_config WHERE model_type = 'RERANK' AND enabled
            """
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        data = dict(row)
        return ModelSnapshot(
            provider=str(data["provider"]),
            model_name=str(data["model_name"]),
            parameters=dict(data["parameters"] or {}),
            base_url=str(data["base_url"]) if data["base_url"] else None,
            api_key=self._decrypt_api_key(data),
        )

    async def list_profiles(
        self,
        knowledge_id: UUID,
        *,
        enabled_only: bool = False,
        ready_only: bool = False,
    ) -> list[ProblemProfile]:
        query = """
            SELECT p.*,
                   d.status AS document_status,
                   kb.active_index_id,
                   EXISTS (
                       SELECT 1 FROM document_element e
                       WHERE e.index_id = kb.active_index_id
                         AND e.document_id = p.document_id
                   ) AS document_published,
                   NOT EXISTS (
                       SELECT 1
                       FROM document_element e
                       LEFT JOIN image_asset a ON a.element_id = e.id
                       WHERE e.index_id = kb.active_index_id
                         AND e.document_id = p.document_id
                         AND e.element_type = 'IMAGE'
                         AND a.id IS NULL
                   ) AS images_complete
            FROM document_problem_profile p
            JOIN knowledge_base kb ON kb.id = p.kb_id
            JOIN document_source d ON d.id = p.document_id AND d.kb_id = p.kb_id
            WHERE p.kb_id = :kb_id
        """
        if enabled_only:
            query += " AND p.enabled"
        query += " ORDER BY p.canonical_question"
        async with self.engine.connect() as conn:
            rows = (await conn.execute(text(query), {"kb_id": knowledge_id})).mappings().all()
        profiles = [self._profile(dict(row)) for row in rows]
        return (
            [profile for profile in profiles if profile.support_ready] if ready_only else profiles
        )

    async def get_profile_by_code(
        self, knowledge_id: UUID, problem_code: str
    ) -> ProblemProfile | None:
        profiles = await self.list_profiles(knowledge_id, enabled_only=True)
        return next((profile for profile in profiles if profile.problem_code == problem_code), None)

    async def get_profile_by_document(
        self, knowledge_id: UUID, document_id: UUID
    ) -> ProblemProfile | None:
        profiles = await self.list_profiles(knowledge_id, enabled_only=True)
        return next((profile for profile in profiles if profile.document_id == document_id), None)

    @staticmethod
    def _profile(row: dict[str, Any]) -> ProblemProfile:
        readiness_reasons: list[str] = []
        if not row.get("enabled", False):
            readiness_reasons.append("PROFILE_DISABLED")
        if row.get("document_status") != "STORED":
            readiness_reasons.append("DOCUMENT_NOT_STORED")
        if row.get("active_index_id") is None:
            readiness_reasons.append("KNOWLEDGE_BASE_NOT_READY")
        elif not row.get("document_published", False):
            readiness_reasons.append("DOCUMENT_NOT_PUBLISHED")
        if not row.get("images_complete", True):
            readiness_reasons.append("IMAGE_ASSET_MISSING")
        return ProblemProfile(
            id=UUID(str(row["id"])),
            document_id=UUID(str(row["document_id"])),
            knowledge_id=UUID(str(row["kb_id"])),
            problem_code=str(row["problem_code"]),
            canonical_question=str(row["canonical_question"]),
            aliases=list(row["aliases"] or []),
            keywords=list(row["keywords"] or []),
            product_name=row["product_name"],
            model_names=list(row["model_names"] or []),
            task_type=row["task_type"],
            routing_summary=str(row.get("routing_summary") or ""),
            symptoms=list(row.get("symptoms") or []),
            exclusions=list(row.get("exclusions") or []),
            profile_enrichment_version=int(row.get("profile_enrichment_version") or 0),
            enabled=bool(row["enabled"]),
            support_ready=not readiness_reasons,
            readiness_reasons=readiness_reasons,
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    async def get_document(
        self, *, knowledge_id: UUID, index_id: UUID, document_id: UUID, problem_code: str | None
    ) -> FocusedDocument:
        async with self.engine.connect() as conn:
            doc = (
                (
                    await conn.execute(
                        text("""
                SELECT id, display_name, filename FROM document_source
                WHERE id = :id AND kb_id = :kb_id AND status = 'STORED'
            """),
                        {"id": document_id, "kb_id": knowledge_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if doc is None:
                raise ApiError("DOCUMENT_NOT_FOUND", "文档不存在或已删除", 404)
            rows = (
                (
                    await conn.execute(
                        text("""
                SELECT e.id AS element_id, e.sequence_no, e.element_type, e.content,
                       a.id AS image_asset_id, a.mime_type, a.width, a.height, a.vision_caption, a.ocr_text
                FROM document_element e LEFT JOIN image_asset a ON a.element_id = e.id
                WHERE e.index_id = :index_id AND e.document_id = :document_id
                ORDER BY e.sequence_no
            """),
                        {"index_id": index_id, "document_id": document_id},
                    )
                )
                .mappings()
                .all()
            )
        if not rows:
            raise ApiError(
                "DOCUMENT_NOT_IN_FOCUSED_VERSION",
                "文档不在当前会话锁定的发布版本中",
                409,
            )
        blocks = [
            DocumentContentBlock(
                element_id=UUID(str(row["element_id"])),
                sequence_no=int(row["sequence_no"]),
                type=str(row["element_type"]).lower(),
                text=str(row["content"] or ""),
                image_asset_id=UUID(str(row["image_asset_id"])) if row["image_asset_id"] else None,
                caption=str(row["vision_caption"] or row["ocr_text"] or "") or None,
                mime_type=str(row["mime_type"]) if row["mime_type"] else None,
                width=int(row["width"]) if row["width"] else None,
                height=int(row["height"]) if row["height"] else None,
                ocr_text=str(row["ocr_text"] or ""),
            )
            for row in rows
        ]
        return FocusedDocument(
            id=document_id,
            title=str(doc["display_name"] or doc["filename"]),
            problem_code=problem_code,
            index_id=index_id,
            blocks=blocks,
        )

    async def finish_turn(
        self,
        turn: SupportTurn,
        *,
        response: dict[str, Any],
        context: dict[str, Any],
        focus_document_id: UUID | None,
        trace: dict[str, Any],
        usage: dict[str, Any] | None = None,
    ) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                text("""
                UPDATE message SET content = :answer, sources = CAST(:sources AS jsonb), content_blocks = CAST(:blocks AS jsonb),
                    response_type = :response_type, support_mode = :support_mode, status = 'COMPLETED', updated_at = now()
                WHERE id = :id AND status = 'STREAMING'
            """),
                {
                    "id": turn.assistant_message_id,
                    "answer": str(response.get("answer", "")),
                    "sources": json.dumps(response.get("references", []), ensure_ascii=False),
                    "blocks": json.dumps(response.get("content", []), ensure_ascii=False),
                    "response_type": response["response_type"],
                    "support_mode": response.get("support_mode"),
                },
            )
            context_result = await conn.execute(
                text("""
                UPDATE conversation
                SET focus_document_id = :focus,
                    focus_index_id = :focus_index,
                    chat_context = CAST(:context AS jsonb),
                    support_context_version = support_context_version + 1,
                    updated_at = now()
                WHERE id = :id AND support_context_version = :expected_version
            """),
                {
                    "id": turn.conversation_id,
                    "focus": focus_document_id,
                    "focus_index": turn.index_id if focus_document_id is not None else None,
                    "context": json.dumps(context, ensure_ascii=False),
                    "expected_version": turn.context_version,
                },
            )
            if context_result.rowcount != 1:
                raise ApiError("WORKFLOW_CONFLICT", "会话状态已被另一请求更新，请重试", 409)
            await conn.execute(
                text("""
                UPDATE rag_trace SET answer = :answer, sources = CAST(:sources AS jsonb), retrieval_result = CAST(:trace AS jsonb),
                    selected_context = CAST(:context_blocks AS jsonb), response_type = :response_type,
                    answer_mode = :answer_mode, model_usage = CAST(:usage AS jsonb), latency = CAST(:latency AS jsonb),
                    stage = 'COMPLETED', status = 'COMPLETED', finished_at = now()
                WHERE trace_id = :trace_id AND status = 'RUNNING'
            """),
                {
                    "trace_id": turn.trace_id,
                    "answer": str(response.get("answer", "")),
                    "sources": json.dumps(response.get("references", []), ensure_ascii=False),
                    "trace": json.dumps(trace, ensure_ascii=False),
                    "context_blocks": json.dumps(response.get("content", []), ensure_ascii=False),
                    "response_type": response["response_type"],
                    "answer_mode": None,
                    "usage": json.dumps(usage or {}, ensure_ascii=False),
                    "latency": json.dumps({"total_ms": response["latency_ms"]}),
                },
            )
            if turn.idempotency_key:
                await conn.execute(
                    text("""
                    UPDATE support_idempotency SET response = CAST(:response AS jsonb), status = 'COMPLETED', updated_at = now()
                    WHERE kb_id = :kb_id AND idempotency_key = :key
                """),
                    {
                        "kb_id": turn.knowledge_id,
                        "key": turn.idempotency_key,
                        "response": json.dumps(response, ensure_ascii=False),
                    },
                )

    async def set_stage(self, turn: SupportTurn, stage: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    UPDATE rag_trace SET stage = :stage
                    WHERE trace_id = :trace_id AND status = 'RUNNING'
                    """
                ),
                {"trace_id": turn.trace_id, "stage": stage},
            )

    async def save_gateway_response(
        self,
        *,
        knowledge_id: UUID,
        idempotency_key: str,
        support_trace_id: UUID,
        response: dict[str, Any],
    ) -> None:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                text(
                    """
                    UPDATE support_idempotency
                    SET response = response || jsonb_build_object(
                            '_gateway_response', CAST(:gateway_response AS jsonb)
                        ),
                        updated_at = now()
                    WHERE kb_id = :kb_id AND idempotency_key = :key
                      AND trace_id = :trace_id AND status = 'COMPLETED'
                    """
                ),
                {
                    "kb_id": knowledge_id,
                    "key": idempotency_key,
                    "trace_id": support_trace_id,
                    "gateway_response": json.dumps(response, ensure_ascii=False),
                },
            )
            if result.rowcount != 1:
                raise ApiError("IDEMPOTENCY_STATE_INVALID", "降级幂等状态不一致", 409)

    async def fail_turn(
        self,
        turn: SupportTurn,
        *,
        code: str,
        message: str,
        trace: dict[str, Any] | None = None,
        stage: str | None = None,
        retryable: bool | None = None,
        detail: dict[str, Any] | str | None = None,
    ) -> None:
        # Mirror RagRepository.fail_turn's error payload: code/message plus the
        # optional stage/retryable/detail diagnostics the review page needs.
        error: dict[str, Any] = {"code": code, "message": message}
        if stage is not None:
            error["stage"] = stage
        if retryable is not None:
            error["retryable"] = retryable
        if detail is not None:
            error["detail"] = detail
        async with self.engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE message SET status = 'FAILED', updated_at = now() WHERE id = :id AND status = 'STREAMING'"
                ),
                {"id": turn.assistant_message_id},
            )
            await conn.execute(
                text("""
                UPDATE rag_trace SET error = CAST(:error AS jsonb), retrieval_result = CAST(:trace AS jsonb),
                    stage = COALESCE(:stage, stage), status = 'FAILED', finished_at = now()
                WHERE trace_id = :trace_id AND status = 'RUNNING'
            """),
                {
                    "trace_id": turn.trace_id,
                    "error": json.dumps(error, ensure_ascii=False),
                    "trace": json.dumps(trace or {}, ensure_ascii=False),
                    "stage": stage,
                },
            )
            if turn.idempotency_key:
                await conn.execute(
                    text(
                        "UPDATE support_idempotency SET status = 'FAILED', updated_at = now() WHERE kb_id = :kb_id AND idempotency_key = :key"
                    ),
                    {"kb_id": turn.knowledge_id, "key": turn.idempotency_key},
                )
