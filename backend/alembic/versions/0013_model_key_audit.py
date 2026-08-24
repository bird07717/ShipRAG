"""Audit trail for model config and API key changes.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-21
"""

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE model_config_history (
            id uuid PRIMARY KEY,
            model_config_id uuid NOT NULL
                REFERENCES model_config(id) ON DELETE CASCADE,
            action varchar(60) NOT NULL,
            changed_fields jsonb NOT NULL DEFAULT '[]'::jsonb,
            actor varchar(120) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_model_config_history_model"
        " ON model_config_history (model_config_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS model_config_history CASCADE")
