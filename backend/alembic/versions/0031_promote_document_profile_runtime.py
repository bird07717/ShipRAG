"""Promote document attributes as the only runtime Profile projection."""

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # 0029 copied legacy rows once.  This pass fills any document that gained
    # a legacy Profile later, or whose new row only contains a pending value.
    # Existing online document attributes always win and are never overwritten.
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
                   'aliases', p.aliases, 'keywords', p.keywords,
                   'product_name', p.product_name, 'model_names', p.model_names,
                   'task_type', p.task_type, 'routing_summary', p.routing_summary,
                   'symptoms', p.symptoms, 'exclusions', p.exclusions,
                   'enabled', p.enabled
               ))),
               1, 'MIGRATED', 1, p.updated_at, p.enabled
        FROM document_problem_profile p
        LEFT JOIN document_profile dp ON dp.document_id = p.document_id
        WHERE dp.document_id IS NULL OR dp.active_payload IS NULL
        ON CONFLICT (document_id) DO UPDATE SET
            active_payload = EXCLUDED.active_payload,
            active_version = GREATEST(document_profile.active_version, 1),
            active_source = 'MIGRATED', active_confidence = 1,
            active_updated_at = EXCLUDED.active_updated_at, enabled = EXCLUDED.enabled,
            updated_at = now()
        WHERE document_profile.active_payload IS NULL
        """
    )
    op.execute(
        """
        INSERT INTO document_profile_match_term
            (document_id, kb_id, intent_key, term_type, normalized_term, weight)
        SELECT dp.document_id, dp.kb_id, intent.value ->> 'problem_code', term_type,
               lower(trim(term)), weight
        FROM document_profile dp
        CROSS JOIN LATERAL jsonb_array_elements(
            COALESCE(dp.active_payload -> 'intents', '[]'::jsonb)
        ) intent(value)
        CROSS JOIN LATERAL (
            SELECT 'CANONICAL_QUESTION'::varchar AS term_type,
                   intent.value ->> 'canonical_question' AS term, 1.0::double precision AS weight
            UNION ALL SELECT 'PROBLEM_CODE', intent.value ->> 'problem_code', 1.0
            UNION ALL SELECT 'ALIAS', alias.value, 0.95
              FROM jsonb_array_elements_text(COALESCE(intent.value -> 'aliases', '[]'::jsonb)) alias(value)
            UNION ALL SELECT 'KEYWORD', keyword.value, 0.6
              FROM jsonb_array_elements_text(COALESCE(intent.value -> 'keywords', '[]'::jsonb)) keyword(value)
        ) terms
        WHERE dp.active_payload IS NOT NULL AND dp.enabled
          AND trim(COALESCE(term, '')) <> ''
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    # The old projection remains untouched as rollback/audit data.  Attribute
    # rows may include new user edits, so a downgrade must not delete them.
    pass
