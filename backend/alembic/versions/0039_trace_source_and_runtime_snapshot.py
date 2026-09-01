"""Make online Assistant traces distinguishable and reproducible.

Revision ID: 0039
Revises: 0038
"""

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE rag_trace ADD COLUMN IF NOT EXISTS source varchar(30) "
        "NOT NULL DEFAULT 'INTERNAL_CHAT'"
    )
    op.execute("UPDATE rag_trace SET source = 'PLAYGROUND' WHERE mode = 'PLAYGROUND'")
    op.execute(
        "ALTER TABLE rag_trace ADD CONSTRAINT ck_rag_trace_source "
        "CHECK (source IN ('ASSISTANT_API', 'INTERNAL_CHAT', 'PLAYGROUND'))"
    )
    op.execute("CREATE INDEX ix_rag_trace_source_created ON rag_trace (source, created_at DESC)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_rag_trace_source_created")
    op.execute("ALTER TABLE rag_trace DROP CONSTRAINT IF EXISTS ck_rag_trace_source")
    op.execute("ALTER TABLE rag_trace DROP COLUMN IF EXISTS source")
