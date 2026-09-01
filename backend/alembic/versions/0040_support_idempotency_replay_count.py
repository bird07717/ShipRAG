"""Count Assistant idempotent replay hits for operations visibility.

Revision ID: 0040
Revises: 0039
"""

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE support_idempotency ADD COLUMN IF NOT EXISTS replay_count "
        "integer NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE support_idempotency DROP COLUMN IF EXISTS replay_count")
