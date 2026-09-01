"""Add staged knowledge releases and editable problem-profile drafts.

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-25
"""

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE knowledge_release (
            id uuid PRIMARY KEY,
            kb_id uuid NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
            index_id uuid NOT NULL UNIQUE REFERENCES knowledge_index(id) ON DELETE CASCADE,
            status varchar(30) NOT NULL DEFAULT 'DRAFT',
            validation_result jsonb NOT NULL DEFAULT '{}'::jsonb,
            generation_error text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            published_at timestamptz,
            CHECK (status IN (
                'DRAFT', 'VALIDATING', 'READY_TO_PUBLISH',
                'VALIDATION_FAILED', 'PUBLISHED', 'CANCELLED'
            )),
            CHECK (jsonb_typeof(validation_result) = 'object')
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_knowledge_release_kb_created ON knowledge_release (kb_id, created_at DESC)"
    )
    op.execute(
        """
        CREATE TABLE problem_profile_draft (
            id uuid PRIMARY KEY,
            release_id uuid NOT NULL REFERENCES knowledge_release(id) ON DELETE CASCADE,
            kb_id uuid NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
            document_id uuid NOT NULL REFERENCES document_source(id) ON DELETE CASCADE,
            operation varchar(20) NOT NULL DEFAULT 'CREATE',
            target_profile_id uuid REFERENCES document_problem_profile(id) ON DELETE SET NULL,
            problem_code varchar(100) NOT NULL,
            canonical_question varchar(500) NOT NULL,
            aliases jsonb NOT NULL DEFAULT '[]'::jsonb,
            keywords jsonb NOT NULL DEFAULT '[]'::jsonb,
            product_name varchar(200),
            model_names jsonb NOT NULL DEFAULT '[]'::jsonb,
            task_type varchar(50),
            enabled boolean NOT NULL DEFAULT true,
            status varchar(20) NOT NULL DEFAULT 'DRAFT',
            generation_source varchar(20) NOT NULL DEFAULT 'FALLBACK',
            confidence double precision NOT NULL DEFAULT 0,
            reasoning text,
            validation_result jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (release_id, document_id),
            CHECK (operation IN ('CREATE', 'UPDATE', 'NONE')),
            CHECK (status IN ('DRAFT', 'APPROVED', 'REJECTED', 'PUBLISHED')),
            CHECK (generation_source IN ('LLM', 'FALLBACK', 'MANUAL')),
            CHECK (confidence >= 0 AND confidence <= 1),
            CHECK (jsonb_typeof(aliases) = 'array'),
            CHECK (jsonb_typeof(keywords) = 'array'),
            CHECK (jsonb_typeof(model_names) = 'array'),
            CHECK (jsonb_typeof(validation_result) = 'object')
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_problem_profile_draft_release_status "
        "ON problem_profile_draft (release_id, status)"
    )
    op.execute(
        "CREATE INDEX ix_problem_profile_draft_kb_document "
        "ON problem_profile_draft (kb_id, document_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS problem_profile_draft")
    op.execute("DROP TABLE IF EXISTS knowledge_release")
