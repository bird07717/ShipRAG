"""Add M5 strict BM25, rerank configuration, and citation trace.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-17
"""

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None

BM25_ENGINE = "pg_search:0.25.0:lindera-chinese"


def upgrade() -> None:
    statements = [
        "CREATE EXTENSION IF NOT EXISTS pg_search CASCADE",
        """
        INSERT INTO model_config (
            id, name, model_type, provider, base_url, model_name, parameters, enabled
        ) VALUES (
            '00000000-0000-0000-0000-000000000007',
            'default-rerank', 'RERANK', 'siliconflow',
            'https://api.siliconflow.cn/v1/', 'Qwen/Qwen3-VL-Reranker-8B',
            jsonb_build_object('secret_source', 'environment', 'top_n', 10,
                               'return_documents', false), true
        )
        """,
        """
        CREATE INDEX document_chunk_search_bm25_idx
        ON document_chunk
        USING bm25 (
            id,
            (search_text::pdb.lindera(chinese)),
            kb_id,
            index_id,
            document_id,
            chunk_type
        ) WITH (key_field='id')
        """,
        f"""
        ALTER TABLE knowledge_index
        ALTER COLUMN bm25_engine SET DEFAULT '{BM25_ENGINE}'
        """,
        f"""
        UPDATE knowledge_index SET bm25_engine = '{BM25_ENGINE}'
        WHERE status IN ('READY','ACTIVE','DEPRECATED')
        """,
        """
        ALTER TABLE rag_trace
        ADD COLUMN citation_result jsonb NOT NULL DEFAULT '{}'::jsonb
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    op.execute("ALTER TABLE rag_trace DROP COLUMN IF EXISTS citation_result")
    op.execute("ALTER TABLE knowledge_index ALTER COLUMN bm25_engine SET DEFAULT 'NOT_BUILT'")
    op.execute(
        f"UPDATE knowledge_index SET bm25_engine = 'NOT_BUILT' WHERE bm25_engine = '{BM25_ENGINE}'"
    )
    op.execute("DROP INDEX IF EXISTS document_chunk_search_bm25_idx")
    op.execute("DELETE FROM model_config WHERE id = '00000000-0000-0000-0000-000000000007'")
