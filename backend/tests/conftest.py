from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.main import create_app

# The Postgres/Redis integration suites skip themselves when their env vars
# are absent. When the developer stack described by the project .env is
# reachable, wire those vars automatically so a plain `pytest` run covers
# the full suite (the guard tests for begin_turn-level regressions live
# there). Explicit environment variables always win, and unreachable
# infrastructure keeps the previous skip behaviour.
_REDIS_TEST_DB = 15  # dedicated test DB — the GC suite flushes it on setup


def _project_env() -> dict[str, str]:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        values[key.strip()] = raw.strip().strip("\"'")
    return values


def _endpoint_reachable(url: str) -> bool:
    parsed = urlparse(url)
    if not parsed.hostname:
        return False
    default_port = 6379 if (parsed.scheme or "").startswith("redis") else 5432
    try:
        with socket.create_connection((parsed.hostname, parsed.port or default_port), timeout=0.5):
            return True
    except OSError:
        return False


def _redis_test_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(path=f"/{_REDIS_TEST_DB}"))


def _bootstrap_integration_env() -> None:
    env_file = _project_env()

    def value(key: str) -> str | None:
        return os.environ.get(key) or env_file.get(key)

    database_url = value("DATABASE_URL")
    if database_url and _endpoint_reachable(database_url):
        os.environ.setdefault("PHASE4_TEST_DATABASE_URL", database_url)
        os.environ.setdefault("SUPPORT_TEST_DATABASE_URL", database_url)
        model_secret = value("MODEL_SECRET_KEY")
        if model_secret:
            os.environ.setdefault("MODEL_SECRET_KEY", model_secret)

    redis_url = value("REDIS_URL")
    if redis_url and _endpoint_reachable(redis_url):
        test_redis = _redis_test_url(redis_url)
        os.environ.setdefault("GC_TEST_REDIS_URL", test_redis)
        os.environ.setdefault("PROFILE_TEST_REDIS_URL", test_redis)


_bootstrap_integration_env()


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
