"""Persisted failure telemetry for post-launch review.

Only failed API requests are stored (see ADR-free rationale in migration
0019): successful traffic volume is already covered by /metrics, while
failures are what an operator or the integration side actually needs to
look up by request_id.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.common.errors import ApiError
from app.rag.metrics import MetricsRepository

logger = structlog.get_logger(__name__)

_SUMMARY_GROUP_LIMIT = 20


class ObservabilityRepository:
    """Raw-SQL access to request_error_log plus failure aggregations.

    Volume note: request_error_log only ever holds failures and is pruned by
    the retention job, so (created_at DESC) plus request_id lookups stay cheap
    without rollup tables.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def insert_request_error(
        self,
        *,
        request_id: str,
        method: str,
        path: str,
        status_code: int,
        error_code: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> UUID:
        error_id = uuid4()
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO request_error_log (
                        id, request_id, method, path, status_code,
                        error_code, message, detail
                    ) VALUES (
                        :id, :request_id, :method, :path, :status_code,
                        :error_code, :message, CAST(:detail AS jsonb)
                    )
                    """
                ),
                {
                    "id": error_id,
                    "request_id": request_id[:128],
                    "method": method[:10],
                    "path": path,
                    "status_code": status_code,
                    "error_code": error_code[:100],
                    "message": message,
                    "detail": _json(detail or {}),
                },
            )
        return error_id

    async def list_request_errors(
        self,
        *,
        request_id: str | None = None,
        path: str | None = None,
        status_code: int | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        clauses = ["1 = 1"]
        parameters: dict[str, Any] = {"limit": limit, "offset": offset}
        if request_id is not None:
            clauses.append("request_id = :request_id")
            parameters["request_id"] = request_id
        if path is not None:
            clauses.append("path ILIKE :path")
            parameters["path"] = f"%{path}%"
        if status_code is not None:
            clauses.append("status_code = :status_code")
            parameters["status_code"] = status_code
        if created_from is not None:
            clauses.append("created_at >= :created_from")
            parameters["created_from"] = created_from
        if created_to is not None:
            clauses.append("created_at < :created_to")
            parameters["created_to"] = created_to
        where = " AND ".join(clauses)
        async with self.engine.connect() as connection:
            total = (
                await connection.execute(
                    text(f"SELECT count(*) FROM request_error_log WHERE {where}"),
                    parameters,
                )
            ).scalar_one()
            rows = (
                await connection.execute(
                    text(
                        f"""
                        SELECT id, request_id, method, path, status_code,
                               error_code, message, created_at
                        FROM request_error_log
                        WHERE {where}
                        ORDER BY created_at DESC
                        LIMIT :limit OFFSET :offset
                        """
                    ),
                    parameters,
                )
            ).mappings()
            items = [dict(row) for row in rows]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    async def get_request_error(self, error_id: UUID) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text("SELECT * FROM request_error_log WHERE id = :id"),
                {"id": error_id},
            )
            row = result.mappings().one_or_none()
        if row is None:
            raise ApiError("REQUEST_ERROR_LOG_NOT_FOUND", "请求错误日志不存在", 404)
        return dict(row)

    async def failure_summary(
        self,
        *,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        knowledge_id: UUID | None = None,
    ) -> dict[str, Any]:
        # Shared with /metrics so both views agree on the default window and
        # the 92-day ceiling.
        start, end = MetricsRepository.resolve_range(created_from, created_to)
        trace_clauses = ["status = 'FAILED'", "created_at >= :start", "created_at < :end"]
        parameters: dict[str, Any] = {"start": start, "end": end}
        if knowledge_id is not None:
            trace_clauses.append("kb_id = :kb_id")
            parameters["kb_id"] = knowledge_id
        trace_where = " AND ".join(trace_clauses)
        async with self.engine.connect() as connection:
            trace_failures = [
                dict(row)
                for row in (
                    await connection.execute(
                        text(
                            f"""
                            SELECT COALESCE(error ->> 'code', 'UNKNOWN') AS error_code,
                                   count(*) AS count,
                                   max(created_at) AS last_seen
                            FROM rag_trace
                            WHERE {trace_where}
                            GROUP BY 1
                            ORDER BY count DESC
                            """
                        ),
                        parameters,
                    )
                ).mappings()
            ]
            request_errors = [
                dict(row)
                for row in (
                    await connection.execute(
                        text(
                            """
                            SELECT path, status_code, count(*) AS count,
                                   max(created_at) AS last_seen
                            FROM request_error_log
                            WHERE created_at >= :start AND created_at < :end
                            GROUP BY path, status_code
                            ORDER BY count DESC
                            LIMIT :group_limit
                            """
                        ),
                        {**parameters, "group_limit": _SUMMARY_GROUP_LIMIT},
                    )
                ).mappings()
            ]
            by_day = [
                dict(row)
                for row in (
                    await connection.execute(
                        text(
                            """
                            SELECT to_char(date_trunc('day', created_at), 'YYYY-MM-DD') AS day,
                                   count(*) AS count
                            FROM request_error_log
                            WHERE created_at >= :start AND created_at < :end
                            GROUP BY 1
                            ORDER BY day
                            """
                        ),
                        parameters,
                    )
                ).mappings()
            ]
        return {
            "from": start,
            "to": end,
            "trace_failures": trace_failures,
            "request_errors": request_errors,
            "request_errors_by_day": by_day,
        }

    async def delete_expired(self, *, retention_days: int) -> int:
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    DELETE FROM request_error_log
                    WHERE created_at < now() - make_interval(days => :days)
                    """
                ),
                {"days": retention_days},
            )
        deleted = int(result.rowcount or 0)
        if deleted:
            logger.info("request_error_log_pruned", deleted=deleted, retention_days=retention_days)
        return deleted


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)
