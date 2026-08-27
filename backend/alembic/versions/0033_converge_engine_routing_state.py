"""Converge Gateway policies and separate conversation routing state.

Revision ID: 0033
Revises: 0032
Create Date: 2026-08-27
"""

from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # SUPPORT_ONLY was an administrative shortcut, not a DemoChat runtime
    # mode. Keep existing conversations usable by promoting their snapshot to
    # Hybrid before the old constraint is replaced.
    op.execute(
        "UPDATE engine_runtime_policy SET execution_policy = "
        "'SUPPORT_WITH_RAG_FALLBACK' WHERE execution_policy = 'SUPPORT_ONLY'"
    )
    op.execute(
        "UPDATE conversation SET execution_policy = 'SUPPORT_WITH_RAG_FALLBACK' "
        "WHERE execution_policy = 'SUPPORT_ONLY'"
    )
    op.execute(
        "DELETE FROM engine_retrieval_config WHERE execution_policy = 'SUPPORT_ONLY'"
    )

    op.execute(
        "ALTER TABLE engine_runtime_policy DROP CONSTRAINT IF EXISTS "
        "engine_runtime_policy_execution_policy_check"
    )
    op.execute(
        "ALTER TABLE engine_runtime_policy ADD CONSTRAINT "
        "engine_runtime_policy_execution_policy_check CHECK (execution_policy IN "
        "('RAG_ONLY', 'SUPPORT_WITH_RAG_FALLBACK'))"
    )
    op.execute(
        "ALTER TABLE engine_retrieval_config DROP CONSTRAINT IF EXISTS "
        "engine_retrieval_config_execution_policy_check"
    )
    op.execute(
        "ALTER TABLE engine_retrieval_config ADD CONSTRAINT "
        "engine_retrieval_config_execution_policy_check CHECK (execution_policy IN "
        "('RAG_ONLY', 'SUPPORT_WITH_RAG_FALLBACK'))"
    )

    op.execute(
        "ALTER TABLE conversation DROP CONSTRAINT IF EXISTS "
        "ck_conversation_execution_policy, DROP CONSTRAINT IF EXISTS "
        "ck_conversation_execution_engine"
    )
    op.execute("ALTER TABLE conversation RENAME COLUMN execution_policy TO execution_policy_snapshot")
    op.execute("ALTER TABLE conversation RENAME COLUMN execution_engine TO last_execution_engine")
    op.execute(
        "ALTER TABLE conversation "
        "ADD COLUMN conversation_mode varchar(30), "
        "ADD COLUMN focus_epoch integer NOT NULL DEFAULT 0, "
        "ADD COLUMN pending_switch_options jsonb NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute(
        "UPDATE conversation SET conversation_mode = CASE "
        "WHEN execution_policy_snapshot = 'RAG_ONLY' THEN 'RAG_ONLY' "
        "WHEN focus_document_id IS NOT NULL THEN 'FOCUSED_SUPPORT' "
        "ELSE 'SUPPORT_DISCOVERY' END"
    )
    op.execute(
        "UPDATE conversation SET focus_epoch = 1 WHERE focus_document_id IS NOT NULL"
    )
    op.execute(
        "UPDATE conversation SET pending_switch_options = "
        "COALESCE(chat_context -> 'pending_options', '[]'::jsonb)"
    )
    op.execute(
        "ALTER TABLE conversation ADD CONSTRAINT ck_conversation_execution_policy_snapshot "
        "CHECK (execution_policy_snapshot IS NULL OR execution_policy_snapshot IN "
        "('RAG_ONLY', 'SUPPORT_WITH_RAG_FALLBACK'))"
    )
    op.execute(
        "ALTER TABLE conversation ADD CONSTRAINT ck_conversation_last_execution_engine "
        "CHECK (last_execution_engine IS NULL OR last_execution_engine IN ('SUPPORT', 'RAG'))"
    )
    op.execute(
        "ALTER TABLE conversation ADD CONSTRAINT ck_conversation_mode CHECK "
        "(conversation_mode IS NULL OR conversation_mode IN "
        "('SUPPORT_DISCOVERY', 'FOCUSED_SUPPORT', 'RAG_ONLY'))"
    )
    op.execute(
        "ALTER TABLE conversation ADD CONSTRAINT ck_conversation_focus_epoch "
        "CHECK (focus_epoch >= 0)"
    )
    op.execute(
        "ALTER TABLE conversation ADD CONSTRAINT ck_conversation_pending_switch_options "
        "CHECK (jsonb_typeof(pending_switch_options) = 'array')"
    )


def downgrade() -> None:
    # Downgrading would have to reintroduce the retired SUPPORT_ONLY policy
    # and silently lose the new state fields. Keep this migration forward-only.
    pass
