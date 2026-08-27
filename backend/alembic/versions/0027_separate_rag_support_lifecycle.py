"""Separate automatic RAG activation from reviewed Support releases.

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-26
"""

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE knowledge_base
            ADD COLUMN rag_active_index_id uuid
                REFERENCES knowledge_index(id) ON DELETE RESTRICT,
            ADD COLUMN support_active_index_id uuid
                REFERENCES knowledge_index(id) ON DELETE RESTRICT,
            ADD COLUMN support_active_release_id uuid
                REFERENCES knowledge_release(id) ON DELETE RESTRICT
        """
    )
    op.execute(
        "CREATE INDEX ix_knowledge_base_rag_active_index "
        "ON knowledge_base (rag_active_index_id) WHERE rag_active_index_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_base_support_active_index "
        "ON knowledge_base (support_active_index_id) WHERE support_active_index_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_base_support_active_release "
        "ON knowledge_base (support_active_release_id) WHERE support_active_release_id IS NOT NULL"
    )

    op.execute(
        """
        ALTER TABLE conversation
            ADD COLUMN rag_index_id uuid
                REFERENCES knowledge_index(id) ON DELETE RESTRICT,
            ADD COLUMN support_index_id uuid
                REFERENCES knowledge_index(id) ON DELETE RESTRICT,
            ADD COLUMN support_release_id uuid
                REFERENCES knowledge_release(id) ON DELETE RESTRICT
        """
    )
    op.execute(
        "CREATE INDEX ix_conversation_rag_index ON conversation (rag_index_id) "
        "WHERE rag_index_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_conversation_support_index ON conversation (support_index_id) "
        "WHERE support_index_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_conversation_support_release ON conversation (support_release_id) "
        "WHERE support_release_id IS NOT NULL"
    )

    op.execute(
        "ALTER TABLE rag_trace ADD COLUMN support_release_id uuid "
        "REFERENCES knowledge_release(id) ON DELETE RESTRICT"
    )
    op.execute(
        "CREATE INDEX ix_rag_trace_support_release ON rag_trace (support_release_id) "
        "WHERE support_release_id IS NOT NULL"
    )

    # Keep data changes after all DDL. PostgreSQL reports pending FK trigger
    # events if an updated table is altered/indexed again in this transaction.
    op.execute(
        """
        UPDATE knowledge_base
        SET rag_active_index_id = active_index_id,
            support_active_index_id = active_index_id
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT r.id, r.kb_id, r.index_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY r.kb_id
                       ORDER BY r.published_at DESC NULLS LAST,
                                r.created_at DESC, r.id DESC
                   ) AS rn
            FROM knowledge_release r
            WHERE r.status = 'PUBLISHED'
        )
        UPDATE knowledge_base kb
        SET support_active_release_id = ranked.id,
            support_active_index_id = ranked.index_id
        FROM ranked
        WHERE ranked.kb_id = kb.id AND ranked.rn = 1
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_rag_trace_support_release")
    op.execute("ALTER TABLE rag_trace DROP COLUMN IF EXISTS support_release_id")
    op.execute("DROP INDEX IF EXISTS ix_conversation_support_release")
    op.execute("DROP INDEX IF EXISTS ix_conversation_support_index")
    op.execute("DROP INDEX IF EXISTS ix_conversation_rag_index")
    op.execute(
        "ALTER TABLE conversation DROP COLUMN IF EXISTS support_release_id, "
        "DROP COLUMN IF EXISTS support_index_id, DROP COLUMN IF EXISTS rag_index_id"
    )
    op.execute("DROP INDEX IF EXISTS ix_knowledge_base_support_active_release")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_base_support_active_index")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_base_rag_active_index")
    op.execute(
        "ALTER TABLE knowledge_base DROP COLUMN IF EXISTS support_active_release_id, "
        "DROP COLUMN IF EXISTS support_active_index_id, DROP COLUMN IF EXISTS rag_active_index_id"
    )
