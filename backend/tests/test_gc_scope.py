"""Per-knowledge GC must only touch that knowledge base's retention data.

The admin "回收旧索引" button calls ``collect_garbage(kb_id=...)``; trace
retention and terminal-task pruning follow the same scope. The periodic GC
(``kb_id=None``) keeps processing the whole fleet.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from minio import Minio
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.ingestion.repository import IngestionRepository
from app.services import ingestion as ingestion_service
from app.services.ingestion import IngestionService
from app.tasks.repository import TaskRecordRepository


class _Result:
    rowcount = 3


class _Connection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> _Connection:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def execute(self, statement: Any, parameters: dict[str, Any]) -> _Result:
        self.calls.append((str(statement), dict(parameters)))
        return _Result()


class _Engine:
    def __init__(self) -> None:
        self.connection = _Connection()

    def begin(self) -> _Connection:
        return self.connection


def _repository(engine: _Engine) -> IngestionRepository:
    repository = IngestionRepository.__new__(IngestionRepository)
    repository.engine = cast(AsyncEngine, engine)
    return repository


@pytest.mark.parametrize("kb_id", [uuid4(), None])
async def test_retain_traces_scopes_both_steps_to_the_knowledge_base(kb_id: UUID | None) -> None:
    engine = _Engine()

    result = await _repository(engine).retain_traces(7, 30, kb_id=kb_id)

    assert result == {"redacted": 3, "deleted": 3}
    (redact_sql, redact_params), (delete_sql, delete_params) = engine.connection.calls
    assert "UPDATE rag_trace" in redact_sql
    assert "DELETE FROM rag_trace" in delete_sql
    for sql, params in ((redact_sql, redact_params), (delete_sql, delete_params)):
        if kb_id is None:
            assert "kb_id" not in sql
            assert "kb_id" not in params
        else:
            assert sql.rstrip().endswith("AND kb_id = :kb_id")
            assert params["kb_id"] == kb_id


@pytest.mark.parametrize("kb_id", [uuid4(), None])
async def test_prune_terminal_tasks_scopes_deletion_to_the_knowledge_base(
    kb_id: UUID | None,
) -> None:
    engine = _Engine()

    deleted = await TaskRecordRepository(cast(AsyncEngine, engine)).prune_terminal_tasks(
        90, kb_id=kb_id
    )

    assert deleted == 3
    ((sql, params),) = engine.connection.calls
    assert "DELETE FROM task_record" in sql
    assert "PARTITION BY t.kb_id" in sql  # per-knowledge floor is unchanged
    if kb_id is None:
        assert ":kb_id" not in sql
        assert "kb_id" not in params
    else:
        assert sql.rstrip().endswith("AND kb_id = :kb_id")
        assert params["kb_id"] == kb_id


class _GcRepository:
    def __init__(self) -> None:
        self.engine = object()
        self.calls: dict[str, Any] = {}

    async def purge_expired_conversations(self, days: int, kb_id: UUID | None = None) -> list[UUID]:
        self.calls["purge"] = kb_id
        return []

    async def retain_traces(
        self, full_days: int, summary_days: int, kb_id: UUID | None = None
    ) -> dict[str, int]:
        self.calls["traces"] = kb_id
        return {"redacted": 0, "deleted": 0}

    async def find_deleting_indexes(self) -> list[Any]:
        return []

    async def find_deletable_indexes(self, count: int, days: int) -> list[Any]:
        return []


@pytest.mark.parametrize("kb_id", [uuid4(), None])
async def test_collect_garbage_applies_the_call_scope_to_retention(
    monkeypatch: pytest.MonkeyPatch, kb_id: UUID | None
) -> None:
    pruned: dict[str, Any] = {}

    class _Tasks:
        def __init__(self, _engine: Any) -> None:
            pass

        async def prune_terminal_tasks(self, days: int, *, kb_id: UUID | None = None) -> int:
            pruned["kb_id"] = kb_id
            return 0

    monkeypatch.setattr(ingestion_service, "TaskRecordRepository", _Tasks)
    repository = _GcRepository()
    service = IngestionService(
        cast(IngestionRepository, repository),
        cast(Minio, object()),
        Settings(_env_file=None, app_env="test", task_record_retention_days=90),
    )

    result = await service.collect_garbage(kb_id=kb_id)

    assert result["deleted_count"] == 0
    assert repository.calls == {"purge": kb_id, "traces": kb_id}
    assert pruned == {"kb_id": kb_id}
