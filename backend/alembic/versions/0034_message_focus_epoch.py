"""Track the focus epoch of new conversation messages.

Revision ID: 0034
Revises: 0033
Create Date: 2026-08-27
"""

from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Existing messages predate focus epochs. Leave them NULL instead of
    # inventing a document boundary that cannot be recovered reliably.
    op.execute("ALTER TABLE message ADD COLUMN focus_epoch integer")
    op.execute("ALTER TABLE message ADD CONSTRAINT ck_message_focus_epoch CHECK (focus_epoch >= 0)")
    op.execute(
        "CREATE INDEX ix_message_conversation_focus_epoch_sequence "
        "ON message (conversation_id, focus_epoch, sequence_no)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_message_conversation_focus_epoch_sequence")
    op.execute("ALTER TABLE message DROP CONSTRAINT IF EXISTS ck_message_focus_epoch")
    op.execute("ALTER TABLE message DROP COLUMN IF EXISTS focus_epoch")
