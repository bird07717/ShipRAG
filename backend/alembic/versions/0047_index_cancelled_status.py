"""Add CANCELLED to index statuses and SKIPPED/CANCELLED to document statuses.

ADR-0023: an index build can be cooperatively cancelled from the console;
the index and its unprocessed documents settle as CANCELLED — a terminal
state deletable and retryable like FAILED.  ADR-0022's document-level skip
cap also writes index_document.status = 'SKIPPED', which was missing from
the check constraint (the path never executed before this migration).

Revision ID: 0047
Revises: 0046
Create Date: 2026-09-17
"""

from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE knowledge_index DROP CONSTRAINT knowledge_index_status_check")
    op.execute(
        "ALTER TABLE knowledge_index ADD CONSTRAINT knowledge_index_status_check "
        "CHECK (status IN ('BUILDING','READY','ACTIVE','DEPRECATED','FAILED',"
        "'CANCELLED','DELETING'))"
    )
    op.execute("ALTER TABLE index_document DROP CONSTRAINT index_document_status_check")
    op.execute(
        "ALTER TABLE index_document ADD CONSTRAINT index_document_status_check "
        "CHECK (status IN ('QUEUED','PARSING','PROCESSING_IMAGES','CHUNKING',"
        "'EMBEDDING','READY','FAILED','SKIPPED','CANCELLED'))"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE index_document SET status = 'FAILED', "
        "error_code = COALESCE(error_code, 'DOWNGRADE_CANCELLED') "
        "WHERE status IN ('CANCELLED', 'SKIPPED')"
    )
    op.execute("ALTER TABLE index_document DROP CONSTRAINT index_document_status_check")
    op.execute(
        "ALTER TABLE index_document ADD CONSTRAINT index_document_status_check "
        "CHECK (status IN ('QUEUED','PARSING','PROCESSING_IMAGES','CHUNKING',"
        "'EMBEDDING','READY','FAILED'))"
    )
    op.execute(
        "UPDATE knowledge_index SET status = 'FAILED', "
        "error_code = COALESCE(error_code, 'DOWNGRADE_CANCELLED') "
        "WHERE status = 'CANCELLED'"
    )
    op.execute("ALTER TABLE knowledge_index DROP CONSTRAINT knowledge_index_status_check")
    op.execute(
        "ALTER TABLE knowledge_index ADD CONSTRAINT knowledge_index_status_check "
        "CHECK (status IN ('BUILDING','READY','ACTIVE','DEPRECATED','FAILED','DELETING'))"
    )
