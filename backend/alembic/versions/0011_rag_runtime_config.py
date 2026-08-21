"""Runtime-adjustable RAG retrieval config (admin-managed).

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-20
"""

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Single-row (id CHECK = true) table. begin_turn snapshots these values
    # every turn; the admin console writes here so retrieval tuning no
    # longer requires env changes or migrations.
    op.execute(
        """
        CREATE TABLE rag_config (
            id boolean PRIMARY KEY DEFAULT true CHECK (id),
            vector_top_k integer NOT NULL DEFAULT 10,
            bm25_top_k integer NOT NULL DEFAULT 10,
            fusion_top_k integer NOT NULL DEFAULT 20,
            rerank_top_n integer NOT NULL DEFAULT 10,
            context_max_chunks integer NOT NULL DEFAULT 8,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("INSERT INTO rag_config (id) VALUES (true)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rag_config")
