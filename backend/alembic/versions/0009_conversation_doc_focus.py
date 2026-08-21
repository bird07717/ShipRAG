"""Add conversation doc-focus state for chat document routing.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-20
"""

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    statements = [
        """
        ALTER TABLE conversation
        ADD COLUMN focus_document_id uuid
            REFERENCES document_source(id) ON DELETE SET NULL,
        ADD COLUMN chat_context jsonb NOT NULL DEFAULT '{}'::jsonb
        """,
        """
        CREATE INDEX ix_conversation_focus_document
        ON conversation (focus_document_id)
        WHERE focus_document_id IS NOT NULL
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    statements = [
        "DROP INDEX IF EXISTS ix_conversation_focus_document",
        "ALTER TABLE conversation DROP COLUMN IF EXISTS chat_context",
        "ALTER TABLE conversation DROP COLUMN IF EXISTS focus_document_id",
    ]
    for statement in statements:
        op.execute(statement)
