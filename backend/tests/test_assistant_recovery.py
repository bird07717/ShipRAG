from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.assistant.recovery import (
    RECOVERY_CODE,
    AssistantRequestRecoveryRepository,
)


class _Result:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row

    def mappings(self) -> _Result:
        return self

    def one(self) -> dict[str, Any]:
        return self.row


class _Connection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.executed: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> _Connection:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
        self.executed.append((str(statement), params))
        return _Result(self.rows.pop(0))


class _Engine:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.connection = _Connection(rows)

    def connect(self) -> AbstractAsyncContextManager[_Connection]:
        return self.connection

    def begin(self) -> AbstractAsyncContextManager[_Connection]:
        return self.connection


def _repository(rows: list[dict[str, Any]]) -> tuple[AssistantRequestRecoveryRepository, _Engine]:
    engine = _Engine(rows)
    return AssistantRequestRecoveryRepository(cast(AsyncEngine, engine)), engine


@pytest.mark.asyncio
async def test_preview_reports_stale_request_and_active_task_counts() -> None:
    expected = {
        "request_count": 25,
        "without_conversation_count": 1,
        "linked_message_count": 24,
        "linked_trace_count": 24,
        "orphan_trace_count": 1,
        "oldest_updated_at": datetime(2026, 9, 1, tzinfo=UTC),
        "newest_updated_at": datetime(2026, 9, 1, tzinfo=UTC),
        "active_task_count": 0,
    }
    repository, engine = _repository([expected])
    cutoff = datetime(2026, 9, 6, tzinfo=UTC)

    assert await repository.preview(stale_before=cutoff) == expected
    sql, params = engine.connection.executed[0]
    assert "status = 'RUNNING' AND updated_at < :stale_before" in sql
    assert "status IN ('QUEUED', 'RUNNING')" in sql
    assert params == {"stale_before": cutoff}


@pytest.mark.asyncio
async def test_reconcile_fails_request_message_and_trace_in_one_statement() -> None:
    repository, engine = _repository(
        [{"request_count": 25, "message_count": 25, "trace_count": 25}]
    )
    cutoff = datetime(2026, 9, 6, tzinfo=UTC)

    assert await repository.reconcile(stale_before=cutoff) == {
        "request_count": 25,
        "message_count": 25,
        "trace_count": 25,
    }
    sql, params = engine.connection.executed[0]
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "source = 'ASSISTANT_API'" in sql
    assert "stale_assistant_traces" in sql
    assert "UPDATE message AS m" in sql
    assert "UPDATE rag_trace AS t" in sql
    assert "UPDATE assistant_request AS ar" in sql
    assert "terminal_error = CAST(:error AS jsonb)" in sql
    assert RECOVERY_CODE in params["error"]
    assert params["stale_before"] == cutoff
