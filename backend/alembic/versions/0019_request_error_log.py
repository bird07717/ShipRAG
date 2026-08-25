"""Add the request_error_log table for post-launch failure review.

Failed API requests (4xx/5xx, validation errors, unhandled exceptions) are
persisted so the integration side can be debugged by request_id after launch.
Also adds the (status, created_at) index on task_record that the global task
list endpoint needs.

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-24
"""

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE request_error_log (
            id uuid PRIMARY KEY,
            request_id varchar(128) NOT NULL,
            method varchar(10) NOT NULL,
            path text NOT NULL,
            status_code int NOT NULL,
            error_code varchar(100) NOT NULL,
            message text NOT NULL,
            detail jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CHECK (status_code >= 400),
            CHECK (jsonb_typeof(detail) = 'object')
        )
        """
    )
    op.execute("CREATE INDEX ix_request_error_log_created ON request_error_log (created_at DESC)")
    op.execute("CREATE INDEX ix_request_error_log_request_id ON request_error_log (request_id)")
    op.execute(
        "CREATE INDEX ix_request_error_log_status ON request_error_log (status_code, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_task_record_status_created ON task_record (status, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_task_record_status_created")
    op.execute("DROP TABLE IF EXISTS request_error_log")
