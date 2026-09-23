from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.assistant import recovery
from app.assistant.recovery import (
    INTERNAL_RECOVERY_CODE,
    RECOVERY_CODE,
    AssistantRequestRecoveryRepository,
)
from app.core.config import Settings


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
        [
            {
                "request_count": 25,
                "message_count": 25,
                "trace_count": 25,
                "internal_trace_count": 3,
            }
        ]
    )
    cutoff = datetime(2026, 9, 6, tzinfo=UTC)

    assert await repository.reconcile(stale_before=cutoff) == {
        "request_count": 25,
        "message_count": 25,
        "trace_count": 25,
        "internal_trace_count": 3,
    }
    sql, params = engine.connection.executed[0]
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "source = 'ASSISTANT_API'" in sql
    assert "stale_assistant_traces" in sql
    assert "stale_internal_traces" in sql
    assert "source IN ('INTERNAL_CHAT', 'PLAYGROUND')" in sql
    assert "UPDATE message AS m" in sql
    assert "UPDATE rag_trace AS t" in sql
    assert "UPDATE assistant_request AS ar" in sql
    assert "terminal_error = CAST(:error AS jsonb)" in sql
    assert RECOVERY_CODE in params["error"]
    assert INTERNAL_RECOVERY_CODE in params["internal_error"]
    assert params["stale_before"] == cutoff


class _RunRepository:
    def __init__(
        self, request_count: int, orphan_trace_count: int, internal_trace_count: int = 0
    ) -> None:
        self.before = {
            "request_count": request_count,
            "orphan_trace_count": orphan_trace_count,
            "internal_trace_count": internal_trace_count,
        }
        self.preview_calls = 0
        self.reconcile_calls = 0

    async def preview(self, *, stale_before: datetime) -> dict[str, Any]:
        assert stale_before.tzinfo is not None
        self.preview_calls += 1
        return self.before if self.preview_calls == 1 else {**self.before, "request_count": 0}

    async def reconcile(self, *, stale_before: datetime) -> dict[str, int]:
        assert stale_before.tzinfo is not None
        self.reconcile_calls += 1
        return {
            "request_count": 2,
            "message_count": 2,
            "trace_count": 2,
            "internal_trace_count": 2,
        }


class _RunResources:
    def __init__(self) -> None:
        self.database = object()
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "apply",
        "request_count",
        "orphan_count",
        "internal_count",
        "expected_mode",
        "expected_reconcile",
    ),
    [
        (False, 2, 1, 0, "preview", 0),
        (True, 0, 0, 0, "apply", 0),
        (True, 2, 0, 0, "apply", 1),
        # Internal chat residue alone must still trigger the apply pass.
        (True, 0, 0, 2, "apply", 1),
    ],
)
async def test_run_previews_and_conditionally_applies_recovery(
    monkeypatch: pytest.MonkeyPatch,
    apply: bool,
    request_count: int,
    orphan_count: int,
    internal_count: int,
    expected_mode: str,
    expected_reconcile: int,
) -> None:
    repository = _RunRepository(request_count, orphan_count, internal_count)
    resources = _RunResources()
    monkeypatch.setattr(recovery.AppResources, "create", lambda _: resources)
    monkeypatch.setattr(recovery, "AssistantRequestRecoveryRepository", lambda _: repository)

    result = await recovery.run(
        Settings(_env_file=None, app_env="test"), stale_hours=12, apply=apply
    )

    assert result["mode"] == expected_mode
    assert repository.reconcile_calls == expected_reconcile
    assert repository.preview_calls == (2 if apply else 1)
    assert resources.closed is True


@pytest.mark.asyncio
async def test_run_rejects_non_positive_stale_window() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        await recovery.run(Settings(_env_file=None, app_env="test"), stale_hours=0, apply=False)


def test_recovery_arguments_and_main(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["recovery", "--stale-hours", "6", "--apply"])
    arguments = recovery._arguments()
    assert arguments.stale_hours == 6
    assert arguments.apply is True

    settings = Settings(_env_file=None, app_env="test")

    async def fake_run(received: Settings, *, stale_hours: float, apply: bool) -> dict[str, Any]:
        assert received is settings
        assert stale_hours == recovery.DEFAULT_STALE_HOURS
        assert apply is False
        return {"mode": "preview"}

    monkeypatch.setattr(
        recovery,
        "_arguments",
        lambda: recovery.argparse.Namespace(stale_hours=recovery.DEFAULT_STALE_HOURS, apply=False),
    )
    monkeypatch.setattr(recovery, "get_settings", lambda: settings)
    monkeypatch.setattr(recovery, "run", fake_run)

    recovery.main()

    assert '"mode": "preview"' in capsys.readouterr().out
