from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        service_token=SecretStr("test-token"),
        database_url=SecretStr("postgresql+asyncpg://rag:password@127.0.0.1:1/test"),
        redis_url=SecretStr("redis://:password@127.0.0.1:1/0"),
        minio_endpoint="127.0.0.1:1",
        readiness_timeout_seconds=0.1,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client
