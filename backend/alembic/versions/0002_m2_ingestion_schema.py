"""Create the M2 document ingestion schema.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-17
"""

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    statements = [
        """
        CREATE TABLE model_config (
            id uuid PRIMARY KEY,
            name varchar(120) NOT NULL UNIQUE,
            model_type varchar(20) NOT NULL CHECK (model_type IN ('LLM','EMBEDDING','RERANK','OCR','VISION')),
            provider varchar(40) NOT NULL,
            base_url text NOT NULL,
            api_key_ciphertext bytea,
            api_key_nonce bytea,
            api_key_key_version integer,
            model_name varchar(200) NOT NULL,
            parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
            enabled boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE UNIQUE INDEX uq_model_config_enabled_type
        ON model_config (model_type) WHERE enabled
        """,
        """
        INSERT INTO model_config (
            id, name, model_type, provider, base_url, model_name, parameters, enabled
        ) VALUES (
            '00000000-0000-0000-0000-000000000001',
            'default-embedding',
            'EMBEDDING',
            'siliconflow',
            'https://api.siliconflow.cn/v1/',
            'Qwen/Qwen3-VL-Embedding-8B',
            jsonb_build_object('dimensions', 1024, 'secret_source', 'environment'),
            true
        )
        """,
        """
        CREATE TABLE knowledge_base (
            id uuid PRIMARY KEY,
            name varchar(200) NOT NULL UNIQUE,
            description text,
            status varchar(20) NOT NULL DEFAULT 'ENABLED' CHECK (status IN ('ENABLED','DISABLED')),
            active_index_id uuid,
            rebuild_required boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE knowledge_index (
            id uuid PRIMARY KEY,
            kb_id uuid NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
            version integer NOT NULL CHECK (version > 0),
            status varchar(20) NOT NULL CHECK (status IN ('BUILDING','READY','ACTIVE','DEPRECATED','FAILED','DELETING')),
            embedding_model_id uuid NOT NULL REFERENCES model_config(id) ON DELETE RESTRICT,
            embedding_model_name varchar(200) NOT NULL,
            embedding_dimension integer NOT NULL CHECK (embedding_dimension = 1024),
            bm25_engine varchar(100) NOT NULL DEFAULT 'NOT_BUILT',
            document_count integer NOT NULL DEFAULT 0 CHECK (document_count >= 0),
            element_count integer NOT NULL DEFAULT 0 CHECK (element_count >= 0),
            chunk_count integer NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
            build_reason varchar(30) NOT NULL CHECK (build_reason IN ('INITIAL','DOCUMENT_CHANGED','REPROCESS','MODEL_CHANGED','MANUAL')),
            activate_on_success boolean NOT NULL DEFAULT true,
            error_code varchar(100),
            error_message text,
            created_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz,
            activated_at timestamptz,
            UNIQUE (kb_id, version)
        )
        """,
        """
        CREATE UNIQUE INDEX uq_knowledge_index_building
        ON knowledge_index (kb_id) WHERE status = 'BUILDING'
        """,
        """
        CREATE UNIQUE INDEX uq_knowledge_index_active
        ON knowledge_index (kb_id) WHERE status = 'ACTIVE'
        """,
        """
        ALTER TABLE knowledge_base
        ADD CONSTRAINT fk_knowledge_base_active_index
        FOREIGN KEY (active_index_id) REFERENCES knowledge_index(id) ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED
        """,
        """
        CREATE TABLE document_source (
            id uuid PRIMARY KEY,
            kb_id uuid NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
            filename varchar(500) NOT NULL,
            display_name varchar(500) NOT NULL,
            minio_bucket varchar(100) NOT NULL,
            minio_object_key text NOT NULL UNIQUE,
            file_hash char(64) NOT NULL CHECK (file_hash ~ '^[0-9a-f]{64}$'),
            file_size bigint NOT NULL CHECK (file_size >= 0),
            mime_type varchar(150) NOT NULL,
            status varchar(20) NOT NULL DEFAULT 'STORED' CHECK (status IN ('STORED','DELETED')),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            deleted_at timestamptz
        )
        """,
        """
        CREATE UNIQUE INDEX uq_document_source_live_hash
        ON document_source (kb_id, file_hash) WHERE status = 'STORED'
        """,
        """
        CREATE INDEX ix_document_source_kb_created
        ON document_source (kb_id, created_at DESC)
        """,
        """
        CREATE TABLE index_document (
            id uuid PRIMARY KEY,
            index_id uuid NOT NULL REFERENCES knowledge_index(id) ON DELETE CASCADE,
            document_id uuid NOT NULL REFERENCES document_source(id) ON DELETE RESTRICT,
            source_hash char(64) NOT NULL,
            status varchar(30) NOT NULL CHECK (status IN ('QUEUED','PARSING','PROCESSING_IMAGES','CHUNKING','EMBEDDING','READY','FAILED')),
            page_count integer CHECK (page_count IS NULL OR page_count > 0),
            error_code varchar(100),
            error_message text,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            started_at timestamptz,
            finished_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (index_id, document_id)
        )
        """,
        """
        CREATE INDEX ix_index_document_document ON index_document (document_id, created_at DESC)
        """,
        """
        CREATE TABLE document_element (
            id uuid PRIMARY KEY,
            index_id uuid NOT NULL REFERENCES knowledge_index(id) ON DELETE CASCADE,
            index_document_id uuid NOT NULL REFERENCES index_document(id) ON DELETE CASCADE,
            document_id uuid NOT NULL REFERENCES document_source(id) ON DELETE RESTRICT,
            element_type varchar(20) NOT NULL CHECK (element_type IN ('TEXT','TABLE','IMAGE')),
            sequence_no integer NOT NULL CHECK (sequence_no > 0),
            content text NOT NULL DEFAULT '',
            section_path jsonb NOT NULL DEFAULT '[]'::jsonb,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (index_document_id, sequence_no)
        )
        """,
        """
        CREATE INDEX ix_document_element_lookup
        ON document_element (document_id, index_id, sequence_no)
        """,
        """
        CREATE TABLE image_asset (
            id uuid PRIMARY KEY,
            index_id uuid NOT NULL REFERENCES knowledge_index(id) ON DELETE CASCADE,
            index_document_id uuid NOT NULL REFERENCES index_document(id) ON DELETE CASCADE,
            document_id uuid NOT NULL REFERENCES document_source(id) ON DELETE RESTRICT,
            element_id uuid NOT NULL UNIQUE REFERENCES document_element(id) ON DELETE CASCADE,
            minio_bucket varchar(100) NOT NULL,
            minio_object_key text NOT NULL UNIQUE,
            file_hash char(64) NOT NULL,
            mime_type varchar(150) NOT NULL,
            width integer CHECK (width IS NULL OR width > 0),
            height integer CHECK (height IS NULL OR height > 0),
            ocr_text text,
            vision_caption text,
            ocr_status varchar(20) NOT NULL CHECK (ocr_status IN ('PENDING','READY','FAILED','SKIPPED')),
            vision_status varchar(20) NOT NULL CHECK (vision_status IN ('PENDING','READY','FAILED','SKIPPED')),
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE document_chunk (
            id uuid PRIMARY KEY,
            kb_id uuid NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
            index_id uuid NOT NULL REFERENCES knowledge_index(id) ON DELETE CASCADE,
            index_document_id uuid NOT NULL REFERENCES index_document(id) ON DELETE CASCADE,
            document_id uuid NOT NULL REFERENCES document_source(id) ON DELETE RESTRICT,
            chunk_type varchar(20) NOT NULL CHECK (chunk_type IN ('TEXT','TABLE','IMAGE','MIXED')),
            sequence_no integer NOT NULL CHECK (sequence_no > 0),
            content text NOT NULL,
            search_text text NOT NULL,
            token_count integer NOT NULL CHECK (token_count > 0),
            embedding vector(1024) NOT NULL,
            section_path jsonb NOT NULL DEFAULT '[]'::jsonb,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (index_document_id, sequence_no)
        )
        """,
        """
        CREATE INDEX ix_document_chunk_kb_index ON document_chunk (kb_id, index_id)
        """,
        """
        CREATE INDEX ix_document_chunk_document ON document_chunk (index_id, document_id)
        """,
        """
        CREATE INDEX ix_document_chunk_embedding_hnsw
        ON document_chunk USING hnsw (embedding vector_cosine_ops)
        """,
        """
        CREATE TABLE chunk_element (
            chunk_id uuid NOT NULL REFERENCES document_chunk(id) ON DELETE CASCADE,
            element_id uuid NOT NULL REFERENCES document_element(id) ON DELETE CASCADE,
            ordinal integer NOT NULL CHECK (ordinal > 0),
            PRIMARY KEY (chunk_id, element_id),
            UNIQUE (chunk_id, ordinal)
        )
        """,
        """
        CREATE TABLE task_record (
            id uuid PRIMARY KEY,
            task_type varchar(50) NOT NULL,
            status varchar(20) NOT NULL CHECK (status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')),
            stage varchar(50) NOT NULL,
            progress integer NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
            attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
            kb_id uuid REFERENCES knowledge_base(id) ON DELETE CASCADE,
            index_id uuid REFERENCES knowledge_index(id) ON DELETE CASCADE,
            document_id uuid REFERENCES document_source(id) ON DELETE CASCADE,
            error_code varchar(100),
            error_message text,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            started_at timestamptz,
            finished_at timestamptz,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE INDEX ix_task_record_index ON task_record (index_id, created_at DESC)
        """,
        """
        CREATE TABLE idempotency_record (
            id uuid PRIMARY KEY,
            kb_id uuid NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
            operation varchar(80) NOT NULL,
            idempotency_key varchar(200) NOT NULL,
            request_hash char(64) NOT NULL,
            response jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (operation, idempotency_key)
        )
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    for table in (
        "idempotency_record",
        "task_record",
        "chunk_element",
        "document_chunk",
        "image_asset",
        "document_element",
        "index_document",
        "document_source",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute(
        "ALTER TABLE knowledge_base DROP CONSTRAINT IF EXISTS fk_knowledge_base_active_index"
    )
    op.execute("DROP TABLE IF EXISTS knowledge_index CASCADE")
    op.execute("DROP TABLE IF EXISTS knowledge_base CASCADE")
    op.execute("DROP TABLE IF EXISTS model_config CASCADE")
