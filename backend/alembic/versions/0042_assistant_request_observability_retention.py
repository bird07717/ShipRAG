"""Add Assistant replay observability and orphan-retention support.

Revision ID: 0042
Revises: 0041
"""

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE assistant_request ADD COLUMN replay_count bigint NOT NULL DEFAULT 0, "
        "ADD COLUMN last_replayed_at timestamptz"
    )
    op.execute(
        "ALTER TABLE assistant_request ADD CONSTRAINT "
        "ck_assistant_request_replay_count_nonnegative CHECK (replay_count >= 0)"
    )
    op.execute(
        "CREATE INDEX ix_assistant_request_orphan_retention "
        "ON assistant_request (updated_at) "
        "WHERE conversation_id IS NULL "
        "AND status IN ('COMPLETED', 'FAILED', 'CANCELLED')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_assistant_request_orphan_retention")
    op.execute(
        "ALTER TABLE assistant_request DROP CONSTRAINT IF EXISTS "
        "ck_assistant_request_replay_count_nonnegative"
    )
    op.execute(
        "ALTER TABLE assistant_request DROP COLUMN IF EXISTS last_replayed_at, "
        "DROP COLUMN IF EXISTS replay_count"
    )
