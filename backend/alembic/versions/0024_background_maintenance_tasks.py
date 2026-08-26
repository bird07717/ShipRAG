"""Durable maintenance tasks for profile generation and knowledge-base deletion.

Revision ID: 0024
Revises: 0023
Create Date: 2026-08-26
"""

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE knowledge_base DROP CONSTRAINT knowledge_base_status_check")
    op.execute(
        "ALTER TABLE knowledge_base ADD CONSTRAINT knowledge_base_status_check "
        "CHECK (status IN ('ENABLED', 'DISABLED', 'DELETING'))"
    )
    op.execute("ALTER TABLE task_record DROP CONSTRAINT task_record_kb_id_fkey")
    op.execute(
        "ALTER TABLE task_record ADD CONSTRAINT task_record_kb_id_fkey "
        "FOREIGN KEY (kb_id) REFERENCES knowledge_base(id) ON DELETE SET NULL"
    )
    op.execute(
        """
        CREATE TABLE storage_cleanup_object (
            task_id uuid NOT NULL REFERENCES task_record(id) ON DELETE CASCADE,
            minio_bucket varchar(100) NOT NULL,
            minio_object_key text NOT NULL,
            status varchar(20) NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING', 'DELETED', 'FAILED')),
            attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            error_message text,
            created_at timestamptz NOT NULL DEFAULT now(),
            deleted_at timestamptz,
            PRIMARY KEY (task_id, minio_bucket, minio_object_key)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_storage_cleanup_object_pending "
        "ON storage_cleanup_object(task_id, status, created_at)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_task_record_active_kb_delete "
        "ON task_record(kb_id) "
        "WHERE task_type = 'KNOWLEDGE_BASE_DELETE' AND status IN ('QUEUED', 'RUNNING')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_task_record_active_kb_delete")
    op.execute("DROP INDEX IF EXISTS ix_storage_cleanup_object_pending")
    op.execute("DROP TABLE IF EXISTS storage_cleanup_object")
    op.execute("ALTER TABLE task_record DROP CONSTRAINT task_record_kb_id_fkey")
    op.execute(
        "ALTER TABLE task_record ADD CONSTRAINT task_record_kb_id_fkey "
        "FOREIGN KEY (kb_id) REFERENCES knowledge_base(id) ON DELETE CASCADE"
    )
    op.execute("ALTER TABLE knowledge_base DROP CONSTRAINT knowledge_base_status_check")
    op.execute(
        "ALTER TABLE knowledge_base ADD CONSTRAINT knowledge_base_status_check "
        "CHECK (status IN ('ENABLED', 'DISABLED'))"
    )
