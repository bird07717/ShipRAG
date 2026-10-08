from __future__ import annotations

from collections.abc import Mapping
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

# Every input-guard short circuit answers deterministically, while the
# CONVERSATION path intentionally skips RAG. Neither is a sample of technical
# retrieval/generation latency. Both still count as real operational requests.
_LATENCY_SAMPLE = (
    "citation_result ->> 'shortcut' IS NULL "
    "AND COALESCE(citation_result ->> 'response_path', 'KNOWLEDGE') <> 'CONVERSATION'"
)
# Greetings and conversation-control acknowledgements are real requests for
# operational volume, but they are not technical questions. Low-information
# guards intentionally remain technical signals because the following turn
# may supply the requested condition.
# RAG V2 records stage outcomes in runtime_snapshot.generation; the legacy
# rerank_result / citation_result columns are not written by V2, so quality
# signals for current traffic must be read from these paths.
_V2_QUERY_STATUS = "runtime_snapshot #>> '{generation,query_interpreter,status}'"
_V2_RERANK_STATUS = "runtime_snapshot #>> '{generation,retrieval,document_rerank,status}'"
_V2_RERANK_REASON = "runtime_snapshot #>> '{generation,retrieval,document_rerank,reason}'"
# A Rerank row without a usable key makes create_rerank_provider raise
# MODEL_NOT_CONFIGURED inside the retrieval try-block, which is recorded as
# DEGRADED. That is a configuration problem, not a failed call, so it is
# counted as "unconfigured" and kept out of the call-failure rate.
_V2_RERANK_CALL_FAILURE = f"COALESCE({_V2_RERANK_REASON}, '') <> 'MODEL_NOT_CONFIGURED'"
_TECHNICAL_SAMPLE = (
    "COALESCE(citation_result ->> 'shortcut', '') NOT IN ('GREETING', 'META_ACK') "
    "AND COALESCE(citation_result ->> 'response_path', 'KNOWLEDGE') <> 'CONVERSATION'"
)


