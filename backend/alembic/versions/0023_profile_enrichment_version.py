"""Track the Profile enrichment schema version for safe backfills.

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-25
"""

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    for table in ("problem_profile_draft", "document_problem_profile"):
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN profile_enrichment_version integer NOT NULL DEFAULT 0"
        )
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_profile_enrichment_version "
            "CHECK (profile_enrichment_version >= 0)"
        )


def downgrade() -> None:
    for table in ("problem_profile_draft", "document_problem_profile"):
        op.execute(
            f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS ck_{table}_profile_enrichment_version"
        )
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS profile_enrichment_version")
