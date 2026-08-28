"""Add user_id and title to conversation for platform-side history.

Platform integrations need per-user conversation isolation and a display
title.  ``user_id`` is the caller-side attribution key (same value the
gateway already records on rag_trace); ``title`` is derived from the first
user message so no extra LLM call is required.  Both columns are nullable:
legacy rows and anonymous DemoChat conversations keep working unchanged.

Revision ID: 0036
Revises: 0035
Create Date: 2026-08-28
"""

from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE conversation ADD COLUMN user_id varchar(100), ADD COLUMN title varchar(200)"
    )
    op.execute(
        "CREATE INDEX ix_conversation_kb_user_updated "
        "ON conversation (knowledge_id, user_id, updated_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_conversation_kb_user_updated")
    op.execute(
        "ALTER TABLE conversation DROP COLUMN IF EXISTS user_id, DROP COLUMN IF EXISTS title"
    )
