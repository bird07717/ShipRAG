from __future__ import annotations

import base64
import os
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from app.common.errors import ApiError
from app.core.config import Settings
from app.core.secrets import ModelSecretBox
from app.ingestion.model_config import _secret_box, load_build_models


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> list[dict[str, Any]]:
        return self.rows


class _Connection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.params: dict[str, Any] = {}

    async def execute(self, statement: Any, params: dict[str, Any]) -> _Rows:
        assert "FROM model_config" in str(statement)
        self.params = params
        return _Rows(self.rows)


class _Engine:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.connection = _Connection(rows)

    @asynccontextmanager
    async def connect(self) -> Any:
        yield self.connection


def _secret() -> SecretStr:
    return SecretStr(base64.b64encode(os.urandom(32)).decode("ascii"))


def _row(model_type: str, provider: str, **overrides: Any) -> dict[str, Any]:
    return {
        "model_type": model_type,
        "provider": provider,
        "model_name": "model-name",
        "parameters": {},
        "base_url": None,
        "api_key_ciphertext": None,
        "api_key_nonce": None,
        "api_key_key_version": None,
        **overrides,
    }


def test_build_model_secret_box_requires_current_key() -> None:
    with pytest.raises(ApiError) as exc_info:
        _secret_box(Settings(_env_file=None, app_env="test", model_secret_key=None))
    assert exc_info.value.code == "MODEL_SECRET_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_load_build_models_handles_database_fallback_and_encrypted_keys() -> None:
    secret_key = _secret()
    box = ModelSecretBox(secret_key)
    ciphertext, nonce = box.encrypt("db-api-key")
    rows = [
        _row("UNKNOWN", "zhipu"),
        _row("OCR", "unsupported"),
        _row("VISION", "zhipu", parameters=["invalid"]),
        _row(
            "EMBEDDING",
            "siliconflow",
            parameters={"dimensions": 1024},
            base_url="https://models.example/v1",
            api_key_ciphertext=ciphertext,
            api_key_nonce=nonce,
            api_key_key_version=1,
        ),
    ]
    engine = _Engine(rows)
    settings = Settings(
        _env_file=None,
        app_env="test",
        model_secret_key=secret_key,
        model_secret_key_version=1,
    )

    configs = await load_build_models(cast(AsyncEngine, engine), settings)

    assert engine.connection.params == {"types": ["EMBEDDING", "OCR", "VISION"]}
    assert configs["OCR"].source == "ENV_FALLBACK"
    assert configs["OCR"].snapshot is None
    assert configs["VISION"].source == "DATABASE"
    assert configs["VISION"].snapshot is not None
    assert configs["VISION"].snapshot.parameters == {}
    embedding = configs["EMBEDDING"].snapshot
    assert embedding is not None
    assert embedding.parameters == {"dimensions": 1024}
    assert embedding.base_url == "https://models.example/v1"
    assert embedding.api_key is not None
    assert embedding.api_key.get_secret_value() == "db-api-key"
