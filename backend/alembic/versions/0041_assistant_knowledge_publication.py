"""Separate Assistant publication from the knowledge-base runtime status.

Revision ID: 0041
Revises: 0040
"""

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing ENABLED knowledge bases are already visible through the public
    # Assistant contract.  Preserve that state during the rolling upgrade,
    # while making every knowledge base created afterwards private by default.
    op.execute("ALTER TABLE knowledge_base ADD COLUMN assistant_online boolean")
    op.execute(
        "UPDATE knowledge_base SET assistant_online = (status = 'ENABLED') "
        "WHERE assistant_online IS NULL"
    )
    op.execute(
        "ALTER TABLE knowledge_base ALTER COLUMN assistant_online SET DEFAULT false, "
        "ALTER COLUMN assistant_online SET NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_base_assistant_online "
        "ON knowledge_base (name, id) WHERE assistant_online"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_base_assistant_online")
    op.execute("ALTER TABLE knowledge_base DROP COLUMN IF EXISTS assistant_online")
