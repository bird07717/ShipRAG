"""Expand trace response type for Support clarification outcomes.

Revision ID: 0032
Revises: 0031
Create Date: 2026-08-27
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # 0015 expanded message.response_type but accidentally left this trace
    # column at the varchar(20) introduced by 0014.  The supported outcome
    # CLARIFICATION_REQUIRED is 22 characters and otherwise fails at commit.
    op.alter_column(
        "rag_trace",
        "response_type",
        existing_type=sa.String(length=20),
        type_=sa.String(length=40),
        existing_nullable=True,
    )


def downgrade() -> None:
    # A safe downgrade cannot narrow this column: completed traces may already
    # contain CLARIFICATION_REQUIRED (22 chars), which is a valid contract.
    pass
