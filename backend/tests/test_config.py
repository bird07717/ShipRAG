from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_production_requires_service_token() -> None:
    with pytest.raises(ValidationError, match="SERVICE_TOKEN"):
        Settings(_env_file=None, app_env="production", service_token=None)


def test_csv_settings_are_normalized() -> None:
    settings = Settings(
        _env_file=None,
        cors_origins="http://a.example, http://b.example,",
        rq_queues="ingestion, maintenance",
        required_postgres_extensions="vector, pg_search",
    )

    assert settings.cors_origin_list == ["http://a.example", "http://b.example"]
    assert settings.queue_names == ("ingestion", "maintenance")
    assert settings.postgres_extension_list == ("vector", "pg_search")


def test_embedding_dimension_is_frozen() -> None:
    with pytest.raises(ValidationError, match="frozen at 1024"):
        Settings(_env_file=None, embedding_dimension=0)


def test_image_understanding_concurrency_defaults_to_eight() -> None:
    settings = Settings(_env_file=None)

    assert settings.m4_image_concurrency == 8


def test_index_job_timeout_defaults_to_two_hours() -> None:
    settings = Settings(_env_file=None)

    assert settings.rq_index_job_timeout_seconds == 7_200


def test_index_job_timeout_rejects_unsafe_values() -> None:
    with pytest.raises(ValidationError, match="RQ index job timeout"):
        Settings(_env_file=None, rq_index_job_timeout_seconds=30)
