"""Raise default LLM output budget and disable thinking.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-20
"""

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | None = None
depends_on: str | None = None

_DEFAULT_LLM_ID = "00000000-0000-0000-0000-000000000003"


def upgrade() -> None:
    # Guarded update: only touches the seeded default-llm row while it still
    # carries the 0003 seed values. Operator-modified rows are left alone.
    op.execute(
        f"""
        UPDATE model_config
        SET parameters = jsonb_set(
                jsonb_set(parameters, '{{max_tokens}}', '4096'),
                '{{thinking}}', '{{"type":"disabled"}}'::jsonb
            ),
            updated_at = now()
        WHERE id = '{_DEFAULT_LLM_ID}'
          AND model_type = 'LLM'
          AND parameters->>'max_tokens' = '2048'
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE model_config
        SET parameters = jsonb_set(
                jsonb_set(parameters, '{{max_tokens}}', '2048'),
                '{{thinking}}', '{{"type":"enabled"}}'::jsonb
            ),
            updated_at = now()
        WHERE id = '{_DEFAULT_LLM_ID}'
          AND model_type = 'LLM'
          AND parameters->>'max_tokens' = '4096'
        """
    )
