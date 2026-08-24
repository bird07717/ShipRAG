from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.common.errors import ApiError

# Latency stages surfaced in the stage breakdown. total_ms is handled
# separately (percentiles); these are averages.
_STAGE_KEYS = (
    "prepare_turn_ms",
    "query_rewrite_ms",
    "query_embedding_ms",
    "bm25_retrieval_ms",
    "vector_retrieval_ms",
    "fusion_ms",
    "rerank_ms",
    "context_expansion_ms",
    "prompt_ms",
    "llm_ms",
)

_MAX_RANGE_DAYS = 92


class MetricsRepository:
    """Aggregations over rag_trace for server-side usage / quality analytics.

    All metrics are computed on the fly: the existing (kb_id, created_at) and
    (mode, status, created_at) indexes cover the filtered scans at the
    volumes this service sees per launch. When daily trace counts grow past
    the point where percentile_cont gets slow, swap the implementation for a
    maintenance-queue rollup table — the API shape stays unchanged.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    @staticmethod
    def resolve_range(
        created_from: datetime | None, created_to: datetime | None
    ) -> tuple[datetime, datetime]:
        # Naive inputs are interpreted as local time (same semantics asyncpg
        # applies to naive datetime bind params), so mixed naive/aware
        # callers cannot hit a TypeError mid-comparison.
        now = datetime.now(UTC)
        start = _as_aware(created_from) or now - timedelta(days=7)
        end = _as_aware(created_to) or now
        if start >= end:
            raise ApiError("VALIDATION_ERROR", "时间范围无效：from 必须早于 to", 422)
        if end - start > timedelta(days=_MAX_RANGE_DAYS):
            raise ApiError("VALIDATION_ERROR", "时间范围不能超过 92 天", 422)
        return start, end

    @staticmethod
    def _filters(
        knowledge_id: Any,
        mode: str,
        start: datetime,
        end: datetime,
    ) -> tuple[str, dict[str, Any]]:
        clauses = ["mode = :mode", "created_at >= :start", "created_at < :end"]
        parameters: dict[str, Any] = {"mode": mode, "start": start, "end": end}
        if knowledge_id is not None:
            clauses.append("kb_id = :kb_id")
            parameters["kb_id"] = knowledge_id
        return " AND ".join(clauses), parameters

    async def overview(
        self,
        *,
        knowledge_id: Any = None,
        mode: str = "CHAT",
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> dict[str, Any]:
        start, end = self.resolve_range(created_from, created_to)
        where, parameters = self._filters(knowledge_id, mode, start, end)
        query = f"""
            SELECT
                count(*) AS total,
                count(*) FILTER (WHERE status = 'COMPLETED') AS completed,
                count(*) FILTER (WHERE status = 'FAILED') AS failed,
                count(*) FILTER (WHERE status = 'CANCELLED') AS cancelled,
                count(*) FILTER (WHERE response_type = 'ANSWERED') AS answered,
                count(*) FILTER (WHERE response_type = 'UNCONFIRMED') AS unconfirmed,
                count(*) FILTER (WHERE response_type = 'OUT_OF_SCOPE') AS out_of_scope,
                count(*) FILTER (WHERE response_type = 'DOC_DELIVERED') AS doc_delivered,
                count(*) FILTER (WHERE response_type IS NOT NULL) AS with_response_type,
                count(DISTINCT user_id) AS unique_users,
                count(*) FILTER (
                    WHERE citation_result ->> 'citation_missing' = 'true'
                ) AS citation_missing,
                count(*) FILTER (
                    WHERE rerank_result ->> 'status' = 'DEGRADED'
                ) AS rerank_degraded,
                COALESCE(percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY (latency ->> 'total_ms')::bigint
                ), 0)::bigint AS p50_ms,
                COALESCE(percentile_cont(0.95) WITHIN GROUP (
                    ORDER BY (latency ->> 'total_ms')::bigint
                ), 0)::bigint AS p95_ms,
                COALESCE(percentile_cont(0.99) WITHIN GROUP (
                    ORDER BY (latency ->> 'total_ms')::bigint
                ), 0)::bigint AS p99_ms,
                COALESCE(avg((latency ->> 'total_ms')::bigint), 0)::bigint AS avg_ms,
                COALESCE(sum((model_usage ->> 'prompt_tokens')::bigint), 0) AS llm_prompt_tokens,
                COALESCE(sum((model_usage ->> 'completion_tokens')::bigint), 0)
                    AS llm_completion_tokens,
                COALESCE(sum((rerank_result -> 'usage' ->> 'total_tokens')::bigint), 0)
                    AS rerank_tokens
            FROM rag_trace
            WHERE {where}
        """
        async with self.engine.connect() as connection:
            result = await connection.execute(text(query), parameters)
            row = dict(result.mappings().one())
        answered_base = int(row["with_response_type"])
        no_answer = int(row["unconfirmed"]) + int(row["out_of_scope"])
        return {
            "range": {"from": start.isoformat(), "to": end.isoformat(), "mode": mode},
            "total_traces": int(row["total"]),
            "completed": int(row["completed"]),
            "failed": int(row["failed"]),
            "cancelled": int(row["cancelled"]),
            "response_type_counts": {
                "ANSWERED": int(row["answered"]),
                "UNCONFIRMED": int(row["unconfirmed"]),
                "OUT_OF_SCOPE": int(row["out_of_scope"]),
                "DOC_DELIVERED": int(row["doc_delivered"]),
            },
            "classified_traces": answered_base,
            "no_answer_rate": _rate(no_answer, answered_base),
            "doc_delivered_rate": _rate(int(row["doc_delivered"]), answered_base),
            "failure_rate": _rate(int(row["failed"]), int(row["total"])),
            "citation_missing_rate": _rate(int(row["citation_missing"]), int(row["completed"])),
            "rerank_degraded_rate": _rate(int(row["rerank_degraded"]), int(row["total"])),
            "unique_users": int(row["unique_users"]),
            "latency": {
                "p50_ms": int(row["p50_ms"]),
                "p95_ms": int(row["p95_ms"]),
                "p99_ms": int(row["p99_ms"]),
                "avg_ms": int(row["avg_ms"]),
            },
            "tokens": {
                "llm_prompt": int(row["llm_prompt_tokens"]),
                "llm_completion": int(row["llm_completion_tokens"]),
                "llm_total": int(row["llm_prompt_tokens"]) + int(row["llm_completion_tokens"]),
                "rerank_total": int(row["rerank_tokens"]),
            },
        }

    async def timeseries(
        self,
        *,
        knowledge_id: Any = None,
        mode: str = "CHAT",
        granularity: str = "day",
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> list[dict[str, Any]]:
        start, end = self.resolve_range(created_from, created_to)
        where, parameters = self._filters(knowledge_id, mode, start, end)
        trunc = "hour" if granularity == "hour" else "day"
        parameters["trunc"] = trunc
        query = f"""
            SELECT date_trunc(:trunc, created_at) AS bucket,
                   count(*) AS queries,
                   count(*) FILTER (WHERE status = 'FAILED') AS failed,
                   count(*) FILTER (WHERE response_type = 'ANSWERED') AS answered,
                   count(*) FILTER (WHERE response_type = 'UNCONFIRMED') AS unconfirmed,
                   count(*) FILTER (WHERE response_type = 'OUT_OF_SCOPE') AS out_of_scope,
                   count(*) FILTER (WHERE response_type = 'DOC_DELIVERED') AS doc_delivered,
                   COALESCE(percentile_cont(0.95) WITHIN GROUP (
                       ORDER BY (latency ->> 'total_ms')::bigint
                   ), 0)::bigint AS p95_ms,
                   COALESCE(sum((model_usage ->> 'total_tokens')::bigint), 0) AS llm_tokens,
                   COALESCE(sum((rerank_result -> 'usage' ->> 'total_tokens')::bigint), 0)
                       AS rerank_tokens
            FROM rag_trace
            WHERE {where}
            GROUP BY bucket
            ORDER BY bucket
        """
        async with self.engine.connect() as connection:
            result = await connection.execute(text(query), parameters)
            rows = [dict(row) for row in result.mappings()]
        points: list[dict[str, Any]] = []
        for row in rows:
            classified = (
                int(row["answered"])
                + int(row["unconfirmed"])
                + int(row["out_of_scope"])
                + int(row["doc_delivered"])
            )
            no_answer = int(row["unconfirmed"]) + int(row["out_of_scope"])
            points.append(
                {
                    "bucket": row["bucket"].isoformat(),
                    "queries": int(row["queries"]),
                    "failed": int(row["failed"]),
                    "answered": int(row["answered"]),
                    "unconfirmed": int(row["unconfirmed"]),
                    "out_of_scope": int(row["out_of_scope"]),
                    "doc_delivered": int(row["doc_delivered"]),
                    "no_answer_rate": _rate(no_answer, classified),
                    "p95_ms": int(row["p95_ms"]),
                    "llm_tokens": int(row["llm_tokens"]),
                    "rerank_tokens": int(row["rerank_tokens"]),
                }
            )
        return points

    async def latency(
        self,
        *,
        knowledge_id: Any = None,
        mode: str = "CHAT",
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> dict[str, Any]:
        start, end = self.resolve_range(created_from, created_to)
        where, parameters = self._filters(knowledge_id, mode, start, end)
        stage_select = ",\n".join(
            f"COALESCE(avg((latency ->> '{key}')::bigint), 0)::bigint AS {key}"
            for key in _STAGE_KEYS
        )
        query = f"""
            SELECT
                COALESCE(percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY (latency ->> 'total_ms')::bigint
                ), 0)::bigint AS p50_ms,
                COALESCE(percentile_cont(0.95) WITHIN GROUP (
                    ORDER BY (latency ->> 'total_ms')::bigint
                ), 0)::bigint AS p95_ms,
                COALESCE(percentile_cont(0.99) WITHIN GROUP (
                    ORDER BY (latency ->> 'total_ms')::bigint
                ), 0)::bigint AS p99_ms,
                COALESCE(avg((latency ->> 'total_ms')::bigint), 0)::bigint AS avg_ms,
                {stage_select}
            FROM rag_trace
            WHERE {where}
        """
        async with self.engine.connect() as connection:
            result = await connection.execute(text(query), parameters)
            row = dict(result.mappings().one())
        return {
            "range": {"from": start.isoformat(), "to": end.isoformat(), "mode": mode},
            "total": {
                "p50_ms": int(row["p50_ms"]),
                "p95_ms": int(row["p95_ms"]),
                "p99_ms": int(row["p99_ms"]),
                "avg_ms": int(row["avg_ms"]),
            },
            "stages": {key: int(row[key]) for key in _STAGE_KEYS},
        }


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.astimezone()


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)
