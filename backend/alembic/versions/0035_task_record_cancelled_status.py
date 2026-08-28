"""Add CANCELLED terminal status to task_record.

Profile generation tasks must be stoppable from the admin UI.  The worker
cooperatively polls task_record.status between documents, so cancellation is
purely a database state transition; no queue surgery is required.

Revision ID: 0035
Revises: 0034
Create Date: 2026-08-27
"""

from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE task_record DROP CONSTRAINT task_record_status_check")
    op.execute(
        "ALTER TABLE task_record ADD CONSTRAINT task_record_status_check "
        "CHECK (status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED'))"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE task_record SET status = 'FAILED', "
        "error_code = COALESCE(error_code, 'DOWNGRADE_CANCELLED'), "
        "error_message = COALESCE(error_message, '任务取消状态在降级时转为失败') "
        "WHERE status = 'CANCELLED'"
    )
    op.execute("ALTER TABLE task_record DROP CONSTRAINT task_record_status_check")
    op.execute(
        "ALTER TABLE task_record ADD CONSTRAINT task_record_status_check "
        "CHECK (status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED'))"
    )
