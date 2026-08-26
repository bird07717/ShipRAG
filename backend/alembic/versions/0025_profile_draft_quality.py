"""Persist offline Profile quality diagnostics on candidate drafts.

Revision ID: 0025
Revises: 0024
Create Date: 2026-08-26
"""

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE problem_profile_draft ADD COLUMN quality_status varchar(30) "
        "NOT NULL DEFAULT 'PASS'"
    )
    op.execute(
        "ALTER TABLE problem_profile_draft ADD COLUMN quality_notes jsonb "
        "NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute(
        "ALTER TABLE problem_profile_draft ADD COLUMN quality_evidence jsonb "
        "NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute(
        "ALTER TABLE problem_profile_draft ADD CONSTRAINT ck_problem_profile_draft_quality_status "
        "CHECK (quality_status IN ('PASS', 'REVIEW_REQUIRED', 'FALLBACK', 'MANUAL_REVIEWED'))"
    )
    op.execute(
        "ALTER TABLE problem_profile_draft ADD CONSTRAINT ck_problem_profile_draft_quality_notes_array "
        "CHECK (jsonb_typeof(quality_notes) = 'array')"
    )
    op.execute(
        "ALTER TABLE problem_profile_draft ADD CONSTRAINT ck_problem_profile_draft_quality_evidence_array "
        "CHECK (jsonb_typeof(quality_evidence) = 'array')"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE problem_profile_draft DROP CONSTRAINT IF EXISTS ck_problem_profile_draft_quality_evidence_array")
    op.execute("ALTER TABLE problem_profile_draft DROP CONSTRAINT IF EXISTS ck_problem_profile_draft_quality_notes_array")
    op.execute("ALTER TABLE problem_profile_draft DROP CONSTRAINT IF EXISTS ck_problem_profile_draft_quality_status")
    op.execute("ALTER TABLE problem_profile_draft DROP COLUMN IF EXISTS quality_evidence")
    op.execute("ALTER TABLE problem_profile_draft DROP COLUMN IF EXISTS quality_notes")
    op.execute("ALTER TABLE problem_profile_draft DROP COLUMN IF EXISTS quality_status")
