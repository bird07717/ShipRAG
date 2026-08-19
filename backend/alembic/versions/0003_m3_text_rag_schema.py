"""Create the M3 text RAG schema.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-17
"""

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    statements = [
        """
        INSERT INTO model_config (
            id, name, model_type, provider, base_url, model_name, parameters, enabled
        ) VALUES (
            '00000000-0000-0000-0000-000000000003',
            'default-llm',
            'LLM',
            'zhipu',
            'https://open.bigmodel.cn/api/paas/v4/',
            'glm-5.2',
            jsonb_build_object(
                'secret_source', 'environment',
                'temperature', 0.1,
                'max_tokens', 2048,
                'thinking', jsonb_build_object('type', 'enabled')
            ),
            true
        )
        """,
        """
        CREATE TABLE prompt_template (
            id uuid PRIMARY KEY,
            name varchar(120) NOT NULL,
            version integer NOT NULL CHECK (version > 0),
            content text NOT NULL CHECK (length(btrim(content)) > 0),
            active boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (name, version)
        )
        """,
        """
        CREATE UNIQUE INDEX uq_prompt_template_active
        ON prompt_template (active) WHERE active
        """,
        """
        INSERT INTO prompt_template (id, name, version, content, active)
        VALUES (
            '00000000-0000-0000-0000-000000000004',
            'default-rag',
            1,
            '你是企业产品知识助手。只能依据“知识库上下文”回答，不得把对话历史当作事实依据。\n'
            '如果上下文不足，请明确回答“知识库中没有足够信息”，不要猜测。\n'
            '引用事实时，只能使用上下文中已经分配的 [S1]、[S2] 等来源编号，禁止编造来源。\n\n'
            '对话历史：\n{{history}}\n\n'
            '知识库上下文：\n{{context}}\n\n'
            '用户问题：\n{{question}}\n\n'
            '请直接给出清晰、简洁的中文答案。',
            true
        )
        """,
        """
        CREATE TABLE conversation (
            id uuid PRIMARY KEY,
            knowledge_id uuid NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE INDEX ix_conversation_knowledge_updated
        ON conversation (knowledge_id, updated_at DESC)
        """,
        """
        CREATE TABLE message (
            id uuid PRIMARY KEY,
            conversation_id uuid NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
            sequence_no integer NOT NULL CHECK (sequence_no > 0),
            role varchar(20) NOT NULL CHECK (role IN ('USER','ASSISTANT')),
            content text NOT NULL DEFAULT '',
            sources jsonb NOT NULL DEFAULT '[]'::jsonb,
            status varchar(20) NOT NULL CHECK (
                status IN ('STREAMING','COMPLETED','FAILED','CANCELLED')
            ),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CHECK (role = 'ASSISTANT' OR status = 'COMPLETED')
        )
        """,
        """
        CREATE UNIQUE INDEX uq_message_conversation_sequence
        ON message (conversation_id, sequence_no)
        """,
        """
        CREATE INDEX ix_message_conversation_created
        ON message (conversation_id, sequence_no DESC)
        """,
        """
        CREATE TABLE rag_trace (
            id uuid PRIMARY KEY,
            trace_id uuid NOT NULL UNIQUE,
            request_id varchar(200) NOT NULL,
            mode varchar(20) NOT NULL DEFAULT 'CHAT' CHECK (mode IN ('CHAT','PLAYGROUND')),
            kb_id uuid NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
            index_id uuid NOT NULL REFERENCES knowledge_index(id) ON DELETE RESTRICT,
            conversation_id uuid REFERENCES conversation(id) ON DELETE SET NULL,
            message_id uuid REFERENCES message(id) ON DELETE SET NULL,
            question text NOT NULL,
            retrieval_result jsonb NOT NULL DEFAULT '{}'::jsonb,
            rerank_result jsonb NOT NULL DEFAULT '{}'::jsonb,
            selected_context jsonb NOT NULL DEFAULT '[]'::jsonb,
            prompt text,
            answer text,
            sources jsonb NOT NULL DEFAULT '[]'::jsonb,
            model_usage jsonb NOT NULL DEFAULT '{}'::jsonb,
            latency jsonb NOT NULL DEFAULT '{}'::jsonb,
            status varchar(20) NOT NULL CHECK (
                status IN ('RUNNING','COMPLETED','FAILED','CANCELLED')
            ),
            error jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz
        )
        """,
        """
        CREATE INDEX ix_rag_trace_kb_created
        ON rag_trace (kb_id, created_at DESC)
        """,
        """
        CREATE INDEX ix_rag_trace_conversation_created
        ON rag_trace (conversation_id, created_at DESC)
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rag_trace CASCADE")
    op.execute("DROP TABLE IF EXISTS message CASCADE")
    op.execute("DROP TABLE IF EXISTS conversation CASCADE")
    op.execute("DROP TABLE IF EXISTS prompt_template CASCADE")
    op.execute("DELETE FROM model_config WHERE id = '00000000-0000-0000-0000-000000000003'")
