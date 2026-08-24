"""Add optimistic concurrency control for technical-support workflow state.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-24
"""

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE conversation ADD COLUMN support_context_version integer NOT NULL DEFAULT 0 "
        "CHECK (support_context_version >= 0)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE conversation DROP COLUMN IF EXISTS support_context_version")
