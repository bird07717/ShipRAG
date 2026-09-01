"""Add routing metadata to generated and published Support profiles.

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-25
"""

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    for table in ("problem_profile_draft", "document_problem_profile"):
        op.execute(f"ALTER TABLE {table} ADD COLUMN routing_summary text NOT NULL DEFAULT ''")
        op.execute(f"ALTER TABLE {table} ADD COLUMN symptoms jsonb NOT NULL DEFAULT '[]'::jsonb")
        op.execute(f"ALTER TABLE {table} ADD COLUMN exclusions jsonb NOT NULL DEFAULT '[]'::jsonb")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_symptoms_array "
            "CHECK (jsonb_typeof(symptoms) = 'array')"
        )
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_exclusions_array "
            "CHECK (jsonb_typeof(exclusions) = 'array')"
        )


def downgrade() -> None:
    for table in ("problem_profile_draft", "document_problem_profile"):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS ck_{table}_exclusions_array")
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS ck_{table}_symptoms_array")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS exclusions")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS symptoms")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS routing_summary")
