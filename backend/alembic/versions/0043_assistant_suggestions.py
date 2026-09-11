"""Store per-knowledge-base Assistant suggestions and a write revision.

Revision ID: 0043
Revises: 0042
"""

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE knowledge_base "
        "ADD COLUMN assistant_suggestions jsonb NOT NULL DEFAULT '[]'::jsonb, "
        "ADD COLUMN assistant_config_revision integer NOT NULL DEFAULT 0, "
        "ADD CONSTRAINT ck_assistant_suggestions_array CHECK ("
        "jsonb_typeof(assistant_suggestions) = 'array' "
        "AND jsonb_array_length(assistant_suggestions) <= 3), "
        "ADD CONSTRAINT ck_assistant_config_revision CHECK (assistant_config_revision >= 0)"
    )
    # Preserve the public welcome of previously online KBs only. New/private
    # KBs must choose their own questions, never inherit unrelated examples.
    op.execute(
        "UPDATE knowledge_base SET assistant_suggestions = "
        '\'["如何使用U盘升级PDC","如何更换PSU电源板","如何手动自检VDR"]\'::jsonb '
        "WHERE assistant_online"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE knowledge_base DROP COLUMN assistant_suggestions, "
        "DROP COLUMN assistant_config_revision"
    )
