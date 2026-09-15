"""Add index-bound RAG document understanding cards.

Revision ID: 0046
Revises: 0045
Create Date: 2026-09-13
"""

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE rag_document_card (
            id uuid PRIMARY KEY,
            knowledge_id uuid NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
            index_id uuid NOT NULL REFERENCES knowledge_index(id) ON DELETE CASCADE,
            document_id uuid NOT NULL REFERENCES document_source(id) ON DELETE RESTRICT,
            schema_version integer NOT NULL DEFAULT 1 CHECK (schema_version > 0),
            document_type varchar(30) CHECK (
                document_type IS NULL OR document_type IN (
                    'PROCEDURE', 'REFERENCE', 'TROUBLESHOOTING', 'OVERVIEW', 'MIXED'
                )
            ),
            card_payload jsonb,
            routing_text text NOT NULL DEFAULT '',
            routing_embedding vector(1024),
            content_fingerprint char(64) CHECK (
                content_fingerprint IS NULL OR content_fingerprint ~ '^[0-9a-f]{64}$'
            ),
            generation_status varchar(20) NOT NULL DEFAULT 'PENDING' CHECK (
                generation_status IN ('PENDING', 'READY', 'FAILED', 'STALE')
            ),
            review_status varchar(20) NOT NULL DEFAULT 'AUTO' CHECK (
                review_status IN ('AUTO', 'CONFIRMED', 'REJECTED')
            ),
            revision integer NOT NULL DEFAULT 0 CHECK (revision >= 0),
            generator_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
            error_code varchar(100),
            error_message text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (index_id, document_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_rag_document_card_scope
        ON rag_document_card (knowledge_id, index_id, generation_status, review_status)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_rag_document_card_embedding_hnsw
        ON rag_document_card USING hnsw (routing_embedding vector_cosine_ops)
        """
    )
    op.execute(
        """
        CREATE INDEX rag_document_card_search_bm25_idx
        ON rag_document_card
        USING bm25 (
            id,
            (routing_text::pdb.lindera(chinese)),
            knowledge_id,
            index_id,
            document_id,
            document_type,
            generation_status,
            review_status
        ) WITH (key_field='id')
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_task_record_active_rag_card_per_index
        ON task_record (index_id)
        WHERE task_type = 'RAG_DOCUMENT_CARD_GENERATE'
          AND status IN ('QUEUED', 'RUNNING')
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_task_record_active_rag_card_per_index")
    op.execute("DROP INDEX IF EXISTS rag_document_card_search_bm25_idx")
    op.execute("DROP INDEX IF EXISTS ix_rag_document_card_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_rag_document_card_scope")
    op.execute("DROP TABLE IF EXISTS rag_document_card")
