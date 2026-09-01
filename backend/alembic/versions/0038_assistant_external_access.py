"""Add the data needed by the narrow external Assistant API.

Revision ID: 0038
Revises: 0037
"""

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE conversation ADD COLUMN IF NOT EXISTS last_activity_at timestamptz")
    op.execute(
        "UPDATE conversation SET last_activity_at = updated_at WHERE last_activity_at IS NULL"
    )
    op.execute("ALTER TABLE conversation ALTER COLUMN last_activity_at SET NOT NULL")
    op.execute("ALTER TABLE conversation ALTER COLUMN last_activity_at SET DEFAULT now()")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_conversation_user_kb_activity "
        "ON conversation (user_id, knowledge_id, last_activity_at DESC, id DESC)"
    )
    op.execute(
        "ALTER TABLE message ADD COLUMN IF NOT EXISTS response_payload jsonb "
        "NOT NULL DEFAULT '{}'::jsonb"
    )
    op.execute(
        "ALTER TABLE rag_trace ADD COLUMN IF NOT EXISTS runtime_snapshot jsonb "
        "NOT NULL DEFAULT '{}'::jsonb, ADD COLUMN IF NOT EXISTS redacted_at timestamptz"
    )
    op.execute("ALTER TABLE rag_trace DROP CONSTRAINT IF EXISTS rag_trace_index_id_fkey")
    op.execute("ALTER TABLE rag_trace ALTER COLUMN index_id DROP NOT NULL")
    op.execute(
        "ALTER TABLE rag_trace ADD CONSTRAINT rag_trace_index_id_fkey "
        "FOREIGN KEY (index_id) REFERENCES knowledge_index(id) ON DELETE SET NULL"
    )
    op.execute(
        """
        CREATE TABLE assistant_request (
            id uuid PRIMARY KEY,
            external_user_id varchar(100) NOT NULL,
            knowledge_id uuid NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
            idempotency_key varchar(200) NOT NULL,
            request_hash varchar(64) NOT NULL,
            conversation_id uuid REFERENCES conversation(id) ON DELETE CASCADE,
            user_message_id uuid REFERENCES message(id) ON DELETE SET NULL,
            assistant_message_id uuid REFERENCES message(id) ON DELETE SET NULL,
            trace_id uuid,
            status varchar(20) NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED')),
            terminal_response jsonb NOT NULL DEFAULT '{}'::jsonb,
            terminal_error jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (external_user_id, knowledge_id, idempotency_key)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_assistant_request_running_conversation "
        "ON assistant_request (conversation_id) WHERE status = 'RUNNING' AND conversation_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_assistant_request_status_updated ON assistant_request (status, updated_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_assistant_request_status_updated")
    op.execute("DROP INDEX IF EXISTS uq_assistant_request_running_conversation")
    op.execute("DROP TABLE IF EXISTS assistant_request")
    op.execute("ALTER TABLE rag_trace DROP CONSTRAINT IF EXISTS rag_trace_index_id_fkey")
    op.execute("DELETE FROM rag_trace WHERE index_id IS NULL")
    op.execute("ALTER TABLE rag_trace ALTER COLUMN index_id SET NOT NULL")
    op.execute(
        "ALTER TABLE rag_trace ADD CONSTRAINT rag_trace_index_id_fkey "
        "FOREIGN KEY (index_id) REFERENCES knowledge_index(id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE rag_trace DROP COLUMN IF EXISTS redacted_at, DROP COLUMN IF EXISTS runtime_snapshot"
    )
    op.execute("ALTER TABLE message DROP COLUMN IF EXISTS response_payload")
    op.execute("DROP INDEX IF EXISTS ix_conversation_user_kb_activity")
    op.execute("ALTER TABLE conversation DROP COLUMN IF EXISTS last_activity_at")
