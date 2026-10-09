"""Persist non-secret identities of actual index build models.

Revision ID: 0048
Revises: 0047
"""

from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Do not fill old indexes using today's mutable configuration.
    op.execute(
        "ALTER TABLE knowledge_index ADD COLUMN build_models jsonb NOT NULL DEFAULT '{}'::jsonb "
        "CHECK (jsonb_typeof(build_models) = 'object')"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE knowledge_index DROP COLUMN build_models")
