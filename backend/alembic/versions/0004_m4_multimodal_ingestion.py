"""Add M4 OCR and Vision model snapshots to image assets.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-17
"""

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    statements = [
        """
        INSERT INTO model_config (
            id, name, model_type, provider, base_url, model_name, parameters, enabled
        ) VALUES (
            '00000000-0000-0000-0000-000000000005',
            'default-ocr', 'OCR', 'siliconflow',
            'https://api.siliconflow.cn/v1/', 'deepseek-ai/DeepSeek-OCR',
            jsonb_build_object('secret_source', 'environment', 'temperature', 0,
                               'max_tokens', 4096), true
        )
        """,
        """
        INSERT INTO model_config (
            id, name, model_type, provider, base_url, model_name, parameters, enabled
        ) VALUES (
            '00000000-0000-0000-0000-000000000006',
            'default-vision', 'VISION', 'zhipu',
            'https://open.bigmodel.cn/api/paas/v4/', 'glm-5v-turbo',
            jsonb_build_object('secret_source', 'environment', 'temperature', 0,
                               'max_tokens', 1024,
                               'thinking', jsonb_build_object('type', 'disabled')), true
        )
        """,
        """
        ALTER TABLE image_asset
          ADD COLUMN ocr_provider varchar(40),
          ADD COLUMN ocr_model_name varchar(200),
          ADD COLUMN ocr_error_code varchar(100),
          ADD COLUMN vision_provider varchar(40),
          ADD COLUMN vision_model_name varchar(200),
          ADD COLUMN vision_error_code varchar(100),
          ADD COLUMN processed_at timestamptz
        """,
        """
        CREATE INDEX ix_image_asset_document_index
        ON image_asset (document_id, index_id)
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_image_asset_document_index")
    op.execute(
        """
        ALTER TABLE image_asset
          DROP COLUMN IF EXISTS processed_at,
          DROP COLUMN IF EXISTS vision_error_code,
          DROP COLUMN IF EXISTS vision_model_name,
          DROP COLUMN IF EXISTS vision_provider,
          DROP COLUMN IF EXISTS ocr_error_code,
          DROP COLUMN IF EXISTS ocr_model_name,
          DROP COLUMN IF EXISTS ocr_provider
        """
    )
    op.execute(
        """
        DELETE FROM model_config
        WHERE id IN ('00000000-0000-0000-0000-000000000005',
                     '00000000-0000-0000-0000-000000000006')
        """
    )
