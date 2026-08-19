"""Activate the evidence-grounded RAG prompt v2.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-17
"""

from alembic import op
from app.rag.prompt import DEFAULT_RAG_PROMPT

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | None = None
depends_on: str | None = None

_PROMPT_ID = "00000000-0000-0000-0000-000000000007"


def upgrade() -> None:
    escaped_prompt = DEFAULT_RAG_PROMPT.replace("'", "''")
    op.execute("UPDATE prompt_template SET active = false WHERE active")
    op.execute(
        f"""
        INSERT INTO prompt_template (id, name, version, content, active)
        VALUES ('{_PROMPT_ID}', 'default-rag', 2, '{escaped_prompt}', true)
        ON CONFLICT (name, version) DO UPDATE
        SET content = EXCLUDED.content, active = true, updated_at = now()
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM prompt_template WHERE name = 'default-rag' AND version = 2")
    op.execute(
        """
        UPDATE prompt_template
        SET active = true, updated_at = now()
        WHERE id = '00000000-0000-0000-0000-000000000004'
        """
    )
