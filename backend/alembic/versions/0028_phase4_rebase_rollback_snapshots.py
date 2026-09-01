"""Phase 4: REMOVE drafts and Support release profile snapshots.

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-26
"""

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Drafts may now suggest removing a live Profile whose document left the
    # candidate index (document deletion / replacement).
    op.execute(
        """
        DO $$
        DECLARE dropped text;
        BEGIN
            SELECT conname INTO dropped
            FROM pg_constraint
            WHERE conrelid = 'problem_profile_draft'::regclass
              AND contype = 'c'
              AND pg_get_constraintdef(oid) ILIKE '%operation%IN%';
            IF dropped IS NOT NULL THEN
                EXECUTE format(
                    'ALTER TABLE problem_profile_draft DROP CONSTRAINT %I', dropped
                );
            END IF;
        END $$;
        """
    )
    op.execute(
        "ALTER TABLE problem_profile_draft ADD CONSTRAINT "
        "problem_profile_draft_operation_check "
        "CHECK (operation IN ('CREATE', 'UPDATE', 'REMOVE', 'NONE'))"
    )

    # Snapshot of the live Profile state at publish time.  Support release
    # rollback restores from this table; releases published before this
    # migration have no snapshot and therefore cannot be rolled back to.
    op.execute(
        """
        CREATE TABLE support_release_profile (
            id uuid PRIMARY KEY,
            release_id uuid NOT NULL REFERENCES knowledge_release(id) ON DELETE CASCADE,
            document_id uuid NOT NULL REFERENCES document_source(id) ON DELETE CASCADE,
            problem_code varchar(100) NOT NULL,
            canonical_question varchar(500) NOT NULL,
            aliases jsonb NOT NULL DEFAULT '[]'::jsonb,
            keywords jsonb NOT NULL DEFAULT '[]'::jsonb,
            product_name varchar(200),
            model_names jsonb NOT NULL DEFAULT '[]'::jsonb,
            task_type varchar(50),
            routing_summary text NOT NULL DEFAULT '',
            symptoms jsonb NOT NULL DEFAULT '[]'::jsonb,
            exclusions jsonb NOT NULL DEFAULT '[]'::jsonb,
            profile_enrichment_version int NOT NULL DEFAULT 1,
            enabled boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (release_id, document_id),
            CHECK (jsonb_typeof(aliases) = 'array'),
            CHECK (jsonb_typeof(keywords) = 'array'),
            CHECK (jsonb_typeof(model_names) = 'array'),
            CHECK (jsonb_typeof(symptoms) = 'array'),
            CHECK (jsonb_typeof(exclusions) = 'array')
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_support_release_profile_release ON support_release_profile (release_id)"
    )
    op.execute(
        "CREATE INDEX ix_support_release_profile_document ON support_release_profile (document_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS support_release_profile")
    op.execute(
        """
        DO $$
        DECLARE dropped text;
        BEGIN
            SELECT conname INTO dropped
            FROM pg_constraint
            WHERE conrelid = 'problem_profile_draft'::regclass
              AND contype = 'c'
              AND pg_get_constraintdef(oid) ILIKE '%operation%IN%';
            IF dropped IS NOT NULL THEN
                EXECUTE format(
                    'ALTER TABLE problem_profile_draft DROP CONSTRAINT %I', dropped
                );
            END IF;
        END $$;
        """
    )
    op.execute(
        "ALTER TABLE problem_profile_draft ADD CONSTRAINT "
        "problem_profile_draft_operation_check "
        "CHECK (operation IN ('CREATE', 'UPDATE', 'NONE'))"
    )
