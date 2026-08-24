"""Re-encrypt model API keys after MODEL_SECRET_KEY rotation.

Keys are read from the environment only (never serialized through Redis):

    MODEL_SECRET_KEY=<new 32-byte base64>            # current version
    MODEL_SECRET_KEY_VERSION=2
    MODEL_SECRET_KEY_PREVIOUS=<old 32-byte base64>   # version 1
    MODEL_SECRET_KEY_PREVIOUS_VERSION=1

Run directly:

    ../.venv/bin/python -m app.tasks.key_rotation

The task is idempotent: rows already on the current version are skipped.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.core.resources import AppResources
from app.core.secrets import ModelSecretBox


async def _rotate(settings: Settings) -> dict[str, Any]:
    if settings.model_secret_key is None or settings.model_secret_key_previous is None:
        raise SystemExit("需要同时配置 MODEL_SECRET_KEY 与 MODEL_SECRET_KEY_PREVIOUS 才能执行轮换")
    box = ModelSecretBox(
        settings.model_secret_key,
        version=settings.model_secret_key_version,
        previous_key=settings.model_secret_key_previous,
        previous_version=settings.model_secret_key_previous_version,
    )
    resources = AppResources.create(settings)
    try:
        async with resources.database.begin() as connection:
            rows_result = await connection.execute(
                text(
                    "SELECT id, api_key_ciphertext, api_key_nonce, api_key_key_version"
                    " FROM model_config WHERE api_key_ciphertext IS NOT NULL"
                    " AND api_key_key_version = :previous_version"
                ),
                {"previous_version": box.version - 1},
            )
            rows = rows_result.mappings().all()
            rotated = 0
            for row in rows:
                secret = box.decrypt(
                    bytes(row["api_key_ciphertext"]),
                    bytes(row["api_key_nonce"]),
                    int(row["api_key_key_version"] or 1),
                )
                ciphertext, nonce = box.encrypt(secret.get_secret_value())
                await connection.execute(
                    text(
                        "UPDATE model_config SET api_key_ciphertext = :ciphertext,"
                        " api_key_nonce = :nonce, api_key_key_version = :key_version"
                        " WHERE id = :id"
                    ),
                    {
                        "ciphertext": ciphertext,
                        "nonce": nonce,
                        "key_version": box.version,
                        "id": UUID(str(row["id"])),
                    },
                )
                rotated += 1
            remaining_result = await connection.execute(
                text(
                    "SELECT count(*) FROM model_config WHERE api_key_ciphertext IS NOT NULL"
                    " AND api_key_key_version <> :current_version"
                ),
                {"current_version": box.version},
            )
            remaining = int(remaining_result.scalar_one())
        return {"rotated": rotated, "remaining_on_old_version": remaining}
    finally:
        await resources.close()


def run_key_rotation_sync(settings: Settings | None = None) -> dict[str, Any]:
    return asyncio.run(_rotate(settings or get_settings()))


if __name__ == "__main__":
    print(json.dumps(run_key_rotation_sync(), ensure_ascii=False))
