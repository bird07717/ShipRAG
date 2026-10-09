"""Persistence and lifecycle rules for RAG document cards."""

from __future__ import annotations

import json
from contextlib import nullcontext
from typing import Any
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.common.errors import ApiError
from app.core.config import Settings
from app.core.secrets import ModelSecretBox
from app.ingestion.model_identity import (
    configured_embedding_identity,
    index_embedding_matches,
    legacy_embedding_unchanged_sql,
)
from app.rag.document_cards import (
    DOCUMENT_CARD_SCHEMA_VERSION,
    DocumentCardPayload,
    build_routing_text,
)
from app.rag.model_providers import environment_api_key
from app.rag.models import ModelSnapshot


class DocumentCardRepository:
    def __init__(self, engine: AsyncEngine, settings: Settings) -> None:
        self.engine = engine
        self.settings = settings
        self._secret_box: ModelSecretBox | None = None

    async def resolve_index(
        self, knowledge_id: UUID, index_id: UUID | None = None
    ) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT i.id, i.kb_id, i.version, i.status, i.embedding_model_id,
                                   i.embedding_model_name, i.embedding_dimension
                            FROM knowledge_base kb
                            JOIN knowledge_index i ON i.id = COALESCE(
                                CAST(:index_id AS uuid), kb.rag_active_index_id, kb.active_index_id
                            )
                            WHERE kb.id = :knowledge_id AND kb.status <> 'DELETING'
                              AND i.kb_id = kb.id
                              AND i.status IN ('READY', 'ACTIVE', 'DEPRECATED')
                            """
                        ),
                        {"knowledge_id": knowledge_id, "index_id": index_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ApiError("INDEX_NOT_READY", "知识库没有可生成文档卡片的索引", 409)
        return dict(row)

    async def generation_documents(
        self,
        knowledge_id: UUID,
        index_id: UUID,
        document_id: UUID | None = None,
    ) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT d.id AS document_id, d.kb_id AS knowledge_id,
                                   d.display_name, d.filename, i.id AS index_id,
                                   i.version AS index_version, idoc.source_hash,
                                   COALESCE(string_agg(
                                       CASE WHEN e.element_type IN ('TEXT', 'TABLE')
                                            THEN e.content ELSE '' END,
                                       E'\n\n' ORDER BY e.sequence_no
                                   ), '') AS content,
                                   COALESCE(
                                       array_agg(DISTINCT e.section_path::text)
                                           FILTER (WHERE e.section_path <> '[]'::jsonb),
                                       '{}'
                                   ) AS sections,
                                   count(*) FILTER (WHERE e.element_type = 'IMAGE') AS image_count
                            FROM knowledge_index i
                            JOIN index_document idoc ON idoc.index_id = i.id
                            JOIN document_source d ON d.id = idoc.document_id
                            LEFT JOIN document_element e
                              ON e.index_id = i.id AND e.document_id = d.id
                            WHERE i.id = :index_id AND i.kb_id = :knowledge_id
                              AND idoc.status = 'READY' AND d.status = 'STORED'
                              AND (CAST(:document_id AS uuid) IS NULL
                                   OR d.id = CAST(:document_id AS uuid))
                            GROUP BY d.id, d.kb_id, d.display_name, d.filename,
                                     i.id, i.version, idoc.source_hash, d.created_at
                            ORDER BY d.created_at, d.id
                            """
                        ),
                        {
                            "knowledge_id": knowledge_id,
                            "index_id": index_id,
                            "document_id": document_id,
                        },
                    )
                )
                .mappings()
                .all()
            )
        documents: list[dict[str, Any]] = []
        for raw in rows:
            row = dict(raw)
            section_names: list[str] = []
            for encoded in row.get("sections") or []:
                try:
                    values = json.loads(str(encoded))
                except (TypeError, ValueError):
                    continue
                if isinstance(values, list):
                    section_names.extend(str(value) for value in values if str(value).strip())
            row["sections"] = list(dict.fromkeys(section_names))[:80]
            documents.append(row)
        if document_id is not None and not documents:
            raise ApiError(
                "DOCUMENT_NOT_INDEXED",
                "文档不存在、不属于该知识库或未在所选索引中就绪",
                409,
            )
        return documents

    async def list_cards(self, knowledge_id: UUID, index_id: UUID) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT c.*, d.display_name AS document_name, i.version AS index_version
                            FROM rag_document_card c
                            JOIN document_source d ON d.id = c.document_id
                            JOIN knowledge_index i ON i.id = c.index_id
                            WHERE c.knowledge_id = :knowledge_id AND c.index_id = :index_id
                              AND d.status = 'STORED'
                            ORDER BY d.display_name, c.document_id
                            """
                        ),
                        {"knowledge_id": knowledge_id, "index_id": index_id},
                    )
                )
                .mappings()
                .all()
            )
        return [self._card(dict(row)) for row in rows]

    async def get_card(
        self, knowledge_id: UUID, index_id: UUID, document_id: UUID
    ) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT c.*, d.display_name AS document_name, i.version AS index_version
                            FROM rag_document_card c
                            JOIN document_source d ON d.id = c.document_id
                            JOIN knowledge_index i ON i.id = c.index_id
                            WHERE c.knowledge_id = :knowledge_id AND c.index_id = :index_id
                              AND c.document_id = :document_id AND d.status = 'STORED'
                            """
                        ),
                        {
                            "knowledge_id": knowledge_id,
                            "index_id": index_id,
                            "document_id": document_id,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
        return self._card(dict(row)) if row is not None else None

    async def prepare_generation(
        self,
        *,
        knowledge_id: UUID,
        index_id: UUID,
        document_ids: list[UUID],
        replace_confirmed: bool,
    ) -> None:
        if not document_ids:
            return
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_document_card (
                        id, knowledge_id, index_id, document_id, generation_status,
                        review_status, generator_snapshot
                    )
                    SELECT gen_random_uuid(), :knowledge_id, :index_id, id,
                           'PENDING', 'AUTO', '{}'::jsonb
                    FROM document_source WHERE id = ANY(:document_ids) AND kb_id = :knowledge_id
                    ON CONFLICT (index_id, document_id) DO UPDATE SET
                        generation_status = CASE
                            WHEN rag_document_card.review_status = 'CONFIRMED'
                            THEN rag_document_card.generation_status ELSE 'PENDING' END,
                        error_code = CASE
                            WHEN rag_document_card.review_status = 'CONFIRMED'
                            THEN rag_document_card.error_code ELSE NULL END,
                        error_message = CASE
                            WHEN rag_document_card.review_status = 'CONFIRMED'
                            THEN rag_document_card.error_message ELSE NULL END,
                        updated_at = now()
                    """
                ),
                {
                    "knowledge_id": knowledge_id,
                    "index_id": index_id,
                    "document_ids": document_ids,
                },
            )

    async def save_generated(
        self,
        *,
        knowledge_id: UUID,
        index_id: UUID,
        document_id: UUID,
        payload: DocumentCardPayload,
        embedding: list[float],
        fingerprint: str,
        generator_snapshot: dict[str, Any],
        replace_confirmed: bool,
        expected_revision: int | None = None,
    ) -> bool:
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    INSERT INTO rag_document_card (
                        id, knowledge_id, index_id, document_id, schema_version,
                        document_type, card_payload, routing_text, routing_embedding,
                        content_fingerprint, generation_status, review_status, revision,
                        generator_snapshot
                    ) VALUES (
                        :id, :knowledge_id, :index_id, :document_id, :schema_version,
                        :document_type, CAST(:payload AS jsonb), :routing_text,
                        CAST(:embedding AS vector), :fingerprint, 'READY', 'AUTO', 1,
                        CAST(:snapshot AS jsonb)
                    )
                    ON CONFLICT (index_id, document_id) DO UPDATE SET
                        schema_version = EXCLUDED.schema_version,
                        document_type = EXCLUDED.document_type,
                        card_payload = EXCLUDED.card_payload,
                        routing_text = EXCLUDED.routing_text,
                        routing_embedding = EXCLUDED.routing_embedding,
                        content_fingerprint = EXCLUDED.content_fingerprint,
                        generation_status = 'READY', review_status = 'AUTO',
                        revision = rag_document_card.revision + 1,
                        generator_snapshot = EXCLUDED.generator_snapshot,
                        error_code = NULL, error_message = NULL, updated_at = now()
                    WHERE (rag_document_card.review_status <> 'CONFIRMED'
                       OR :replace_confirmed)
                      AND (CAST(:expected_revision AS integer) IS NULL
                           OR rag_document_card.revision = CAST(:expected_revision AS integer))
                    RETURNING id
                    """
                ),
                {
                    "id": uuid4(),
                    "knowledge_id": knowledge_id,
                    "index_id": index_id,
                    "document_id": document_id,
                    "schema_version": DOCUMENT_CARD_SCHEMA_VERSION,
                    "document_type": payload.document_type,
                    "payload": json.dumps(payload.model_dump(mode="json"), ensure_ascii=False),
                    "routing_text": build_routing_text(payload),
                    "embedding": json.dumps(embedding),
                    "fingerprint": fingerprint,
                    "snapshot": json.dumps(generator_snapshot, ensure_ascii=False),
                    "replace_confirmed": replace_confirmed,
                    "expected_revision": expected_revision,
                },
            )
            return result.scalar_one_or_none() is not None

    async def record_failure(
        self,
        *,
        knowledge_id: UUID,
        index_id: UUID,
        document_id: UUID,
        error_code: str,
        error_message: str,
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE rag_document_card SET
                        generation_status = CASE WHEN review_status = 'CONFIRMED'
                            THEN generation_status ELSE 'FAILED' END,
                        error_code = :error_code, error_message = :error_message,
                        updated_at = now()
                    WHERE knowledge_id = :knowledge_id AND index_id = :index_id
                      AND document_id = :document_id
                    """
                ),
                {
                    "knowledge_id": knowledge_id,
                    "index_id": index_id,
                    "document_id": document_id,
                    "error_code": error_code[:100],
                    "error_message": error_message[:500],
                },
            )

    async def update_card(
        self,
        *,
        knowledge_id: UUID,
        index_id: UUID,
        document_id: UUID,
        expected_revision: int,
        payload: DocumentCardPayload,
        embedding: list[float],
        fingerprint: str,
    ) -> dict[str, Any]:
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE rag_document_card SET
                        schema_version = :schema_version, document_type = :document_type,
                        card_payload = CAST(:payload AS jsonb), routing_text = :routing_text,
                        routing_embedding = CAST(:embedding AS vector),
                        content_fingerprint = :fingerprint, generation_status = 'READY',
                        review_status = 'AUTO', revision = revision + 1,
                        generator_snapshot = generator_snapshot || '{"source":"MANUAL"}'::jsonb,
                        error_code = NULL, error_message = NULL, updated_at = now()
                    WHERE knowledge_id = :knowledge_id AND index_id = :index_id
                      AND document_id = :document_id AND revision = :expected_revision
                    RETURNING id
                    """
                ),
                {
                    "schema_version": DOCUMENT_CARD_SCHEMA_VERSION,
                    "document_type": payload.document_type,
                    "payload": json.dumps(payload.model_dump(mode="json"), ensure_ascii=False),
                    "routing_text": build_routing_text(payload),
                    "embedding": json.dumps(embedding),
                    "fingerprint": fingerprint,
                    "knowledge_id": knowledge_id,
                    "index_id": index_id,
                    "document_id": document_id,
                    "expected_revision": expected_revision,
                },
            )
            if result.scalar_one_or_none() is None:
                await self._raise_update_error(
                    connection, knowledge_id, index_id, document_id, expected_revision
                )
        card = await self.get_card(knowledge_id, index_id, document_id)
        assert card is not None
        return card

    async def copy_cards(self, prev_index_id: UUID, new_index_id: UUID) -> int:
        """Copy REUSE documents' cards from the previous index to a new one.

        Only READY cards with the current schema version are copied, and only
        for documents the ingestion layer marked build_mode=REUSE in the new
        index (same file hash, parser revision and embedding model).  The
        generation task's fingerprint-skip logic then treats the copies as
        settled, so a rebuild after adding one document only generates cards
        for that document.  review_status is preserved verbatim: CONFIRMED
        carries across versions, AUTO stays pending human review, REJECTED
        keeps blocking readiness.
        """
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    INSERT INTO rag_document_card (
                        id, knowledge_id, index_id, document_id, schema_version,
                        document_type, card_payload, routing_text, routing_embedding,
                        content_fingerprint, generation_status, review_status, revision,
                        generator_snapshot, created_at, updated_at
                    )
                    SELECT gen_random_uuid(), c.knowledge_id, :new_index_id,
                           c.document_id, c.schema_version, c.document_type,
                           c.card_payload, c.routing_text, c.routing_embedding,
                           c.content_fingerprint, 'READY', c.review_status, 1,
                           c.generator_snapshot, now(), now()
                    FROM rag_document_card c
                    WHERE c.index_id = :prev_index_id
                      AND c.generation_status = 'READY'
                      AND c.schema_version = :schema_version
                      AND c.document_id IN (
                          SELECT document_id FROM index_document
                          WHERE index_id = :new_index_id
                            AND status = 'READY'
                            AND metadata->>'build_mode' = 'REUSE'
                      )
                    ON CONFLICT (index_id, document_id) DO NOTHING
                    """
                ),
                {
                    "prev_index_id": prev_index_id,
                    "new_index_id": new_index_id,
                    "schema_version": DOCUMENT_CARD_SCHEMA_VERSION,
                },
            )
            return int(result.rowcount or 0)

    async def confirm_auto_cards(self, knowledge_id: UUID, index_id: UUID) -> int:
        """Batch-confirm every READY/AUTO card of the index in one transaction.

        REJECTED / FAILED / STALE / PENDING cards are left untouched so the
        readiness blockers keep reporting them honestly.  revision is bumped
        to keep the optimistic-lock chain intact for later single-card edits.
        """
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE rag_document_card
                    SET review_status = 'CONFIRMED', revision = revision + 1,
                        updated_at = now()
                    WHERE knowledge_id = :knowledge_id AND index_id = :index_id
                      AND generation_status = 'READY' AND review_status = 'AUTO'
                    RETURNING id
                    """
                ),
                {"knowledge_id": knowledge_id, "index_id": index_id},
            )
            return len(result.all())

    async def review_card(
        self,
        *,
        knowledge_id: UUID,
        index_id: UUID,
        document_id: UUID,
        expected_revision: int,
        review_status: str,
    ) -> dict[str, Any]:
        if review_status not in {"CONFIRMED", "REJECTED"}:
            raise ApiError("VALIDATION_ERROR", "审核状态不受支持", 422)
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE rag_document_card SET review_status = :review_status,
                        revision = revision + 1, updated_at = now()
                    WHERE knowledge_id = :knowledge_id AND index_id = :index_id
                      AND document_id = :document_id AND revision = :expected_revision
                      AND generation_status = 'READY'
                    RETURNING id
                    """
                ),
                {
                    "review_status": review_status,
                    "knowledge_id": knowledge_id,
                    "index_id": index_id,
                    "document_id": document_id,
                    "expected_revision": expected_revision,
                },
            )
            if result.scalar_one_or_none() is None:
                await self._raise_update_error(
                    connection, knowledge_id, index_id, document_id, expected_revision
                )
        card = await self.get_card(knowledge_id, index_id, document_id)
        assert card is not None
        return card

    async def readiness(
        self,
        knowledge_id: UUID,
        index_id: UUID,
        *,
        connection: AsyncConnection | None = None,
    ) -> dict[str, Any]:
        async with (
            self.engine.connect() if connection is None else nullcontext(connection)
        ) as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                            WITH expected AS (
                                SELECT idoc.document_id
                                FROM index_document idoc
                                JOIN document_source d ON d.id = idoc.document_id
                                WHERE idoc.index_id = :index_id AND idoc.status = 'READY'
                                  AND d.kb_id = :knowledge_id AND d.status = 'STORED'
                            )
                            SELECT count(*) AS expected_count,
                                   count(c.document_id) FILTER (
                                       WHERE c.generation_status = 'READY'
                                         AND c.schema_version = :schema_version
                                         AND c.review_status <> 'REJECTED'
                                   ) AS ready_count,
                                   count(c.document_id) FILTER (
                                       WHERE c.generation_status = 'READY'
                                         AND c.schema_version = :schema_version
                                         AND c.review_status = 'CONFIRMED'
                                   ) AS confirmed_count,
                                   count(c.document_id) FILTER (
                                       WHERE c.generation_status = 'READY'
                                         AND c.review_status = 'AUTO'
                                   ) AS auto_count,
                                   count(c.document_id) FILTER (WHERE c.review_status = 'REJECTED')
                                       AS rejected_count,
                                   count(c.document_id) FILTER (
                                       WHERE c.generation_status = 'FAILED'
                                   )
                                       AS failed_count,
                                   count(c.document_id) FILTER (
                                       WHERE c.generation_status = 'STALE'
                                          OR c.schema_version <> :schema_version
                                   ) AS stale_count,
                                   count(*) FILTER (WHERE c.document_id IS NULL) AS missing_count
                            FROM expected e
                            LEFT JOIN rag_document_card c
                              ON c.knowledge_id = :knowledge_id AND c.index_id = :index_id
                             AND c.document_id = e.document_id
                            """
                        ),
                        {
                            "knowledge_id": knowledge_id,
                            "index_id": index_id,
                            "schema_version": DOCUMENT_CARD_SCHEMA_VERSION,
                        },
                    )
                )
                .mappings()
                .one()
            )
        counts = {key: int(value or 0) for key, value in dict(row).items()}
        blockers: list[str] = []
        for key, label in (
            ("missing_count", "缺失"),
            ("failed_count", "生成失败"),
            ("stale_count", "已过期"),
            ("rejected_count", "已拒绝"),
            ("auto_count", "待人工确认"),
        ):
            if counts[key]:
                blockers.append(f"{label} {counts[key]} 份")
        return {
            "knowledge_id": knowledge_id,
            "index_id": index_id,
            **counts,
            "shadow_ready": counts["expected_count"] > 0
            and counts["ready_count"] == counts["expected_count"],
            "ready": counts["expected_count"] > 0
            and counts["confirmed_count"] == counts["expected_count"],
            "blockers": blockers,
        }

    async def embedding_snapshot(self, index_id: UUID) -> ModelSnapshot:
        async with self.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT i.embedding_model_name, i.build_models,
                                   """
                            + legacy_embedding_unchanged_sql("i")
                            + """,
                                   m.provider, m.model_name, m.parameters, m.base_url,
                                   m.api_key_ciphertext, m.api_key_nonce, m.api_key_key_version
                            FROM knowledge_index i
                            JOIN model_config m ON m.id = i.embedding_model_id
                            WHERE i.id = :index_id
                            """
                        ),
                        {"index_id": index_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ApiError("MODEL_NOT_CONFIGURED", "索引冻结的 Embedding 模型不存在", 409)
        raw = dict(row)
        api_key: SecretStr | None = None
        if raw["api_key_ciphertext"] is not None:
            if self.settings.model_secret_key is None:
                raise ApiError("MODEL_SECRET_NOT_CONFIGURED", "未配置模型密钥管理配置", 409)
            if self._secret_box is None:
                self._secret_box = ModelSecretBox(
                    self.settings.model_secret_key,
                    version=self.settings.model_secret_key_version,
                    previous_key=self.settings.model_secret_key_previous,
                    previous_version=self.settings.model_secret_key_previous_version,
                )
            api_key = self._secret_box.decrypt(
                bytes(raw["api_key_ciphertext"]),
                bytes(raw["api_key_nonce"]),
                int(raw["api_key_key_version"] or 1),
            )
        identity = configured_embedding_identity(
            self.settings,
            provider=str(raw["provider"]),
            model_name=str(raw["model_name"]),
            base_url=raw["base_url"],
            has_key=self.settings.has_secret(api_key)
            or self.settings.has_secret(
                environment_api_key(self.settings, "EMBEDDING", str(raw["provider"]))
            ),
        )
        if not index_embedding_matches(
            dict(raw.get("build_models") or {}),
            str(raw["embedding_model_name"]),
            identity,
            legacy_unchanged=bool(raw.get("legacy_embedding_unchanged", True)),
        ):
            raise ApiError(
                "INDEX_EMBEDDING_MISMATCH", "索引与当前 Embedding 配置不一致，请先重建索引", 409
            )
        return ModelSnapshot(
            provider=str(raw["provider"]),
            model_name=str(raw["model_name"]),
            parameters=dict(raw["parameters"] or {}),
            base_url=str(raw["base_url"]) if raw["base_url"] is not None else None,
            api_key=api_key,
        )

    @staticmethod
    async def _raise_update_error(
        connection: AsyncConnection,
        knowledge_id: UUID,
        index_id: UUID,
        document_id: UUID,
        expected_revision: int,
    ) -> None:
        current = await connection.scalar(
            text(
                """
                SELECT revision FROM rag_document_card
                WHERE knowledge_id = :knowledge_id AND index_id = :index_id
                  AND document_id = :document_id
                """
            ),
            {
                "knowledge_id": knowledge_id,
                "index_id": index_id,
                "document_id": document_id,
            },
        )
        if current is None:
            raise ApiError("RAG_DOCUMENT_CARD_NOT_FOUND", "RAG 文档卡片不存在", 404)
        raise ApiError(
            "RAG_DOCUMENT_CARD_UPDATE_CONFLICT",
            "RAG 文档卡片已被其他操作更新，请刷新后重试",
            409,
            details={"expected_revision": expected_revision, "current_revision": int(current)},
        )

    @staticmethod
    def _card(row: dict[str, Any]) -> dict[str, Any]:
        payload = row.get("card_payload")
        row["card_payload"] = dict(payload) if isinstance(payload, dict) else None
        snapshot = row.get("generator_snapshot")
        row["generator_snapshot"] = dict(snapshot) if isinstance(snapshot, dict) else {}
        return row
