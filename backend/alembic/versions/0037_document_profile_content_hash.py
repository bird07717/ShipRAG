# ruff: noqa: E501
"""Document Profile incremental generation: content hash per profile row.

Revision ID: 0037
Revises: 0036
Create Date: 2026-08-28
"""

from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Hash of the RAG content the last successful AUTO generation consumed.
    # Unchanged hash lets full-KB runs skip unchanged documents, so profile
    # regeneration scales with the delta instead of the corpus size.
    op.execute(
        """
        ALTER TABLE document_profile
            ADD COLUMN IF NOT EXISTS content_hash varchar(64)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE document_profile
            DROP COLUMN IF EXISTS content_hash
        """
    )
