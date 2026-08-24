"""Quality metrics columns on rag_trace for server-side usage analytics.

Adds user_id (caller-side user attribution), response_type and answer_mode
(persisted turn outcomes previously only present in the SSE done payload),
plus backfill of DOC_DELIVERED from historical doc_routing decisions.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-21
"""

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE rag_trace
            ADD COLUMN user_id varchar(100),
            ADD COLUMN response_type varchar(20),
            ADD COLUMN answer_mode varchar(30)
        """
    )
    op.execute(
        "ALTER TABLE rag_trace ADD CONSTRAINT ck_rag_trace_response_type"
        " CHECK (response_type IS NULL OR response_type IN"
        " ('ANSWERED', 'UNCONFIRMED', 'OUT_OF_SCOPE', 'DOC_DELIVERED'))"
    )
    op.execute(
        "ALTER TABLE rag_trace ADD CONSTRAINT ck_rag_trace_answer_mode"
        " CHECK (answer_mode IS NULL OR answer_mode IN"
        " ('PRODUCT_KNOWLEDGE', 'PRODUCT_EXPLAINED', 'PRODUCT_GENERAL',"
        " 'UNCONFIRMED', 'OUT_OF_SCOPE'))"
    )
    op.execute("CREATE INDEX ix_rag_trace_user_created ON rag_trace (user_id, created_at DESC)")
    op.execute(
        "CREATE INDEX ix_rag_trace_response_created ON rag_trace (response_type, created_at DESC)"
    )
    # Backfill: historical DOC_DELIVERED turns are unambiguously recoverable
    # from the persisted doc routing decision. ANSWERED / UNCONFIRMED /
    # OUT_OF_SCOPE cannot be distinguished after the fact (the mode tag lived
    # only in the streamed payload), so they stay NULL for old rows —
    # denominators in metrics treat NULL as "unknown" and exclude it.
    op.execute(
        """
        UPDATE rag_trace
        SET response_type = 'DOC_DELIVERED'
        WHERE retrieval_result -> 'doc_routing' ->> 'decision' = 'DELIVER'
          AND status = 'COMPLETED'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_rag_trace_response_created")
    op.execute("DROP INDEX IF EXISTS ix_rag_trace_user_created")
    op.execute("ALTER TABLE rag_trace DROP CONSTRAINT IF EXISTS ck_rag_trace_answer_mode")
    op.execute("ALTER TABLE rag_trace DROP CONSTRAINT IF EXISTS ck_rag_trace_response_type")
    op.execute(
        """
        ALTER TABLE rag_trace
            DROP COLUMN IF EXISTS answer_mode,
            DROP COLUMN IF EXISTS response_type,
            DROP COLUMN IF EXISTS user_id
        """
    )
