# ruff: noqa: E501
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.common.errors import ApiError


class DocumentProfileRepository:
    """Persistence for staged document Profile attributes.

    This intentionally does not write the legacy release tables.  They remain
    readable audit data only while clients migrate to this API.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def list_for_knowledge(self, knowledge_id: UUID) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT d.id AS document_id, d.display_name AS document_name, d.status AS document_status,
                               p.active_payload, p.pending_payload, p.pending_state,
                               p.pending_error_code, p.pending_error_message,
                               p.active_updated_at, p.pending_updated_at, p.enabled
                        FROM document_source d
                        LEFT JOIN document_profile p ON p.document_id = d.id
                        WHERE d.kb_id = :knowledge_id AND d.status <> 'DELETED'
                        ORDER BY d.created_at DESC, d.id
                        """
                        ),
                        {"knowledge_id": knowledge_id},
                    )
                )
                .mappings()
                .all()
            )
        profiles = [self._summary(dict(row)) for row in rows]
        return {
            "knowledge_id": str(knowledge_id),
            "online_count": sum(item["profile_status"] == "ONLINE" for item in profiles),
            "pending_count": sum(item["profile_status"] == "PENDING_ONLINE" for item in profiles),
            "attention_count": sum(
                item["profile_status"] == "NEEDS_ATTENTION" for item in profiles
            ),
            "failed_count": sum(item["profile_status"] == "FAILED" for item in profiles),
            # Running tasks are supplied by the service because task rows do
            # not belong to a single attribute row in an all-document run.
            "generating_count": 0,
            "profiles": profiles,
        }

    async def document_context(self, document_id: UUID) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT d.id AS document_id, d.display_name AS document_name, d.status AS document_status,
                               p.active_payload, p.pending_payload, p.pending_state,
                               p.pending_error_code, p.pending_error_message,
                               p.active_updated_at, p.pending_updated_at, p.enabled
                        FROM document_source d
                        LEFT JOIN document_profile p ON p.document_id = d.id
                        WHERE d.id = :document_id
                        """
                        ),
                        {"document_id": document_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
        return self._summary(dict(row)) if row else None

    async def generation_documents(
        self, knowledge_id: UUID, document_id: UUID | None = None
    ) -> list[dict[str, Any]]:
        """Read current document content without binding Profile to a release.

        RAG may have no active index yet; in that case generation receives an
        empty body and safely produces a conservative pending attribute.
        """
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT d.id AS document_id, d.kb_id, d.display_name, d.filename,
                               COALESCE(string_agg(
                                   CASE WHEN e.element_type IN ('TEXT', 'TABLE') THEN e.content ELSE '' END,
                                   E'\n\n' ORDER BY e.sequence_no
                               ), '') AS content,
                               p.content_hash AS profile_content_hash,
                               p.pending_source, p.pending_state,
                               p.pending_error_code,
                               (p.active_payload IS NOT NULL) AS has_active_profile
                        FROM document_source d
                        LEFT JOIN knowledge_base kb ON kb.id = d.kb_id
                        LEFT JOIN document_element e
                          ON e.document_id = d.id
                         AND e.index_id = COALESCE(kb.rag_active_index_id, kb.active_index_id)
                        LEFT JOIN document_profile p ON p.document_id = d.id
                        WHERE d.kb_id = :knowledge_id AND d.status = 'STORED'
                          AND (CAST(:document_id AS uuid) IS NULL OR d.id = CAST(:document_id AS uuid))
                        GROUP BY d.id, d.kb_id, d.display_name, d.filename,
                                 p.content_hash, p.pending_source, p.pending_state,
                                 p.pending_error_code, p.active_payload
                        ORDER BY d.created_at, d.id
                        """
                        ),
                        {"knowledge_id": knowledge_id, "document_id": document_id},
                    )
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]

    async def upsert_pending(
        self,
        *,
        knowledge_id: UUID,
        document_id: UUID,
        payload: dict[str, Any],
        source: str,
        confidence: float,
        state: str,
        error_code: str | None = None,
        error_message: str | None = None,
        content_hash: str | None = None,
    ) -> dict[str, Any]:
        async with self.engine.begin() as connection:
            await self._assert_live_document(connection, knowledge_id, document_id)
            await connection.execute(
                text(
                    """
                    INSERT INTO document_profile (
                        document_id, kb_id, pending_payload, pending_source, pending_confidence,
                        pending_state, pending_error_code, pending_error_message, pending_updated_at,
                        content_hash
                    ) VALUES (
                        :document_id, :knowledge_id, CAST(:payload AS jsonb), :source, :confidence,
                        :state, :error_code, :error_message, now(), :content_hash
                    )
                    ON CONFLICT (document_id) DO UPDATE SET
                        pending_payload = EXCLUDED.pending_payload,
                        pending_source = EXCLUDED.pending_source,
                        pending_confidence = EXCLUDED.pending_confidence,
                        pending_state = EXCLUDED.pending_state,
                        pending_error_code = EXCLUDED.pending_error_code,
                        pending_error_message = EXCLUDED.pending_error_message,
                        pending_updated_at = now(), updated_at = now(),
                        content_hash = COALESCE(EXCLUDED.content_hash, document_profile.content_hash)
                    """
                ),
                {
                    "document_id": document_id,
                    "knowledge_id": knowledge_id,
                    "payload": json.dumps(payload, ensure_ascii=False),
                    "source": source,
                    "confidence": confidence,
                    "state": state,
                    "error_code": error_code,
                    "error_message": error_message,
                    "content_hash": content_hash,
                },
            )
        result = await self.document_context(document_id)
        if result is None:  # pragma: no cover - guarded by FK + assertion
            raise ApiError("DOCUMENT_NOT_FOUND", "文档不存在", 404)
        return result

    async def discard_pending(self, knowledge_id: UUID, document_id: UUID | None = None) -> int:
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE document_profile
                    SET pending_payload = NULL, pending_source = NULL, pending_confidence = NULL,
                        pending_state = NULL, pending_error_code = NULL, pending_error_message = NULL,
                        pending_updated_at = now(), updated_at = now()
                    WHERE kb_id = :knowledge_id AND pending_state IS NOT NULL
                      AND (CAST(:document_id AS uuid) IS NULL OR document_id = CAST(:document_id AS uuid))
                    """
                ),
                {"knowledge_id": knowledge_id, "document_id": document_id},
            )
        return result.rowcount or 0

    async def pending_rows(
        self, knowledge_id: UUID, *, connection: AsyncConnection | None = None
    ) -> list[dict[str, Any]]:
        query = text(
            """
            SELECT p.*, d.display_name AS document_name, d.status AS document_status
            FROM document_profile p
            JOIN document_source d ON d.id = p.document_id AND d.kb_id = p.kb_id
            WHERE p.kb_id = :knowledge_id AND p.pending_state IS NOT NULL
            ORDER BY d.display_name, p.document_id
            """
        )
        if connection is not None:
            rows = (
                (await connection.execute(query, {"knowledge_id": knowledge_id})).mappings().all()
            )
        else:
            async with self.engine.connect() as read_connection:
                rows = (
                    (await read_connection.execute(query, {"knowledge_id": knowledge_id}))
                    .mappings()
                    .all()
                )
        return [dict(row) for row in rows]

    async def publish(self, knowledge_id: UUID, report: dict[str, Any]) -> dict[str, Any]:
        async with self.engine.begin() as connection:
            # Serializes a KB publish and locks its staged attributes.  No
            # active matcher row can observe a partially published batch.
            await connection.execute(
                text("SELECT id FROM knowledge_base WHERE id = :id FOR UPDATE"),
                {"id": knowledge_id},
            )
            rows = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT * FROM document_profile
                        WHERE kb_id = :knowledge_id AND pending_state = 'PENDING_ONLINE'
                        FOR UPDATE
                        """
                        ),
                        {"knowledge_id": knowledge_id},
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                document_id = UUID(str(row["document_id"]))
                payload = dict(row["pending_payload"] or {})
                await self._replace_match_terms(connection, knowledge_id, document_id, payload)
                await connection.execute(
                    text(
                        """
                        UPDATE document_profile
                        SET active_payload = pending_payload, active_version = active_version + 1,
                            active_source = pending_source, active_confidence = pending_confidence,
                            active_updated_at = now(), enabled = COALESCE(
                                (pending_payload -> 'intents' -> 0 ->> 'enabled')::boolean, true
                            ), pending_payload = NULL, pending_source = NULL,
                            pending_confidence = NULL, pending_state = NULL,
                            pending_error_code = NULL, pending_error_message = NULL,
                            pending_updated_at = now(), updated_at = now()
                        WHERE document_id = :document_id
                        """
                    ),
                    {"document_id": document_id},
                )
            await connection.execute(
                text(
                    """
                    INSERT INTO profile_publish_event (kb_id, document_count, validation_result)
                    VALUES (:knowledge_id, :document_count, CAST(:report AS jsonb))
                    """
                ),
                {
                    "knowledge_id": knowledge_id,
                    "document_count": len(rows),
                    "report": json.dumps(report, ensure_ascii=False),
                },
            )
        return {"knowledge_id": str(knowledge_id), "published_count": len(rows)}

    async def _replace_match_terms(
        self,
        connection: AsyncConnection,
        knowledge_id: UUID,
        document_id: UUID,
        payload: dict[str, Any],
    ) -> None:
        await connection.execute(
            text("DELETE FROM document_profile_match_term WHERE document_id = :document_id"),
            {"document_id": document_id},
        )
        for intent in self.intents(payload):
            intent_key = str(intent.get("problem_code") or document_id)
            terms: list[tuple[str, str, float]] = [
                ("CANONICAL_QUESTION", str(intent["canonical_question"]), 1.0),
            ]
            terms.extend(("ALIAS", value, 0.95) for value in intent.get("aliases", []))
            for term_type, value, weight in terms:
                normalized = self.normalize(value)
                if not normalized:
                    continue
                await connection.execute(
                    text(
                        """
                        INSERT INTO document_profile_match_term
                            (document_id, kb_id, intent_key, term_type, normalized_term, weight)
                        VALUES (:document_id, :knowledge_id, :intent_key, :term_type, :term, :weight)
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    {
                        "document_id": document_id,
                        "knowledge_id": knowledge_id,
                        "intent_key": intent_key,
                        "term_type": term_type,
                        "term": normalized,
                        "weight": weight,
                    },
                )

    @staticmethod
    async def _assert_live_document(
        connection: AsyncConnection, knowledge_id: UUID, document_id: UUID
    ) -> None:
        row = await connection.scalar(
            text(
                "SELECT 1 FROM document_source WHERE id = :document_id AND kb_id = :knowledge_id AND status <> 'DELETED'"
            ),
            {"document_id": document_id, "knowledge_id": knowledge_id},
        )
        if row is None:
            raise ApiError("DOCUMENT_NOT_FOUND", "文档不存在或不属于该知识库", 404)

    @staticmethod
    def intents(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
        values = (payload or {}).get("intents")
        return [dict(value) for value in values] if isinstance(values, list) else []

    @staticmethod
    def normalize(value: str) -> str:
        return "".join(str(value).lower().split())[:500]

    def _summary(self, row: dict[str, Any]) -> dict[str, Any]:
        active = dict(row["active_payload"]) if row.get("active_payload") else None
        pending = dict(row["pending_payload"]) if row.get("pending_payload") else None
        pending_state = row.get("pending_state")
        if pending_state:
            status = str(pending_state)
        elif active and row.get("enabled", True):
            status = "ONLINE"
        elif active:
            status = "DISABLED"
        else:
            status = "NOT_GENERATED"
        return {
            "document_id": str(row["document_id"]),
            "document_name": row["document_name"],
            "document_status": row["document_status"],
            "profile_status": status,
            "active_profile": active,
            "pending_profile": pending,
            "pending_state": pending_state,
            "pending_error_code": row.get("pending_error_code"),
            "pending_error_message": row.get("pending_error_message"),
            "active_updated_at": row.get("active_updated_at"),
            "pending_updated_at": row.get("pending_updated_at"),
        }
