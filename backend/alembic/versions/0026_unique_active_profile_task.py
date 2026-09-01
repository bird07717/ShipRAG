"""Knowledge-level Profile rebuild support.

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-26
"""

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | None = None
depends_on: str | None = None

# Concurrent submissions (Knowledge detail "重建 Profile" and Index Build
# refresh hit the same candidate) must never start two parallel Profile
# generations.  The partial unique index makes the read-check-insert in the
# submission paths race-free, mirroring uq_task_record_active_kb_delete from
# revision 0024.
_INDEX = "uq_task_record_active_profile_per_index"
_PREDICATE = (
    "task_type IN ('PROFILE_GENERATE', 'PROFILE_BACKFILL', "
    "'PROFILE_REFRESH', 'PROFILE_REGENERATE') AND status IN ('QUEUED', 'RUNNING')"
)


def upgrade() -> None:
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX} ON task_record (index_id) WHERE {_PREDICATE}"
    )
    # A rebuild-orchestrated candidate build stays auditable on the index
    # itself, not only in task_record.metadata.
    op.execute("ALTER TABLE knowledge_index DROP CONSTRAINT knowledge_index_build_reason_check")
    op.execute(
        "ALTER TABLE knowledge_index ADD CONSTRAINT knowledge_index_build_reason_check "
        "CHECK (build_reason IN "
        "('INITIAL','DOCUMENT_CHANGED','REPROCESS','MODEL_CHANGED','MANUAL','PROFILE_REBUILD'))"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    op.execute("ALTER TABLE knowledge_index DROP CONSTRAINT knowledge_index_build_reason_check")
    op.execute(
        "ALTER TABLE knowledge_index ADD CONSTRAINT knowledge_index_build_reason_check "
        "CHECK (build_reason IN "
        "('INITIAL','DOCUMENT_CHANGED','REPROCESS','MODEL_CHANGED','MANUAL'))"
    )
