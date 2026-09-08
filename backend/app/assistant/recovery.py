from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings, get_settings
from app.core.resources import AppResources

DEFAULT_STALE_HOURS = 24.0
RECOVERY_CODE = "ABANDONED_ASSISTANT_REQUEST"
RECOVERY_MESSAGE = "请求长时间未完成，已由恢复任务标记为失败"


class AssistantRequestRecoveryRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def preview(self, *, stale_before: datetime) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT count(*) AS request_count,
                                   count(*) FILTER (
                                       WHERE conversation_id IS NULL
                                   ) AS without_conversation_count,
                                   count(*) FILTER (
                                       WHERE assistant_message_id IS NOT NULL
                                   ) AS linked_message_count,
                                   count(*) FILTER (
                                       WHERE trace_id IS NOT NULL
                                   ) AS linked_trace_count,
                                   (
                                       SELECT count(*)
                                       FROM rag_trace AS t
                                       WHERE t.source = 'ASSISTANT_API'
                                         AND t.status = 'RUNNING'
                                         AND t.created_at < :stale_before
                                         AND NOT EXISTS (
                                             SELECT 1
                                             FROM assistant_request AS ar
                                             WHERE ar.trace_id = t.trace_id
                                               AND ar.status = 'RUNNING'
                                         )
                                   ) AS orphan_trace_count,
                                   min(updated_at) AS oldest_updated_at,
                                   max(updated_at) AS newest_updated_at,
                                   (
                                       SELECT count(*) FROM task_record
                                       WHERE status IN ('QUEUED', 'RUNNING')
                                   ) AS active_task_count
                            FROM assistant_request
                            WHERE status = 'RUNNING' AND updated_at < :stale_before
                            """
                        ),
                        {"stale_before": stale_before},
                    )
                )
                .mappings()
                .one()
            )
        return dict(row)

    async def reconcile(self, *, stale_before: datetime) -> dict[str, int]:
        recovered_at = datetime.now(UTC)
        error = json.dumps(
            {
                "code": RECOVERY_CODE,
                "message": RECOVERY_MESSAGE,
                "retryable": True,
                "recovered_at": recovered_at.isoformat(),
            },
            ensure_ascii=False,
        )
        async with self.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                            WITH stale AS MATERIALIZED (
                                SELECT id, assistant_message_id, trace_id
                                FROM assistant_request
                                WHERE status = 'RUNNING' AND updated_at < :stale_before
                                FOR UPDATE SKIP LOCKED
                            ),
                            stale_assistant_traces AS MATERIALIZED (
                                SELECT trace_id, message_id
                                FROM rag_trace
                                WHERE source = 'ASSISTANT_API'
                                  AND status = 'RUNNING'
                                  AND created_at < :stale_before
                                FOR UPDATE SKIP LOCKED
                            ),
                            stale_trace_ids AS MATERIALIZED (
                                SELECT trace_id
                                FROM stale
                                WHERE trace_id IS NOT NULL
                                UNION
                                SELECT trace_id
                                FROM stale_assistant_traces
                            ),
                            stale_message_ids AS MATERIALIZED (
                                SELECT assistant_message_id AS message_id
                                FROM stale
                                WHERE assistant_message_id IS NOT NULL
                                UNION
                                SELECT message_id
                                FROM stale_assistant_traces
                                WHERE message_id IS NOT NULL
                            ),
                            failed_messages AS (
                                UPDATE message AS m
                                SET status = 'FAILED', updated_at = :recovered_at
                                FROM stale_message_ids AS s
                                WHERE m.id = s.message_id
                                  AND m.status = 'STREAMING'
                                RETURNING m.id
                            ),
                            failed_traces AS (
                                UPDATE rag_trace AS t
                                SET status = 'FAILED',
                                    error = COALESCE(t.error, '{}'::jsonb)
                                        || CAST(:error AS jsonb),
                                    stage = COALESCE(t.stage, 'assistant_request_recovery'),
                                    finished_at = COALESCE(t.finished_at, :recovered_at)
                                FROM stale_trace_ids AS s
                                WHERE t.trace_id = s.trace_id
                                  AND t.status = 'RUNNING'
                                RETURNING t.trace_id
                            ),
                            failed_requests AS (
                                UPDATE assistant_request AS ar
                                SET status = 'FAILED',
                                    terminal_error = CAST(:error AS jsonb),
                                    updated_at = :recovered_at
                                FROM stale AS s
                                WHERE ar.id = s.id AND ar.status = 'RUNNING'
                                RETURNING ar.id
                            )
                            SELECT
                                (SELECT count(*) FROM failed_requests) AS request_count,
                                (SELECT count(*) FROM failed_messages) AS message_count,
                                (SELECT count(*) FROM failed_traces) AS trace_count
                            """
                        ),
                        {
                            "stale_before": stale_before,
                            "recovered_at": recovered_at,
                            "error": error,
                        },
                    )
                )
                .mappings()
                .one()
            )
        return {key: int(value) for key, value in row.items()}


async def run(
    settings: Settings,
    *,
    stale_hours: float,
    apply: bool,
) -> dict[str, Any]:
    if stale_hours <= 0:
        raise ValueError("stale_hours must be greater than zero")
    stale_before = datetime.now(UTC) - timedelta(hours=stale_hours)
    resources = AppResources.create(settings)
    repository = AssistantRequestRecoveryRepository(resources.database)
    try:
        before = await repository.preview(stale_before=stale_before)
        matched_count = int(before["request_count"]) + int(before["orphan_trace_count"])
        recovered = (
            await repository.reconcile(stale_before=stale_before)
            if apply and matched_count > 0
            else {"request_count": 0, "message_count": 0, "trace_count": 0}
        )
        after = await repository.preview(stale_before=stale_before) if apply else before
        return {
            "mode": "apply" if apply else "preview",
            "stale_before": stale_before,
            "before": before,
            "recovered": recovered,
            "after": after,
        }
    finally:
        await resources.close()


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview or fail stale RUNNING Assistant requests and their linked rows."
    )
    parser.add_argument("--stale-hours", type=float, default=DEFAULT_STALE_HOURS)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the recovery. Without this flag the command is read-only.",
    )
    return parser.parse_args()


def main() -> None:
    arguments = _arguments()
    result = asyncio.run(
        run(
            get_settings(),
            stale_hours=arguments.stale_hours,
            apply=arguments.apply,
        )
    )
    print(json.dumps(result, ensure_ascii=False, default=str, sort_keys=True))


if __name__ == "__main__":
    main()
