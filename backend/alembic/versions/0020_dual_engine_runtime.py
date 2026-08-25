"""Separate Support and RAG runtime policy and trace identity.

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-25
"""

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE engine_runtime_policy (
            id uuid PRIMARY KEY,
            kb_id uuid NOT NULL UNIQUE REFERENCES knowledge_base(id) ON DELETE CASCADE,
            execution_policy varchar(40) NOT NULL,
            config_version integer NOT NULL DEFAULT 1,
            fallback_conditions jsonb NOT NULL DEFAULT '["NO_MATCH"]'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CHECK (execution_policy IN (
                'SUPPORT_ONLY', 'RAG_ONLY', 'SUPPORT_WITH_RAG_FALLBACK'
            )),
            CHECK (config_version > 0),
            CHECK (jsonb_typeof(fallback_conditions) = 'array')
        )
        """
    )
    op.execute(
        """
        ALTER TABLE conversation
            ADD COLUMN execution_policy varchar(40),
            ADD COLUMN execution_engine varchar(20),
            ADD COLUMN engine_config_version integer,
            ADD CONSTRAINT ck_conversation_execution_policy CHECK (
                execution_policy IS NULL OR execution_policy IN (
                    'SUPPORT_ONLY', 'RAG_ONLY', 'SUPPORT_WITH_RAG_FALLBACK'
                )
            ),
            ADD CONSTRAINT ck_conversation_execution_engine CHECK (
                execution_engine IS NULL OR execution_engine IN ('SUPPORT', 'RAG')
            )
        """
    )
    op.execute(
        """
        ALTER TABLE rag_trace
            ADD COLUMN engine varchar(20) NOT NULL DEFAULT 'RAG',
            ADD COLUMN stage varchar(60),
            ADD COLUMN parent_trace_id uuid,
            ADD COLUMN fallback_from varchar(20),
            ADD COLUMN fallback_reason varchar(100),
            ADD CONSTRAINT ck_rag_trace_engine CHECK (engine IN ('SUPPORT', 'RAG')),
            ADD CONSTRAINT ck_rag_trace_fallback_from CHECK (
                fallback_from IS NULL OR fallback_from IN ('SUPPORT', 'RAG')
            )
        """
    )
    op.execute(
        """
        UPDATE rag_trace
        SET engine = 'SUPPORT'
        WHERE retrieval_result ->> 'support_version' IS NOT NULL
        """
    )
    op.execute(
        "CREATE INDEX ix_rag_trace_engine_status_created "
        "ON rag_trace (engine, status, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_rag_trace_parent_trace "
        "ON rag_trace (parent_trace_id) WHERE parent_trace_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_rag_trace_parent_trace")
    op.execute("DROP INDEX IF EXISTS ix_rag_trace_engine_status_created")
    op.execute(
        "ALTER TABLE rag_trace DROP CONSTRAINT IF EXISTS ck_rag_trace_fallback_from, "
        "DROP CONSTRAINT IF EXISTS ck_rag_trace_engine, "
        "DROP COLUMN IF EXISTS fallback_reason, DROP COLUMN IF EXISTS fallback_from, "
        "DROP COLUMN IF EXISTS parent_trace_id, DROP COLUMN IF EXISTS stage, "
        "DROP COLUMN IF EXISTS engine"
    )
    op.execute(
        "ALTER TABLE conversation DROP CONSTRAINT IF EXISTS ck_conversation_execution_engine, "
        "DROP CONSTRAINT IF EXISTS ck_conversation_execution_policy, "
        "DROP COLUMN IF EXISTS engine_config_version, DROP COLUMN IF EXISTS execution_engine, "
        "DROP COLUMN IF EXISTS execution_policy"
    )
    op.execute("DROP TABLE IF EXISTS engine_runtime_policy")
