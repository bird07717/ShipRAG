"""Remove obsolete default-rag v1 and re-sync v2 from code.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-21

The runtime reads the active prompt_template row every turn (chunk-RAG
fallback path), so the DB row had drifted ahead of the code constant.
DEFAULT_RAG_PROMPT has been aligned to the DB content; this migration
drops the unused v1 row and idempotently re-seeds v2 so fresh
environments converge on the same prompt.
"""

from alembic import op
from app.rag.prompt import DEFAULT_RAG_PROMPT

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | None = None
depends_on: str | None = None


_PROMPT_V1_ID = "00000000-0000-0000-0000-000000000004"
_PROMPT_V2_ID = "00000000-0000-0000-0000-000000000007"

_PROMPT_V1_CONTENT = (
    "你是企业产品知识助手。只能依据“知识库上下文”回答，不得把对话历史当作事实依据。\n"
    "如果上下文不足，请明确回答“知识库中没有足够信息”，不要猜测。\n"
    "引用事实时，只能使用上下文中已经分配的 [S1]、[S2] 等来源编号，禁止编造来源。\n\n"
    "对话历史：\n{{history}}\n\n"
    "知识库上下文：\n{{context}}\n\n"
    "用户问题：\n{{question}}\n\n"
    "请直接给出清晰、简洁的中文答案。"
)


def upgrade() -> None:
    escaped_prompt = DEFAULT_RAG_PROMPT.replace("'", "''")
    op.execute(
        "DELETE FROM prompt_template"
        f" WHERE id = '{_PROMPT_V1_ID}' OR (name = 'default-rag' AND version = 1)"
    )
    op.execute(
        f"""
        INSERT INTO prompt_template (id, name, version, content, active)
        VALUES ('{_PROMPT_V2_ID}', 'default-rag', 2, '{escaped_prompt}', true)
        ON CONFLICT (name, version) DO UPDATE
        SET content = EXCLUDED.content, active = true, updated_at = now()
        """
    )


def downgrade() -> None:
    escaped_prompt = _PROMPT_V1_CONTENT.replace("'", "''")
    op.execute("DELETE FROM prompt_template WHERE name = 'default-rag' AND version = 2")
    op.execute(
        f"""
        INSERT INTO prompt_template (id, name, version, content, active)
        VALUES ('{_PROMPT_V1_ID}', 'default-rag', 1, '{escaped_prompt}', true)
        ON CONFLICT (name, version) DO UPDATE
        SET content = EXCLUDED.content, active = true, updated_at = now()
        """
    )
