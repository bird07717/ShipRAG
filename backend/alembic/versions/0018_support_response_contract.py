"""Add the explicit NEED_MORE_INFORMATION support response type.

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-24
"""

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE rag_trace DROP CONSTRAINT IF EXISTS ck_rag_trace_response_type")
    op.execute(
        """
        ALTER TABLE rag_trace ADD CONSTRAINT ck_rag_trace_response_type
        CHECK (response_type IS NULL OR response_type IN (
            'ANSWERED', 'UNCONFIRMED', 'OUT_OF_SCOPE', 'DOC_DELIVERED',
            'DOCUMENT_DELIVERED', 'CLARIFICATION_REQUIRED',
            'NEED_MORE_INFORMATION', 'NO_MATCH', 'ESCALATION_REQUIRED'
        ))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE rag_trace DROP CONSTRAINT IF EXISTS ck_rag_trace_response_type")
    op.execute(
        """
        ALTER TABLE rag_trace ADD CONSTRAINT ck_rag_trace_response_type
        CHECK (response_type IS NULL OR response_type IN (
            'ANSWERED', 'UNCONFIRMED', 'OUT_OF_SCOPE', 'DOC_DELIVERED',
            'DOCUMENT_DELIVERED', 'CLARIFICATION_REQUIRED', 'NO_MATCH',
            'ESCALATION_REQUIRED'
        ))
        """
    )
