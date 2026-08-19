"""Enable the pgvector extension.

Revision ID: 0001
Revises:
Create Date: 2026-08-17
"""

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    # The extension can be shared by later migrations; never remove it implicitly.
    pass
