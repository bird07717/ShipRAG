"""Store Support Profiles as document attributes with staged publication.

Revision ID: 0029
Revises: 0028
Create Date: 2026-08-26
"""

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ``document_problem_profile`` remains read-only compatibility/audit data
    # during the migration window.  The new table deliberately has one row per
    # document so that staged values cannot accidentally participate in routing.
    op.execute(
        """
        CREATE TABLE document_profile (
            document_id uuid PRIMARY KEY REFERENCES document_source(id) ON DELETE CASCADE,
            kb_id uuid NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
            active_payload jsonb,
            active_version integer NOT NULL DEFAULT 0,
            active_source varchar(20),
            active_confidence double precision,
            active_updated_at timestamptz,
            enabled boolean NOT NULL DEFAULT true,
            pending_payload jsonb,
            pending_source varchar(20),
            pending_confidence double precision,
            pending_state varchar(30),
            pending_error_code varchar(100),
            pending_error_message text,
            pending_updated_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CHECK (active_payload IS NULL OR jsonb_typeof(active_payload) = 'object'),
            CHECK (pending_payload IS NULL OR jsonb_typeof(pending_payload) = 'object'),
            CHECK (active_source IS NULL OR active_source IN ('AUTO', 'MANUAL', 'MIGRATED')),
            CHECK (pending_source IS NULL OR pending_source IN ('AUTO', 'MANUAL', 'MIGRATED')),
            CHECK (pending_state IS NULL OR pending_state IN ('PENDING_ONLINE', 'NEEDS_ATTENTION', 'FAILED')),
            CHECK (active_confidence IS NULL OR active_confidence >= 0 AND active_confidence <= 1),
            CHECK (pending_confidence IS NULL OR pending_confidence >= 0 AND pending_confidence <= 1)
        )
        """
    )
    op.execute("CREATE INDEX ix_document_profile_kb ON document_profile (kb_id)")
    op.execute(
        "CREATE INDEX ix_document_profile_pending ON document_profile (kb_id, pending_state) "
        "WHERE pending_state IS NOT NULL"
    )
    op.execute(
        """
        CREATE TABLE document_profile_match_term (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id uuid NOT NULL REFERENCES document_profile(document_id) ON DELETE CASCADE,
            kb_id uuid NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
            intent_key varchar(160) NOT NULL,
            term_type varchar(30) NOT NULL,
            normalized_term varchar(500) NOT NULL,
            weight double precision NOT NULL DEFAULT 1,
            UNIQUE (document_id, intent_key, term_type, normalized_term),
            CHECK (term_type IN ('CANONICAL_QUESTION', 'ALIAS', 'KEYWORD', 'PROBLEM_CODE'))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_document_profile_match_term_lookup "
        "ON document_profile_match_term (kb_id, normalized_term)"
    )
    op.execute(
        """
        CREATE TABLE profile_publish_event (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            kb_id uuid NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
            document_count integer NOT NULL,
            validation_result jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CHECK (jsonb_typeof(validation_result) = 'object')
        )
        """
    )

    # Backfill the current online projection.  Each legacy row maps to one
    # intent; the new payload format also supports multiple intents later.
    op.execute(
        """
        INSERT INTO document_profile (
            document_id, kb_id, active_payload, active_version, active_source,
            active_confidence, active_updated_at, enabled
        )
        SELECT p.document_id, p.kb_id,
               jsonb_build_object('intents', jsonb_build_array(jsonb_build_object(
                   'problem_code', p.problem_code,
                   'canonical_question', p.canonical_question,
                   'aliases', p.aliases,
                   'keywords', p.keywords,
                   'product_name', p.product_name,
                   'model_names', p.model_names,
                   'task_type', p.task_type,
                   'routing_summary', p.routing_summary,
                   'symptoms', p.symptoms,
                   'exclusions', p.exclusions
               ))),
               1, 'MIGRATED', 1, p.updated_at, p.enabled
        FROM document_problem_profile p
        ON CONFLICT (document_id) DO NOTHING
        """
    )
    # Only the newest open legacy draft is eligible as a staged value.  Older
    # published/cancelled releases remain immutable history and are never
    # resurrected as pending edits.
    op.execute(
        """
        WITH ranked AS (
            SELECT d.*, row_number() OVER (
                PARTITION BY d.document_id ORDER BY d.updated_at DESC, d.created_at DESC
            ) AS position
            FROM problem_profile_draft d
            JOIN knowledge_release r ON r.id = d.release_id
            WHERE r.status NOT IN ('PUBLISHED', 'CANCELLED')
              AND d.status <> 'REJECTED'
        )
        INSERT INTO document_profile (
            document_id, kb_id, pending_payload, pending_source,
            pending_confidence, pending_state, pending_updated_at
        )
        SELECT document_id, kb_id,
               jsonb_build_object('intents', jsonb_build_array(jsonb_build_object(
                   'problem_code', problem_code,
                   'canonical_question', canonical_question,
                   'aliases', aliases, 'keywords', keywords,
                   'product_name', product_name, 'model_names', model_names,
                   'task_type', task_type, 'routing_summary', routing_summary,
                   'symptoms', symptoms, 'exclusions', exclusions,
                   'enabled', enabled
               ))),
               CASE WHEN generation_source = 'MANUAL' THEN 'MANUAL' ELSE 'MIGRATED' END,
               confidence,
               CASE WHEN quality_status IN ('FALLBACK', 'REVIEW_REQUIRED')
                    THEN 'NEEDS_ATTENTION' ELSE 'PENDING_ONLINE' END,
               updated_at
        FROM ranked WHERE position = 1
        ON CONFLICT (document_id) DO UPDATE SET
            pending_payload = EXCLUDED.pending_payload,
            pending_source = EXCLUDED.pending_source,
            pending_confidence = EXCLUDED.pending_confidence,
            pending_state = EXCLUDED.pending_state,
            pending_updated_at = EXCLUDED.pending_updated_at,
            updated_at = now()
        """
    )
    # Seed the online matcher from the active payload.  The application
    # rebuilds these rows transactionally on every later Profile publication.
    op.execute(
        """
        INSERT INTO document_profile_match_term (document_id, kb_id, intent_key, term_type, normalized_term, weight)
        SELECT dp.document_id, dp.kb_id, intent.value ->> 'problem_code', term_type, lower(trim(term)), weight
        FROM document_profile dp
        CROSS JOIN LATERAL jsonb_array_elements(COALESCE(dp.active_payload -> 'intents', '[]'::jsonb)) intent(value)
        CROSS JOIN LATERAL (
            SELECT 'CANONICAL_QUESTION'::varchar AS term_type,
                   intent.value ->> 'canonical_question' AS term, 1.0::double precision AS weight
            UNION ALL SELECT 'PROBLEM_CODE', intent.value ->> 'problem_code', 1.0
            UNION ALL SELECT 'ALIAS', alias.value, 0.95
              FROM jsonb_array_elements_text(COALESCE(intent.value -> 'aliases', '[]'::jsonb)) alias(value)
            UNION ALL SELECT 'KEYWORD', keyword.value, 0.6
              FROM jsonb_array_elements_text(COALESCE(intent.value -> 'keywords', '[]'::jsonb)) keyword(value)
        ) terms
        WHERE dp.active_payload IS NOT NULL AND dp.enabled AND trim(COALESCE(term, '')) <> ''
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS profile_publish_event")
    op.execute("DROP TABLE IF EXISTS document_profile_match_term")
    op.execute("DROP TABLE IF EXISTS document_profile")
