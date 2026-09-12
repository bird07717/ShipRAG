"""Persist RAG turn pairing independently of public snapshots and Trace GC."""

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE message
        ADD COLUMN rag_user_message_id uuid REFERENCES message(id) ON DELETE SET NULL,
        ADD COLUMN rag_history_context jsonb NOT NULL DEFAULT '{}'::jsonb
    """)
    op.execute("CREATE INDEX ix_message_rag_user ON message(rag_user_message_id)")
    op.execute(
        "CREATE INDEX ix_assistant_request_message ON assistant_request(assistant_message_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_assistant_request_message")
    op.execute("ALTER TABLE message DROP COLUMN rag_history_context")
    op.execute("ALTER TABLE message DROP COLUMN rag_user_message_id")
