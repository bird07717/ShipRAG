"""Latency aggregation口径 against a real PostgreSQL instance.

The shortcut filter lives entirely in SQL (``FILTER (WHERE ...)`` over an
ordered-set aggregate), so a fake connection cannot prove it: only a real
server decides which rows reach ``percentile_cont``.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.rag.metrics import MetricsRepository

pytestmark = pytest.mark.asyncio


async def _insert_trace(
    connection,
    *,
    kb_id,
    conversation_id,
    total_ms,
    shortcut,
    response_type,
    response_path=None,
) -> None:
    citation: dict[str, str] = {"status": "OK", "pipeline": "V2"}
    if shortcut:
        citation = {"status": "NOT_REQUIRED", "pipeline": "V2", "shortcut": shortcut}
    if response_path:
        citation["response_path"] = response_path
    await connection.execute(
        text(
            """
            INSERT INTO rag_trace (
                id, trace_id, request_id, kb_id, conversation_id, question,
                mode, status, engine, stage, source, response_type,
                citation_result, latency
            ) VALUES (
                :id, :trace_id, 'metrics-latency', :kb_id, :conversation_id, '问题',
                'CHAT', 'COMPLETED', 'RAG', 'LLM', 'ASSISTANT_API', :response_type,
                CAST(:citation AS jsonb), CAST(:latency AS jsonb)
            )
            """
        ),
        {
            "id": uuid4(),
            "trace_id": uuid4(),
            "kb_id": kb_id,
            "conversation_id": conversation_id,
            "response_type": response_type,
            "citation": json.dumps(citation),
            "latency": json.dumps({"total_ms": total_ms, "llm_ms": total_ms}),
        },
    )


async def test_latency_excludes_every_input_guard_shortcut() -> None:
    url = os.getenv("SUPPORT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SUPPORT_TEST_DATABASE_URL is not configured")

    engine = create_async_engine(url, pool_pre_ping=True)
    kb_id, conversation_id = uuid4(), uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO knowledge_base (id, name) VALUES (:id, :name)"),
                {"id": kb_id, "name": f"metrics-latency-{kb_id}"},
            )
            await connection.execute(
                text("INSERT INTO conversation (id, knowledge_id) VALUES (:id, :kb_id)"),
                {"id": conversation_id, "kb_id": kb_id},
            )
            for total_ms, shortcut, response_type, response_path in (
                (12_000, None, "ANSWERED", "KNOWLEDGE"),
                (12_000, None, "ANSWERED", "KNOWLEDGE"),
                (5, "GREETING", "ANSWERED", None),
                (5, "META_ACK", "ANSWERED", None),
                (5, "EMPTY_QUESTION", "UNCONFIRMED", None),
                (900, None, "ANSWERED", "CONVERSATION"),
            ):
                await _insert_trace(
                    connection,
                    kb_id=kb_id,
                    conversation_id=conversation_id,
                    total_ms=total_ms,
                    shortcut=shortcut,
                    response_type=response_type,
                    response_path=response_path,
                )

        repository = MetricsRepository(engine)
        window = {
            "created_from": datetime.now(UTC) - timedelta(hours=1),
            "created_to": datetime.now(UTC) + timedelta(hours=1),
        }
        overview = await repository.overview(knowledge_id=kb_id, **window)
        timeseries = await repository.timeseries(knowledge_id=kb_id, **window)
        latency = await repository.latency(knowledge_id=kb_id, **window)
        assistant = await repository.assistant(knowledge_id=kb_id, days=1)

        # Operational totals keep every real request. Greetings and
        # conversation-control acknowledgements stay out of technical quality,
        # while low-information clarification remains a technical signal.
        assert overview["total_traces"] == 6
        assert overview["completed"] == 6
        assert overview["response_type_counts"] == {
            "ANSWERED": 2,
            "UNCONFIRMED": 1,
            "OUT_OF_SCOPE": 0,
            "DOC_DELIVERED": 0,
        }
        assert overview["classified_traces"] == 3
        assert timeseries[0]["queries"] == 6
        assert timeseries[0]["answered"] == 2
        assert timeseries[0]["unconfirmed"] == 1
        # Technical latency is computed from the two knowledge turns only.
        assert overview["latency"] == {
            "p50_ms": 12_000,
            "p95_ms": 12_000,
            "p99_ms": 12_000,
            "avg_ms": 12_000,
            "samples": 2,
        }
        assert latency["total"]["samples"] == 2
        assert latency["stages"]["llm_ms"] == 12_000
        source = next(row for row in assistant["sources"] if row["source"] == "ASSISTANT_API")
        # The operational request count still sees all six turns.
        assert source["turns"] == 6
        assert source["latency"] == {
            "p50_ms": 12_000,
            "p95_ms": 12_000,
            "p99_ms": 12_000,
            "avg_ms": 12_000,
            "samples": 2,
        }
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM knowledge_base WHERE id = :id"), {"id": kb_id}
            )
        await engine.dispose()


async def test_v2_quality_reads_stage_outcomes_from_runtime_snapshot() -> None:
    """V2 never writes rerank_result/citation_result; its degradations live in
    runtime_snapshot.generation, and only a real server evaluates the paths."""
    url = os.getenv("SUPPORT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SUPPORT_TEST_DATABASE_URL is not configured")

    def snapshot(query: str | None, rerank: dict[str, str] | None) -> dict[str, object]:
        generation: dict[str, object] = {"pipeline": "V2"}
        if query is not None:
            generation["query_interpreter"] = {"status": query, "reason": None}
        if rerank is not None:
            generation["retrieval"] = {"document_rerank": rerank, "degradations": []}
        return {"generation": generation}

    engine = create_async_engine(url, pool_pre_ping=True)
    kb_id, conversation_id = uuid4(), uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO knowledge_base (id, name) VALUES (:id, :name)"),
                {"id": kb_id, "name": f"metrics-v2-quality-{kb_id}"},
            )
            await connection.execute(
                text("INSERT INTO conversation (id, knowledge_id) VALUES (:id, :kb_id)"),
                {"id": conversation_id, "kb_id": kb_id},
            )
            rows = [
                snapshot("PASSED", {"status": "PASSED", "provider": "dashscope"}),
                snapshot("PASSED", {"status": "DEGRADED", "reason": "UPSTREAM_TIMEOUT"}),
                snapshot("DEGRADED", {"status": "PASSED"}),
                snapshot("PASSED", {"status": "SKIPPED", "reason": "MODEL_NOT_CONFIGURED"}),
                # What production actually records for a Rerank row without a
                # key: the factory raises inside the try-block → DEGRADED.
                snapshot("PASSED", {"status": "DEGRADED", "reason": "MODEL_NOT_CONFIGURED"}),
                snapshot("PASSED", {"status": "SKIPPED", "reason": "NOT_ENOUGH_CANDIDATES"}),
                snapshot("PASSED", None),  # conversation path: no retrieval
                {},  # legacy trace without a V2 snapshot
            ]
            for runtime_snapshot in rows:
                await connection.execute(
                    text(
                        """
                        INSERT INTO rag_trace (
                            id, trace_id, request_id, kb_id, conversation_id, question,
                            mode, status, engine, stage, source, response_type,
                            citation_result, latency, runtime_snapshot
                        ) VALUES (
                            :id, :trace_id, 'metrics-v2', :kb_id, :conversation_id, '问题',
                            'CHAT', 'COMPLETED', 'RAG', 'LLM', 'ASSISTANT_API', 'ANSWERED',
                            '{}'::jsonb, '{"total_ms": 100}'::jsonb, CAST(:snapshot AS jsonb)
                        )
                        """
                    ),
                    {
                        "id": uuid4(),
                        "trace_id": uuid4(),
                        "kb_id": kb_id,
                        "conversation_id": conversation_id,
                        "snapshot": json.dumps(runtime_snapshot),
                    },
                )

        overview = await MetricsRepository(engine).overview(
            knowledge_id=kb_id,
            created_from=datetime.now(UTC) - timedelta(hours=1),
            created_to=datetime.now(UTC) + timedelta(hours=1),
        )
        assert overview["v2_quality"] == {
            "query_interpreter_total": 7,
            "query_interpreter_degraded": 1,
            "query_interpreter_degraded_rate": round(1 / 7, 4),
            # Configuration failures are neither call attempts nor call failures.
            "document_rerank_attempted": 3,
            "document_rerank_degraded": 1,
            "document_rerank_degraded_rate": round(1 / 3, 4),
            "document_rerank_unconfigured": 2,
        }
        # The legacy fields stay at zero for V2 traffic; the console must not show them.
        assert overview["rerank_degraded_rate"] == 0.0
        assert overview["citation_missing_rate"] == 0.0
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM rag_trace WHERE kb_id = :kb_id"), {"kb_id": kb_id}
            )
            await connection.execute(
                text("DELETE FROM knowledge_base WHERE id = :id"), {"id": kb_id}
            )
        await engine.dispose()
