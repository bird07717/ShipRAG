"""Add the technical-support domain schema.

This migration is intentionally additive: legacy RAG conversations, chunks and
traces stay readable while product chat moves to the support workflow.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-24
"""

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE document_problem_profile (
            id uuid PRIMARY KEY,
            document_id uuid NOT NULL REFERENCES document_source(id) ON DELETE CASCADE,
            kb_id uuid NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
            problem_code varchar(100) NOT NULL,
            canonical_question varchar(500) NOT NULL,
            aliases jsonb NOT NULL DEFAULT '[]'::jsonb,
            keywords jsonb NOT NULL DEFAULT '[]'::jsonb,
            product_name varchar(200),
            model_names jsonb NOT NULL DEFAULT '[]'::jsonb,
            task_type varchar(50),
            enabled boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (kb_id, problem_code),
            CHECK (jsonb_typeof(aliases) = 'array'),
            CHECK (jsonb_typeof(keywords) = 'array'),
            CHECK (jsonb_typeof(model_names) = 'array')
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_document_problem_profile_kb_enabled "
        "ON document_problem_profile (kb_id, enabled)"
    )
    op.execute(
        "CREATE INDEX ix_document_problem_profile_document "
        "ON document_problem_profile (document_id)"
    )
    # JSONB has no useful declarative constraint for 'array of strings' or
    # normalized alias uniqueness, so keep the invariant at the database
    # boundary rather than trusting only the admin API.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION validate_document_problem_profile()
        RETURNS trigger AS $$
        DECLARE
            item jsonb;
            candidate text;
            conflict boolean;
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM document_source d
                WHERE d.id = NEW.document_id AND d.kb_id = NEW.kb_id AND d.status <> 'DELETED'
            ) THEN
                RAISE EXCEPTION 'problem profile document must be a live document in the same knowledge base';
            END IF;
            FOREACH item IN ARRAY ARRAY[
                NEW.aliases, NEW.keywords, NEW.model_names
            ] LOOP
                IF EXISTS (SELECT 1 FROM jsonb_array_elements(item) v WHERE jsonb_typeof(v) <> 'string') THEN
                    RAISE EXCEPTION 'profile aliases, keywords and model_names must contain strings only';
                END IF;
            END LOOP;
            IF NEW.enabled THEN
                FOR candidate IN
                    SELECT lower(regexp_replace(value, '[\\s[:punct:]]+', '', 'g'))
                    FROM jsonb_array_elements_text(NEW.aliases) AS a(value)
                    WHERE value <> ''
                LOOP
                    SELECT EXISTS (
                        SELECT 1
                        FROM document_problem_profile p
                        CROSS JOIN LATERAL jsonb_array_elements_text(p.aliases) AS existing(value)
                        WHERE p.kb_id = NEW.kb_id AND p.enabled AND p.id <> NEW.id
                          AND lower(regexp_replace(existing.value, '[\\s[:punct:]]+', '', 'g')) = candidate
                    ) INTO conflict;
                    IF conflict THEN
                        RAISE EXCEPTION 'enabled aliases must be unique within a knowledge base';
                    END IF;
                END LOOP;
            END IF;
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_validate_document_problem_profile
        BEFORE INSERT OR UPDATE ON document_problem_profile
        FOR EACH ROW EXECUTE FUNCTION validate_document_problem_profile()
        """
    )
    op.execute(
        """
        ALTER TABLE message
            ADD COLUMN content_blocks jsonb NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN response_type varchar(40),
            ADD COLUMN support_mode varchar(50)
        """
    )
    op.execute("ALTER TABLE rag_trace DROP CONSTRAINT IF EXISTS ck_rag_trace_response_type")
    op.execute(
        """
        ALTER TABLE rag_trace ADD CONSTRAINT ck_rag_trace_response_type
        CHECK (response_type IS NULL OR response_type IN (
            'ANSWERED', 'UNCONFIRMED', 'OUT_OF_SCOPE', 'DOC_DELIVERED',
            'DOCUMENT_DELIVERED', 'CLARIFICATION_REQUIRED', 'NO_MATCH',
            'ESCALATION_REQUIRED'
        ))
        """
    )
    op.execute(
        """
        CREATE TABLE support_idempotency (
            id uuid PRIMARY KEY,
            kb_id uuid NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
            idempotency_key varchar(200) NOT NULL,
            request_hash char(64) NOT NULL,
            conversation_id uuid REFERENCES conversation(id) ON DELETE SET NULL,
            trace_id uuid,
            response jsonb,
            status varchar(20) NOT NULL CHECK (status IN ('RUNNING','COMPLETED','FAILED')),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (kb_id, idempotency_key)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS support_idempotency")
    op.execute("ALTER TABLE rag_trace DROP CONSTRAINT IF EXISTS ck_rag_trace_response_type")
    op.execute(
        "ALTER TABLE rag_trace ADD CONSTRAINT ck_rag_trace_response_type "
        "CHECK (response_type IS NULL OR response_type IN "
        "('ANSWERED', 'UNCONFIRMED', 'OUT_OF_SCOPE', 'DOC_DELIVERED'))"
    )
    op.execute(
        "ALTER TABLE message DROP COLUMN IF EXISTS support_mode, "
        "DROP COLUMN IF EXISTS response_type, DROP COLUMN IF EXISTS content_blocks"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_validate_document_problem_profile ON document_problem_profile"
    )
    op.execute("DROP FUNCTION IF EXISTS validate_document_problem_profile()")
    op.execute("DROP TABLE IF EXISTS document_problem_profile")
