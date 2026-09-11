"""Keep administrator answer reviews and isolated retest snapshots.

Revision ID: 0044
Revises: 0043
"""

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE assistant_request ADD COLUMN input_payload jsonb NOT NULL DEFAULT '{}'::jsonb"
    )
    op.execute("""
        CREATE TABLE answer_review (
            id uuid PRIMARY KEY,
            knowledge_id uuid NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
            request_id uuid NOT NULL UNIQUE,
            snapshot jsonb NOT NULL,
            category varchar(30) NOT NULL CHECK (category IN
                ('WRONG_DOCUMENT','MISSING_KNOWLEDGE','INCOMPLETE_CONTENT','WRONG_ANSWER',
                 'OTHER','ACCEPTABLE')),
            status varchar(20) NOT NULL CHECK (status IN ('OPEN','VERIFY','CLOSED')),
            expected_document_id uuid,
            expected_document_title text,
            expectation text NOT NULL DEFAULT '',
            notes text NOT NULL DEFAULT '',
            revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    # Deliberately no FK to request/message/trace/doc: reviews outlive log retention
    # and retain the original identities even if a document is removed.
    op.execute("CREATE INDEX ix_answer_review_kb ON answer_review (knowledge_id, updated_at DESC)")
    op.execute("""
        CREATE TABLE answer_retest (
            id uuid PRIMARY KEY,
            review_id uuid NOT NULL REFERENCES answer_review(id) ON DELETE CASCADE,
            review_revision integer NOT NULL,
            expectation jsonb NOT NULL,
            status varchar(20) NOT NULL CHECK (status IN ('RUNNING','COMPLETED','FAILED')),
            turns jsonb NOT NULL DEFAULT '[]'::jsonb,
            error text,
            created_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz
        )
    """)
    op.execute("CREATE INDEX ix_answer_retest_review ON answer_retest (review_id, created_at DESC)")
    op.execute(
        "CREATE UNIQUE INDEX uq_answer_retest_running ON answer_retest (review_id) "
        "WHERE status = 'RUNNING'"
    )
    op.execute(
        "CREATE INDEX ix_assistant_request_kb_created ON assistant_request "
        "(knowledge_id, created_at DESC, id DESC)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE assistant_request DROP COLUMN input_payload")
    op.execute("DROP INDEX ix_assistant_request_kb_created")
    op.execute("DROP TABLE answer_retest")
    op.execute("DROP TABLE answer_review")
