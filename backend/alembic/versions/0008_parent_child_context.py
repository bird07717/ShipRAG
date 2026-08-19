"""Add parent-child chunks, adjacency, and completeness metadata.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-17
"""

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    statements = [
        """
        CREATE TABLE document_parent_chunk (
            id uuid PRIMARY KEY,
            kb_id uuid NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
            index_id uuid NOT NULL REFERENCES knowledge_index(id) ON DELETE CASCADE,
            index_document_id uuid NOT NULL REFERENCES index_document(id) ON DELETE CASCADE,
            document_id uuid NOT NULL REFERENCES document_source(id) ON DELETE RESTRICT,
            parent_type varchar(30) NOT NULL CHECK (
                parent_type IN ('SECTION','SECTION_WINDOW')
            ),
            sequence_no integer NOT NULL CHECK (sequence_no > 0),
            content text NOT NULL,
            token_count integer NOT NULL CHECK (token_count > 0),
            section_path jsonb NOT NULL DEFAULT '[]'::jsonb,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (index_document_id, sequence_no)
        )
        """,
        """
        CREATE INDEX ix_document_parent_chunk_lookup
        ON document_parent_chunk (index_id, document_id, sequence_no)
        """,
        """
        CREATE TABLE parent_chunk_element (
            parent_id uuid NOT NULL REFERENCES document_parent_chunk(id) ON DELETE CASCADE,
            element_id uuid NOT NULL REFERENCES document_element(id) ON DELETE CASCADE,
            ordinal integer NOT NULL CHECK (ordinal > 0),
            PRIMARY KEY (parent_id, element_id),
            UNIQUE (parent_id, ordinal)
        )
        """,
        """
        ALTER TABLE document_chunk
        ADD COLUMN parent_id uuid REFERENCES document_parent_chunk(id) ON DELETE CASCADE,
        ADD COLUMN previous_chunk_id uuid REFERENCES document_chunk(id) ON DELETE SET NULL,
        ADD COLUMN next_chunk_id uuid REFERENCES document_chunk(id) ON DELETE SET NULL,
        ADD COLUMN suspected_incomplete boolean NOT NULL DEFAULT false,
        ADD COLUMN incomplete_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
        ADD COLUMN is_procedural boolean NOT NULL DEFAULT false
        """,
        """
        CREATE INDEX ix_document_chunk_parent_sequence
        ON document_chunk (parent_id, sequence_no)
        """,
        """
        CREATE INDEX ix_document_chunk_neighbor_expansion
        ON document_chunk (index_id, suspected_incomplete, is_procedural)
        """,
        """
        UPDATE knowledge_base SET rebuild_required = true
        WHERE active_index_id IS NOT NULL
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    statements = [
        "DROP INDEX IF EXISTS ix_document_chunk_neighbor_expansion",
        "DROP INDEX IF EXISTS ix_document_chunk_parent_sequence",
        "ALTER TABLE document_chunk DROP COLUMN IF EXISTS is_procedural",
        "ALTER TABLE document_chunk DROP COLUMN IF EXISTS incomplete_reasons",
        "ALTER TABLE document_chunk DROP COLUMN IF EXISTS suspected_incomplete",
        "ALTER TABLE document_chunk DROP COLUMN IF EXISTS next_chunk_id",
        "ALTER TABLE document_chunk DROP COLUMN IF EXISTS previous_chunk_id",
        "ALTER TABLE document_chunk DROP COLUMN IF EXISTS parent_id",
        "DROP TABLE IF EXISTS parent_chunk_element",
        "DROP TABLE IF EXISTS document_parent_chunk",
    ]
    for statement in statements:
        op.execute(statement)
