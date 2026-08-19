"""Add M6 administration query indexes.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-17
"""

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_knowledge_index_kb_created ON knowledge_index (kb_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_rag_trace_mode_status_created ON rag_trace (mode, status, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_rag_trace_mode_status_created")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_index_kb_created")