def _total_latency_select(samples_alias: str = "latency_samples") -> str:
    """Percentiles, average and sample count over non-shortcut turns only."""

    total = "(latency ->> 'total_ms')::bigint"
    keep = f"FILTER (WHERE {_LATENCY_SAMPLE})"
    percentiles = ",\n".join(
        f"""COALESCE(percentile_cont({quantile}) WITHIN GROUP (
                    ORDER BY {total}
                ) {keep}, 0)::bigint AS p{label}_ms"""
        for quantile, label in ((0.5, "50"), (0.95, "95"), (0.99, "99"))
    )
    return (
        f"{percentiles},\n"
        f"                COALESCE(avg({total}) {keep}, 0)::bigint AS avg_ms,\n"
        f"                count(*) {keep} AS {samples_alias}"
    )


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
        *,
        engine: str | None = None,
        source: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        clauses = [
            "mode = :mode",
            "created_at >= :start",
            "created_at < :end",
        ]
        parameters: dict[str, Any] = {"mode": mode, "start": start, "end": end}
        if knowledge_id is not None:
            clauses.append("kb_id = :kb_id")
            parameters["kb_id"] = knowledge_id
        if engine is not None:
            clauses.append("engine = :engine")
            parameters["engine"] = engine
        if source is not None:
            clauses.append("source = :source")
            parameters["source"] = source
        return " AND ".join(clauses), parameters

    async def overview(
        self,
        *,
        knowledge_id: Any = None,
        mode: str = "CHAT",
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        engine: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        start, end = self.resolve_range(created_from, created_to)
        where, parameters = self._filters(
            knowledge_id,
            mode,
            start,
            end,
            engine=engine,
            source=source,
        )
        query = f"""
            SELECT
                count(*) AS total,
                count(*) FILTER (WHERE status = 'COMPLETED') AS completed,
                count(*) FILTER (WHERE status = 'FAILED') AS failed,
                count(*) FILTER (WHERE status = 'CANCELLED') AS cancelled,
                count(*) FILTER (WHERE {_TECHNICAL_SAMPLE}) AS technical_total,
                count(*) FILTER (
                    WHERE {_TECHNICAL_SAMPLE} AND status = 'COMPLETED'
                ) AS technical_completed,
                count(*) FILTER (
                    WHERE {_TECHNICAL_SAMPLE} AND response_type = 'ANSWERED'
                ) AS answered,
                count(*) FILTER (
                    WHERE {_TECHNICAL_SAMPLE} AND response_type = 'UNCONFIRMED'
                ) AS unconfirmed,
                count(*) FILTER (
                    WHERE {_TECHNICAL_SAMPLE} AND response_type = 'OUT_OF_SCOPE'
                ) AS out_of_scope,
                count(*) FILTER (
                    WHERE {_TECHNICAL_SAMPLE}
                      AND response_type IN ('DOC_DELIVERED', 'DOCUMENT_DELIVERED')
                ) AS doc_delivered,
                count(*) FILTER (
                    WHERE {_TECHNICAL_SAMPLE}
                      AND response_type = 'ANSWERED' AND answer_mode = 'PRODUCT_KNOWLEDGE'
                ) AS product_knowledge,
                count(*) FILTER (
                    WHERE {_TECHNICAL_SAMPLE}
                      AND response_type = 'ANSWERED' AND answer_mode = 'PRODUCT_EXPLAINED'
                ) AS product_explained,
                count(*) FILTER (
                    WHERE {_TECHNICAL_SAMPLE}
                      AND response_type = 'ANSWERED' AND answer_mode = 'PRODUCT_GENERAL'
                ) AS product_general,
                count(DISTINCT user_id) AS unique_users,
                count(*) FILTER (
                    WHERE {_TECHNICAL_SAMPLE}
                      AND citation_result ->> 'citation_missing' = 'true'
                ) AS citation_missing,
                count(*) FILTER (
                    WHERE {_TECHNICAL_SAMPLE}
                      AND rerank_result ->> 'status' = 'DEGRADED'
                ) AS rerank_degraded,
                count(*) FILTER (WHERE {_V2_QUERY_STATUS} IS NOT NULL) AS v2_query_total,
                count(*) FILTER (WHERE {_V2_QUERY_STATUS} = 'DEGRADED') AS v2_query_degraded,
                count(*) FILTER (
                    WHERE {_V2_RERANK_STATUS} = 'PASSED'
                       OR ({_V2_RERANK_STATUS} = 'DEGRADED' AND {_V2_RERANK_CALL_FAILURE})
                ) AS v2_rerank_attempted,
                count(*) FILTER (
                    WHERE {_V2_RERANK_STATUS} = 'DEGRADED' AND {_V2_RERANK_CALL_FAILURE}
                ) AS v2_rerank_degraded,
                count(*) FILTER (
                    WHERE {_V2_RERANK_STATUS} IN ('SKIPPED', 'DEGRADED')
                      AND {_V2_RERANK_REASON} = 'MODEL_NOT_CONFIGURED'
                ) AS v2_rerank_unconfigured,
                {_total_latency_select()},
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
        answered = int(row["answered"])
        unconfirmed = int(row["unconfirmed"])
        out_of_scope = int(row["out_of_scope"])
        doc_delivered = int(row["doc_delivered"])
        product_knowledge = int(row["product_knowledge"])
        product_explained = int(row["product_explained"])
        product_general = int(row["product_general"])
        answer_basis_traces = product_knowledge + product_explained + product_general
        direct_answers = answered + doc_delivered
        in_scope = direct_answers + unconfirmed
        classified = in_scope + out_of_scope
        return {
            "range": {
                "from": start.isoformat(),
                "to": end.isoformat(),
                "mode": mode,
                "engine": engine,
                "source": source,
            },
            "total_traces": int(row["total"]),
            "completed": int(row["completed"]),
            "failed": int(row["failed"]),
            "cancelled": int(row["cancelled"]),
            "response_type_counts": {
                "ANSWERED": answered,
                "UNCONFIRMED": unconfirmed,
                "OUT_OF_SCOPE": out_of_scope,
                "DOC_DELIVERED": doc_delivered,
            },
            "answer_mode_counts": {
                "PRODUCT_KNOWLEDGE": product_knowledge,
                "PRODUCT_EXPLAINED": product_explained,
                "PRODUCT_GENERAL": product_general,
            },
            "answer_basis_traces": answer_basis_traces,
            "product_knowledge_rate": _rate(product_knowledge, answer_basis_traces),
            "product_explained_rate": _rate(product_explained, answer_basis_traces),
            "product_general_rate": _rate(product_general, answer_basis_traces),
            "classified_traces": classified,
            "in_scope_traces": in_scope,
            "direct_answer_rate": _rate(direct_answers, in_scope),
            "needs_information_rate": _rate(unconfirmed, in_scope),
            "out_of_scope_rate": _rate(out_of_scope, classified),
            "doc_delivered_rate": _rate(doc_delivered, classified),
            "failure_rate": _rate(int(row["failed"]), int(row["total"])),
            "citation_missing_rate": _rate(
                int(row["citation_missing"]), int(row["technical_completed"])
            ),
            "rerank_degraded_rate": _rate(int(row["rerank_degraded"]), int(row["technical_total"])),
            "v2_quality": {
                "query_interpreter_total": int(row["v2_query_total"]),
                "query_interpreter_degraded": int(row["v2_query_degraded"]),
                "query_interpreter_degraded_rate": _rate(
                    int(row["v2_query_degraded"]), int(row["v2_query_total"])
                ),
                "document_rerank_attempted": int(row["v2_rerank_attempted"]),
                "document_rerank_degraded": int(row["v2_rerank_degraded"]),
                "document_rerank_degraded_rate": _rate(
                    int(row["v2_rerank_degraded"]), int(row["v2_rerank_attempted"])
                ),
                "document_rerank_unconfigured": int(row["v2_rerank_unconfigured"]),
            },
            "unique_users": int(row["unique_users"]),
            "latency": _latency_summary(row, samples=row["latency_samples"]),
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
        engine: str | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        start, end = self.resolve_range(created_from, created_to)
        where, parameters = self._filters(
            knowledge_id,
            mode,
            start,
            end,
            engine=engine,
            source=source,
        )
        trunc = "hour" if granularity == "hour" else "day"
        parameters["trunc"] = trunc
        query = f"""
            SELECT date_trunc(:trunc, created_at) AS bucket,
                   count(*) AS queries,
                   count(*) FILTER (WHERE status = 'FAILED') AS failed,
                   count(*) FILTER (
                       WHERE {_TECHNICAL_SAMPLE} AND response_type = 'ANSWERED'
                   ) AS answered,
                   count(*) FILTER (
                       WHERE {_TECHNICAL_SAMPLE} AND response_type = 'UNCONFIRMED'
                   ) AS unconfirmed,
                   count(*) FILTER (
                       WHERE {_TECHNICAL_SAMPLE} AND response_type = 'OUT_OF_SCOPE'
                   ) AS out_of_scope,
                   count(*) FILTER (
                       WHERE {_TECHNICAL_SAMPLE}
                         AND response_type IN ('DOC_DELIVERED', 'DOCUMENT_DELIVERED')
                   ) AS doc_delivered,
                   COALESCE(percentile_cont(0.95) WITHIN GROUP (
                       ORDER BY (latency ->> 'total_ms')::bigint
                   ) FILTER (WHERE {_LATENCY_SAMPLE}), 0)::bigint AS p95_ms,
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
            direct_answers = int(row["answered"]) + int(row["doc_delivered"])
            in_scope = direct_answers + int(row["unconfirmed"])
            classified = in_scope + int(row["out_of_scope"])
            points.append(
                {
                    "bucket": row["bucket"].isoformat(),
                    "queries": int(row["queries"]),
                    "failed": int(row["failed"]),
                    "answered": int(row["answered"]),
                    "unconfirmed": int(row["unconfirmed"]),
                    "out_of_scope": int(row["out_of_scope"]),
                    "doc_delivered": int(row["doc_delivered"]),
                    "direct_answer_rate": _rate(direct_answers, in_scope),
                    "needs_information_rate": _rate(int(row["unconfirmed"]), in_scope),
                    "out_of_scope_rate": _rate(int(row["out_of_scope"]), classified),
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
            f"COALESCE(avg((latency ->> '{key}')::bigint) "
            f"FILTER (WHERE {_LATENCY_SAMPLE}), 0)::bigint AS {key}"
            for key in _STAGE_KEYS
        )
        query = f"""
            SELECT
                {_total_latency_select()},
                {stage_select}
            FROM rag_trace
            WHERE {where}
        """
        async with self.engine.connect() as connection:
            result = await connection.execute(text(query), parameters)
            row = dict(result.mappings().one())
        return {
            "range": {"from": start.isoformat(), "to": end.isoformat(), "mode": mode},
            "total": _latency_summary(row, samples=row["latency_samples"]),
            "stages": {key: int(row[key]) for key in _STAGE_KEYS},
        }

    async def support(
        self,
        *,
        knowledge_id: Any = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> dict[str, Any]:
        """Aggregate only traces written by the Support V2 orchestration.

        The application records its Support envelope in ``retrieval_result``.
        This keeps the query backwards-compatible with pre-V2 traces and
        avoids assigning made-up values to metrics that need a labelled set.
        """
        start, end = self.resolve_range(created_from, created_to)
        where, parameters = self._filters(knowledge_id, "CHAT", start, end)
        support_where = f"{where} AND retrieval_result ->> 'support_version' = '1'"
        query = f"""
            SELECT
                count(*) AS support_turns,
                count(*) FILTER (WHERE NOT EXISTS (
                    SELECT 1 FROM rag_trace prior
                    WHERE prior.conversation_id = rag_trace.conversation_id
                      AND prior.created_at < rag_trace.created_at
                      AND prior.retrieval_result ->> 'support_version' = '1'
                )) AS first_turns,
                count(*) FILTER (WHERE EXISTS (
                    SELECT 1 FROM rag_trace prior
                    WHERE prior.conversation_id = rag_trace.conversation_id
                      AND prior.created_at < rag_trace.created_at
                      AND prior.retrieval_result ->> 'support_version' = '1'
                )) AS followup_turns,
                count(*) FILTER (
                    WHERE response_type = 'DOCUMENT_DELIVERED'
                ) AS document_delivered,
                count(*) FILTER (
                    WHERE response_type = 'CLARIFICATION_REQUIRED'
                ) AS clarification_required,
                count(*) FILTER (WHERE response_type = 'NO_MATCH') AS no_match,
                count(*) FILTER (
                    WHERE response_type = 'ESCALATION_REQUIRED'
                ) AS escalation_required,
                count(*) FILTER (WHERE retrieval_result -> 'problem_resolution' ->> 'method'
                    IN ('CANONICAL_QUESTION', 'ALIAS')) AS canonical_or_alias_matches,
                count(*) FILTER (WHERE retrieval_result -> 'problem_resolution' ->> 'method'
                    = 'LLM_CATALOG') AS llm_catalog_matches,
                count(*) FILTER (WHERE COALESCE(
                    (retrieval_result ->> 'general_guidance_count')::integer, 0
                ) > 0) AS general_guidance_turns,
                count(*) FILTER (WHERE error ->> 'code' = 'MISSING_EVIDENCE') AS evidence_missing,
                count(*) FILTER (
                    WHERE retrieval_result ->> 'high_risk_intercepted' = 'true'
                ) AS high_risk_intercepts,
                COALESCE(sum(COALESCE(
                    (retrieval_result ->> 'structured_output_retries')::bigint, 0
                )), 0)::bigint AS structured_output_retries,
                count(*) FILTER (
                    WHERE error ->> 'code' = 'WORKFLOW_CONFLICT'
                ) AS workflow_conflicts,
                COALESCE(sum(COALESCE(
                    (retrieval_result ->> 'model_calls')::bigint, 0
                )), 0)::bigint AS model_calls,
                count(*) FILTER (WHERE response_type = 'CLARIFICATION_REQUIRED' AND EXISTS (
                    SELECT 1 FROM rag_trace later
                    WHERE later.conversation_id = rag_trace.conversation_id
                      AND later.created_at > rag_trace.created_at
                      AND later.retrieval_result ->> 'support_version' = '1'
                      AND later.status = 'COMPLETED'
                      AND later.response_type IN (
                          'DOCUMENT_DELIVERED', 'ANSWERED', 'ESCALATION_REQUIRED'
                      )
                )) AS clarification_resolved,
                COALESCE(percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY (latency ->> 'total_ms')::bigint
                ) FILTER (WHERE NOT EXISTS (
                    SELECT 1 FROM rag_trace prior
                    WHERE prior.conversation_id = rag_trace.conversation_id
                      AND prior.created_at < rag_trace.created_at
                      AND prior.retrieval_result ->> 'support_version' = '1'
                )), 0)::bigint AS first_p50_ms,
                COALESCE(percentile_cont(0.95) WITHIN GROUP (
                    ORDER BY (latency ->> 'total_ms')::bigint
                ) FILTER (WHERE NOT EXISTS (
                    SELECT 1 FROM rag_trace prior
                    WHERE prior.conversation_id = rag_trace.conversation_id
                      AND prior.created_at < rag_trace.created_at
                      AND prior.retrieval_result ->> 'support_version' = '1'
                )), 0)::bigint AS first_p95_ms,
                COALESCE(percentile_cont(0.99) WITHIN GROUP (
                    ORDER BY (latency ->> 'total_ms')::bigint
                ) FILTER (WHERE NOT EXISTS (
                    SELECT 1 FROM rag_trace prior
                    WHERE prior.conversation_id = rag_trace.conversation_id
                      AND prior.created_at < rag_trace.created_at
                      AND prior.retrieval_result ->> 'support_version' = '1'
                )), 0)::bigint AS first_p99_ms,
                COALESCE(avg((latency ->> 'total_ms')::bigint) FILTER (WHERE NOT EXISTS (
                    SELECT 1 FROM rag_trace prior
                    WHERE prior.conversation_id = rag_trace.conversation_id
                      AND prior.created_at < rag_trace.created_at
                      AND prior.retrieval_result ->> 'support_version' = '1'
                )), 0)::bigint AS first_avg_ms,
                COALESCE(percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY (latency ->> 'total_ms')::bigint
                ) FILTER (WHERE EXISTS (
                    SELECT 1 FROM rag_trace prior
                    WHERE prior.conversation_id = rag_trace.conversation_id
                      AND prior.created_at < rag_trace.created_at
                      AND prior.retrieval_result ->> 'support_version' = '1'
                )), 0)::bigint AS followup_p50_ms,
                COALESCE(percentile_cont(0.95) WITHIN GROUP (
                    ORDER BY (latency ->> 'total_ms')::bigint
                ) FILTER (WHERE EXISTS (
                    SELECT 1 FROM rag_trace prior
                    WHERE prior.conversation_id = rag_trace.conversation_id
                      AND prior.created_at < rag_trace.created_at
                      AND prior.retrieval_result ->> 'support_version' = '1'
                )), 0)::bigint AS followup_p95_ms,
                COALESCE(percentile_cont(0.99) WITHIN GROUP (
                    ORDER BY (latency ->> 'total_ms')::bigint
                ) FILTER (WHERE EXISTS (
                    SELECT 1 FROM rag_trace prior
                    WHERE prior.conversation_id = rag_trace.conversation_id
                      AND prior.created_at < rag_trace.created_at
                      AND prior.retrieval_result ->> 'support_version' = '1'
                )), 0)::bigint AS followup_p99_ms,
                COALESCE(avg((latency ->> 'total_ms')::bigint) FILTER (WHERE EXISTS (
                    SELECT 1 FROM rag_trace prior
                    WHERE prior.conversation_id = rag_trace.conversation_id
                      AND prior.created_at < rag_trace.created_at
                      AND prior.retrieval_result ->> 'support_version' = '1'
                )), 0)::bigint AS followup_avg_ms,
                COALESCE(sum((model_usage ->> 'prompt_tokens')::bigint), 0) AS llm_prompt_tokens,
                COALESCE(sum((model_usage ->> 'completion_tokens')::bigint), 0)
                    AS llm_completion_tokens
            FROM rag_trace
            WHERE {support_where}
        """
        async with self.engine.connect() as connection:
            result = await connection.execute(text(query), parameters)
            row = dict(result.mappings().one())
        support_turns = int(row["support_turns"])
        clarification_required = int(row["clarification_required"])
        return {
            "range": {"from": start.isoformat(), "to": end.isoformat(), "mode": "CHAT"},
            "support_turns": support_turns,
            "first_turns": int(row["first_turns"]),
            "followup_turns": int(row["followup_turns"]),
            "document_delivered": int(row["document_delivered"]),
            "clarification_required": clarification_required,
            "clarification_resolved": int(row["clarification_resolved"]),
            "no_match": int(row["no_match"]),
            "escalation_required": int(row["escalation_required"]),
            "canonical_or_alias_matches": int(row["canonical_or_alias_matches"]),
            "llm_catalog_matches": int(row["llm_catalog_matches"]),
            "general_guidance_turns": int(row["general_guidance_turns"]),
            "evidence_missing": int(row["evidence_missing"]),
            "high_risk_intercepts": int(row["high_risk_intercepts"]),
            "structured_output_retries": int(row["structured_output_retries"]),
            "workflow_conflicts": int(row["workflow_conflicts"]),
            "model_calls": int(row["model_calls"]),
            "document_delivery_rate": _rate(int(row["document_delivered"]), support_turns),
            "clarification_rate": _rate(clarification_required, support_turns),
            "clarification_resolution_rate": _rate(
                int(row["clarification_resolved"]), clarification_required
            ),
            "escalation_rate": _rate(int(row["escalation_required"]), support_turns),
            "general_guidance_rate": _rate(int(row["general_guidance_turns"]), support_turns),
            "workflow_conflict_rate": _rate(int(row["workflow_conflicts"]), support_turns),
            "first_turn_latency": _latency_summary(row, "first", samples=row["first_turns"]),
            "followup_latency": _latency_summary(row, "followup", samples=row["followup_turns"]),
            "tokens": {
                "llm_prompt": int(row["llm_prompt_tokens"]),
                "llm_completion": int(row["llm_completion_tokens"]),
                "llm_total": int(row["llm_prompt_tokens"]) + int(row["llm_completion_tokens"]),
            },
        }

    async def assistant(
        self,
        *,
        days: int = 7,
        retention_days: int = 7,
        knowledge_id: Any = None,
        stale_seconds: int = 600,
    ) -> dict[str, Any]:
        """Operational aggregation for the online Assistant surface.

        Covers the six dimensions the admin dashboard panel needs: call
        source mix, external user activity, turn-level and HTTP-level error
        rates, idempotency protection/replay, conversation retention state,
        and concurrency health (RUNNING backlog plus busy rejections).
        Everything is derived from existing ledgers — no extra write-path
        instrumentation beyond ``replay_count``.
        """
        if not 1 <= days <= _MAX_RANGE_DAYS:
            raise ApiError("VALIDATION_ERROR", "days 必须在 1 到 92 之间", 422)
        if retention_days < 1:
            raise ApiError("VALIDATION_ERROR", "会话留存天数必须为正整数", 422)
        if stale_seconds < 0:
            raise ApiError("VALIDATION_ERROR", "stale_seconds 必须为非负整数", 422)

        end = datetime.now(UTC)
        start = end - timedelta(days=days)
        parameters: dict[str, Any] = {"start": start, "end": end}
        kb_clause = ""
        assistant_request_kb_clause = ""
        conv_kb_clause = ""
        if knowledge_id is not None:
            kb_clause = " AND kb_id = :kb_id"
            assistant_request_kb_clause = " AND knowledge_id = :kb_id"
            conv_kb_clause = " AND c.knowledge_id = :kb_id"
            parameters["kb_id"] = knowledge_id

        async with self.engine.connect() as connection:
            source_rows = (
                (
                    await connection.execute(
                        text(f"""
                            SELECT source,
                                   count(*) AS turns,
                                   count(*) FILTER (WHERE status = 'FAILED') AS failed,
                                   count(DISTINCT conversation_id) AS conversations,
                                   count(DISTINCT user_id) AS users,
                                   {_total_latency_select()}
                            FROM rag_trace
                            WHERE created_at >= :start AND created_at < :end
                              AND source IS NOT NULL{kb_clause}
                            GROUP BY source
                        """),
                        parameters,
                    )
                )
                .mappings()
                .all()
            )
            user_rows = (
                (
                    await connection.execute(
                        text(f"""
                            SELECT user_id,
                                   count(*) AS turns,
                                   count(*) FILTER (WHERE status = 'FAILED') AS failed,
                                   max(created_at) AS last_seen
                            FROM rag_trace
                            WHERE source = 'ASSISTANT_API' AND user_id IS NOT NULL
                              AND created_at >= :start AND created_at < :end{kb_clause}
                            GROUP BY user_id
                            ORDER BY turns DESC, last_seen DESC
                            LIMIT 10
                        """),
                        parameters,
                    )
                )
                .mappings()
                .all()
            )
            turn_error_rows = (
                (
                    await connection.execute(
                        text(f"""
                            SELECT COALESCE(error ->> 'code', 'UNKNOWN') AS code, count(*) AS count
                            FROM rag_trace
                            WHERE error ->> 'code' IS NOT NULL
                              AND source = 'ASSISTANT_API'
                              AND created_at >= :start AND created_at < :end{kb_clause}
                            GROUP BY 1
                            ORDER BY count DESC
                            LIMIT 5
                        """),
                        parameters,
                    )
                )
                .mappings()
                .all()
            )
            http_error_rows = (
                (
                    await connection.execute(
                        text("""
                            SELECT status_code, COALESCE(error_code, 'UNKNOWN') AS error_code,
                                   count(*) AS count
                            FROM request_error_log
                            WHERE created_at >= :start AND created_at < :end
                              AND path LIKE '/api/v1/assistant%'
                            GROUP BY 1, 2
                            ORDER BY count DESC
                            LIMIT 8
                        """),
                        parameters,
                    )
                )
                .mappings()
                .all()
            )
            idempotency_row = (
                (
                    await connection.execute(
                        text(f"""
                            SELECT count(*) AS protected,
                                   COALESCE(sum(replay_count), 0)::bigint AS replays
                            FROM assistant_request
                            WHERE created_at >= :start AND created_at < :end
                                {assistant_request_kb_clause}
                        """),
                        parameters,
                    )
                )
                .mappings()
                .one()
            )
            ingestion_rows = (
                (
                    await connection.execute(
                        text(f"""
                            SELECT operation, count(*) AS count
                            FROM idempotency_record
                            WHERE created_at >= :start AND created_at < :end{kb_clause}
                            GROUP BY operation
                            ORDER BY count DESC
                        """),
                        parameters,
                    )
                )
                .mappings()
                .all()
            )
            retention_row = (
                (
                    await connection.execute(
                        text(f"""
                            SELECT count(*) AS total,
                                   count(*) FILTER (
                                       WHERE last_activity_at
                                           >= now() - make_interval(days => :retention_days)
                                   ) AS active,
                                   count(*) FILTER (
                                       WHERE last_activity_at
                                           < now() - make_interval(days => :retention_days)
                                   ) AS expired
                            FROM conversation c
                            WHERE EXISTS (
                                SELECT 1 FROM rag_trace t
                                WHERE t.conversation_id = c.id AND t.source = 'ASSISTANT_API'
                            ){conv_kb_clause}
                        """),
                        {**parameters, "retention_days": retention_days},
                    )
                )
                .mappings()
                .one()
            )
            # Concurrency health: a RUNNING backlog that never drains means
            # crashed processes are leaking requests; busy rejections show
            # how often clients hit the per-conversation / admission limits.
            running_row = (
                (
                    await connection.execute(
                        text(f"""
                            SELECT count(*) AS count,
                                   COALESCE(
                                       EXTRACT(EPOCH FROM (now() - min(updated_at))), 0
                                   )::bigint AS oldest_age_seconds,
                                   count(*) FILTER (
                                       WHERE updated_at
                                           < now() - make_interval(secs => :stale_seconds)
                                   ) AS stale_count
                            FROM assistant_request
                            WHERE status = 'RUNNING'{assistant_request_kb_clause}
                        """),
                        {**parameters, "stale_seconds": stale_seconds},
                    )
                )
                .mappings()
                .one()
            )
            busy_rows = (
                (
                    await connection.execute(
                        text("""
                            SELECT COALESCE(error_code, 'UNKNOWN') AS error_code,
                                   count(*) AS count
                            FROM request_error_log
                            WHERE created_at >= :start AND created_at < :end
                              AND path LIKE '/api/v1/assistant%'
                              AND error_code IN (
                                  'CONVERSATION_BUSY', 'REQUEST_IN_PROGRESS', 'SERVICE_BUSY'
                              )
                            GROUP BY 1
                            ORDER BY count DESC
                        """),
                        parameters,
                    )
                )
                .mappings()
                .all()
            )

        sources = [
            {
                "source": str(row["source"]),
                "turns": int(row["turns"]),
                "failed": int(row["failed"]),
                "error_rate": _rate(int(row["failed"]), int(row["turns"])),
                "conversations": int(row["conversations"]),
                "users": int(row["users"]),
                "latency": _latency_summary(dict(row), samples=row["latency_samples"]),
            }
            for row in source_rows
        ]
        return {
            "days": days,
            "sources": sources,
            "top_users": [
                {
                    "user_id": str(row["user_id"]),
                    "turns": int(row["turns"]),
                    "failed": int(row["failed"]),
                    "last_seen": row["last_seen"].isoformat(),
                }
                for row in user_rows
            ],
            "turn_error_codes": [
                {"code": str(row["code"]), "count": int(row["count"])} for row in turn_error_rows
            ],
            "http_errors": [
                {
                    "status_code": int(row["status_code"]),
                    "error_code": str(row["error_code"]),
                    "count": int(row["count"]),
                }
                for row in http_error_rows
            ],
            "idempotency": {
                "assistant_protected": int(idempotency_row["protected"]),
                "assistant_replays": int(idempotency_row["replays"]),
                "ingestion_operations": {
                    str(row["operation"]): int(row["count"]) for row in ingestion_rows
                },
            },
            "retention": {
                "retention_days": retention_days,
                "total": int(retention_row["total"]),
                "active": int(retention_row["active"]),
                "expired": int(retention_row["expired"]),
            },
            "concurrency": {
                "running_requests": int(running_row["count"]),
                "oldest_running_age_seconds": int(running_row["oldest_age_seconds"]),
                "stale_running_requests": int(running_row["stale_count"]),
                "busy_rejections": {str(row["error_code"]): int(row["count"]) for row in busy_rows},
            },
        }


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.astimezone()


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _latency_summary(row: Mapping[str, Any], prefix: str = "", *, samples: Any) -> dict[str, int]:
    """Latency block plus how many turns it was actually computed from."""

    key = f"{prefix}_" if prefix else ""
    return {
        "p50_ms": int(row[f"{key}p50_ms"]),
        "p95_ms": int(row[f"{key}p95_ms"]),
        "p99_ms": int(row[f"{key}p99_ms"]),
        "avg_ms": int(row[f"{key}avg_ms"]),
        "samples": int(samples),
    }
