"""Add independently managed retrieval settings for every engine policy."""

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE engine_retrieval_config (
            execution_policy varchar(40) PRIMARY KEY,
            vector_top_k integer NOT NULL DEFAULT 10,
            bm25_top_k integer NOT NULL DEFAULT 10,
            fusion_top_k integer NOT NULL DEFAULT 20,
            rerank_top_n integer NOT NULL DEFAULT 10,
            context_max_chunks integer NOT NULL DEFAULT 8,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CHECK (execution_policy IN (
                'SUPPORT_ONLY', 'RAG_ONLY', 'SUPPORT_WITH_RAG_FALLBACK'
            )),
            CHECK (vector_top_k > 0 AND bm25_top_k > 0 AND fusion_top_k > 0
                   AND rerank_top_n > 0 AND context_max_chunks > 0)
        )
        """
    )
    op.execute(
        """
        INSERT INTO engine_retrieval_config (
            execution_policy, vector_top_k, bm25_top_k, fusion_top_k,
            rerank_top_n, context_max_chunks
        )
        SELECT 'RAG_ONLY', vector_top_k, bm25_top_k, fusion_top_k,
               rerank_top_n, context_max_chunks
        FROM rag_config WHERE id
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO engine_retrieval_config (execution_policy)
        VALUES ('SUPPORT_ONLY'), ('SUPPORT_WITH_RAG_FALLBACK')
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS engine_retrieval_config")
