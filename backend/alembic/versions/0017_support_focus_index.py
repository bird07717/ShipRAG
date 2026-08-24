"""Pin technical-support conversations to the delivered index version.

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-24
"""

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE conversation
        ADD COLUMN focus_index_id uuid
            REFERENCES knowledge_index(id) ON DELETE RESTRICT
        """
    )
    # Existing focused conversations predate index pinning. The current active
    # index is the only recoverable version boundary for those rows.
    op.execute(
        """
        UPDATE conversation c
        SET focus_index_id = kb.active_index_id
        FROM knowledge_base kb
        WHERE c.knowledge_id = kb.id
          AND c.focus_document_id IS NOT NULL
          AND kb.active_index_id IS NOT NULL
          AND EXISTS (
              SELECT 1
              FROM document_element e
              WHERE e.index_id = kb.active_index_id
                AND e.document_id = c.focus_document_id
          )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_conversation_focus_index
        ON conversation (focus_index_id)
        WHERE focus_index_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_conversation_focus_index")
    op.execute("ALTER TABLE conversation DROP COLUMN IF EXISTS focus_index_id")
