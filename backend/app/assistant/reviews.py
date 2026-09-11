"""Management-only review records. Historical payloads never come from current indexes."""

from __future__ import annotations

import json
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.common.errors import ApiError


def encoded(value: Any) -> str:
    return json.dumps(jsonable_encoder(value), ensure_ascii=False)


class AnswerReviews:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def _knowledge(self, conn: AsyncConnection, knowledge_id: UUID) -> None:
        row = (
            await conn.execute(
                text(
                    "SELECT id FROM knowledge_base WHERE id = :kb "
                    "AND status <> 'DELETING' FOR SHARE"
                ),
                {"kb": knowledge_id},
            )
        ).first()
        if row is None:
            raise ApiError("KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在或正在删除", 404)

    async def records(
        self,
        knowledge_id: UUID,
        *,
        marked: bool,
        status: str | None,
        query: str,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        # Saved cases remain browsable even after the source request expires.
        sql = """
            WITH records AS (
                SELECT r.id AS request_id, r.created_at, r.status AS request_status,
                       r.external_user_id,
                       COALESCE(m.content, t.question,
                            r.input_payload->>'question', '') AS question,
                       COALESCE(r.terminal_response->>'answer', '') AS answer,
                       r.terminal_response->>'response_type' AS response_type,
                       r.terminal_response->'document' AS document,
                       r.replay_count, v.id AS review_id, v.status AS review_status
                FROM assistant_request r
                LEFT JOIN message m ON m.id = r.user_message_id
                LEFT JOIN rag_trace t ON t.trace_id = r.trace_id AND t.kb_id = r.knowledge_id
                LEFT JOIN answer_review v ON v.request_id = r.id AND v.knowledge_id = r.knowledge_id
                WHERE r.knowledge_id = :kb
                UNION ALL
                SELECT v.request_id, (v.snapshot->>'created_at')::timestamptz,
                       v.snapshot->>'status', v.snapshot->>'external_user_id',
                       v.snapshot->>'question', v.snapshot->'response'->>'answer',
                       v.snapshot->'response'->>'response_type',
                       v.snapshot->'response'->'document',
                       0, v.id, v.status
                FROM answer_review v
                WHERE v.knowledge_id = :kb AND NOT EXISTS
                    (SELECT 1 FROM assistant_request r WHERE r.id = v.request_id)
            )
        """
        where = (
            " WHERE (:marked = false OR review_id IS NOT NULL) "
            "AND (CAST(:status AS text) IS NULL OR review_status = :status) "
            "AND (:query = '' OR strpos(lower(question), lower(:query)) > 0)"
        )
        params = {
            "kb": knowledge_id,
            "marked": marked,
            "status": status,
            "query": query,
            "limit": limit,
            "offset": offset,
        }
        async with self.engine.connect() as conn:
            total = (
                await conn.execute(text(sql + "SELECT count(*) FROM records" + where), params)
            ).scalar_one()
            rows = (
                (
                    await conn.execute(
                        text(
                            sql
                            + "SELECT * FROM records"
                            + where
                            + " ORDER BY created_at DESC, request_id DESC "
                            "LIMIT :limit OFFSET :offset"
                        ),
                        params,
                    )
                )
                .mappings()
                .all()
            )
        return {"items": [dict(row) for row in rows], "total": total}

    async def _snapshot(self, conn: AsyncConnection, kb: UUID, request_id: UUID) -> dict[str, Any]:
        row = (
            (
                await conn.execute(
                    text("""
            SELECT r.*,
                   COALESCE(m.content, t.question,
                            r.input_payload->>'question', '') AS question,
                   m.sequence_no AS user_sequence,
                   t.index_id, t.engine, t.runtime_snapshot, t.retrieval_result,
                   t.rerank_result, t.latency, t.redacted_at
            FROM assistant_request r
            LEFT JOIN message m ON m.id = r.user_message_id
            LEFT JOIN rag_trace t ON t.trace_id = r.trace_id AND t.kb_id = r.knowledge_id
            WHERE r.id = :id AND r.knowledge_id = :kb
        """),
                    {"id": request_id, "kb": kb},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ApiError("ANSWER_RECORD_NOT_FOUND", "回答记录不存在或已过期", 404)
        history = []
        if row["user_sequence"] is not None:
            messages = (
                (
                    await conn.execute(
                        text("""
                SELECT m.id, m.sequence_no, m.role, m.content, m.response_payload, m.status,
                       COALESCE(r.input_payload, '{}'::jsonb) AS input_payload
                FROM message m LEFT JOIN assistant_request r ON r.user_message_id = m.id
                WHERE m.conversation_id = :conv AND m.sequence_no < :seq
                ORDER BY m.sequence_no
            """),
                        {"conv": row["conversation_id"], "seq": row["user_sequence"]},
                    )
                )
                .mappings()
                .all()
            )
            history = [dict(item) for item in messages]
        return cast(
            dict[str, Any],
            jsonable_encoder(
                {
                    "request_id": row["id"],
                    "knowledge_id": kb,
                    "created_at": row["created_at"],
                    "status": row["status"],
                    "question": row["question"],
                    "input_payload": row["input_payload"],
                    "external_user_id": row["external_user_id"],
                    "conversation_id": row["conversation_id"],
                    "message_id": row["assistant_message_id"],
                    "trace_id": row["trace_id"],
                    "response": row["terminal_response"],
                    "error": row["terminal_error"],
                    "history": history,
                    "history_complete": bool(row["user_sequence"])
                    and (
                        [m["sequence_no"] for m in history] == list(range(1, row["user_sequence"]))
                        and all(m["status"] == "COMPLETED" for m in history)
                    ),
                    "diagnostics": {
                        key: row[key]
                        for key in (
                            "index_id",
                            "engine",
                            "runtime_snapshot",
                            "retrieval_result",
                            "rerank_result",
                            "latency",
                            "redacted_at",
                        )
                    },
                }
            ),
        )

    async def detail(self, knowledge_id: UUID, request_id: UUID) -> dict[str, Any]:
        async with self.engine.connect() as conn:
            review = (
                (
                    await conn.execute(
                        text(
                            "SELECT * FROM answer_review WHERE knowledge_id = :kb "
                            "AND request_id = :id"
                        ),
                        {"kb": knowledge_id, "id": request_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if review:
                runs = (
                    (
                        await conn.execute(
                            text(
                                "SELECT * FROM answer_retest WHERE review_id = :id "
                                "ORDER BY created_at DESC LIMIT 20"
                            ),
                            {"id": review["id"]},
                        )
                    )
                    .mappings()
                    .all()
                )
                return {
                    "snapshot": review["snapshot"],
                    "review": dict(review),
                    "retests": [dict(run) for run in runs],
                }
            return {
                "snapshot": await self._snapshot(conn, knowledge_id, request_id),
                "review": None,
                "retests": [],
            }

    async def save(self, kb: UUID, request_id: UUID, values: dict[str, Any]) -> dict[str, Any]:
        async with self.engine.begin() as conn:
            await self._knowledge(conn, kb)
            # Serialize first saves as well as updates without a request FK.
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": str(request_id)},
            )
            existing = (
                (
                    await conn.execute(
                        text(
                            "SELECT * FROM answer_review WHERE knowledge_id = :kb "
                            "AND request_id = :id FOR UPDATE"
                        ),
                        {"kb": kb, "id": request_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            revision = existing["revision"] if existing else 0
            if revision != values["revision"]:
                raise ApiError("ANSWER_REVIEW_CONFLICT", "标记已被修改，请重新加载后保存", 409)
            snapshot = (
                existing["snapshot"] if existing else await self._snapshot(conn, kb, request_id)
            )
            if snapshot["status"] == "RUNNING":
                raise ApiError("ANSWER_NOT_FINISHED", "请等待回答完成再标记", 409)
            title = None
            doc_id = values["expected_document_id"]
            if doc_id:
                title = (
                    await conn.execute(
                        text(
                            "SELECT COALESCE(display_name, filename) FROM document_source "
                            "WHERE id = :id AND kb_id = :kb AND status = 'STORED' FOR SHARE"
                        ),
                        {"id": doc_id, "kb": kb},
                    )
                ).scalar_one_or_none()
                if title is None:
                    raise ApiError(
                        "EXPECTED_DOCUMENT_NOT_FOUND", "预期文档不在当前知识库或已删除", 422
                    )
            params = {
                **values,
                "id": existing["id"] if existing else uuid4(),
                "kb": kb,
                "request": request_id,
                "snapshot": encoded(snapshot),
                "title": title,
            }
            await conn.execute(
                text("""
                INSERT INTO answer_review (id, knowledge_id, request_id, snapshot, category, status,
                    expected_document_id, expected_document_title, expectation, notes)
                VALUES (:id, :kb, :request, CAST(:snapshot AS jsonb), :category, :status,
                    :expected_document_id, :title, :expectation, :notes)
                ON CONFLICT (request_id) DO UPDATE SET category = EXCLUDED.category,
                    status = EXCLUDED.status, expected_document_id = EXCLUDED.expected_document_id,
                    expected_document_title = EXCLUDED.expected_document_title,
                    expectation = EXCLUDED.expectation, notes = EXCLUDED.notes,
                    revision = answer_review.revision + 1, updated_at = now()
            """),
                params,
            )
        return await self.detail(kb, request_id)

    async def reserve_retest(
        self, kb: UUID, review_id: UUID, run_id: UUID, revision: int
    ) -> tuple[dict[str, Any], bool]:
        async with self.engine.begin() as conn:
            await self._knowledge(conn, kb)
            review = (
                (
                    await conn.execute(
                        text(
                            "SELECT * FROM answer_review WHERE id = :id "
                            "AND knowledge_id = :kb FOR UPDATE"
                        ),
                        {"id": review_id, "kb": kb},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if review is None:
                raise ApiError("ANSWER_REVIEW_NOT_FOUND", "问题标记不存在", 404)
            old = (
                (
                    await conn.execute(
                        text("SELECT * FROM answer_retest WHERE id = :id"), {"id": run_id}
                    )
                )
                .mappings()
                .one_or_none()
            )
            if old:
                if old["review_id"] != review_id or old["review_revision"] != revision:
                    raise ApiError("RETEST_CONFLICT", "复测请求标识已被使用", 409)
                return dict(old), False
            if review["revision"] != revision:
                raise ApiError("ANSWER_REVIEW_CONFLICT", "问题标记已变化，请重新加载", 409)
            # A killed process cannot leave the review permanently locked.
            await conn.execute(
                text(
                    "UPDATE answer_retest SET status = 'FAILED', "
                    "error = '复测执行中断，请重新发起', finished_at = now() "
                    "WHERE review_id = :id AND status = 'RUNNING' "
                    "AND created_at < now() - interval '5 minutes'"
                ),
                {"id": review_id},
            )
            running = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM answer_retest WHERE review_id = :id AND status = 'RUNNING'"
                    ),
                    {"id": review_id},
                )
            ).first()
            if running:
                raise ApiError("RETEST_RUNNING", "该问题正在复测，请稍后刷新结果", 409)
            snapshot = review["snapshot"]
            if not snapshot["question"] or not snapshot["history_complete"]:
                raise ApiError(
                    "RETEST_CONTEXT_MISSING", "原始问题或前置对话不完整，无法可靠复测", 409
                )
            questions = [m["content"] for m in snapshot["history"] if m["role"] == "USER"]
            if len(questions) >= 20:
                raise ApiError("RETEST_TOO_LONG", "单题复测最多支持包含前文的20轮对话", 422)
            await conn.execute(
                text("""
                INSERT INTO answer_retest (id, review_id, review_revision, expectation, status)
                VALUES (:id, :review, :revision, CAST(:expectation AS jsonb), 'RUNNING')
            """),
                {
                    "id": run_id,
                    "review": review_id,
                    "revision": revision,
                    "expectation": encoded(
                        {
                            k: review[k]
                            for k in (
                                "expected_document_id",
                                "expected_document_title",
                                "expectation",
                            )
                        }
                    ),
                },
            )
            return dict(review), True

    async def finish_retest(
        self, run_id: UUID, turns: list[dict[str, Any]], error: str | None
    ) -> dict[str, Any]:
        async with self.engine.begin() as conn:
            row = (
                (
                    await conn.execute(
                        text("""
                UPDATE answer_retest SET turns = CAST(:turns AS jsonb), status = :status,
                    error = :error, finished_at = now() WHERE id = :id AND status = 'RUNNING'
                RETURNING *
            """),
                        {
                            "id": run_id,
                            "turns": encoded(turns),
                            "error": error,
                            "status": "FAILED" if error else "COMPLETED",
                        },
                    )
                )
                .mappings()
                .one()
            )
        return dict(row)

    async def diagnostics(self, kb: UUID, trace_id: str | None) -> dict[str, Any]:
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        text(
                            "SELECT index_id, engine, runtime_snapshot, latency "
                            "FROM rag_trace WHERE trace_id = :id AND kb_id = :kb"
                        ),
                        {"id": UUID(trace_id) if trace_id else None, "kb": kb},
                    )
                )
                .mappings()
                .one_or_none()
            )
        return dict(row) if row else {}
