from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_production_requires_service_token() -> None:
    with pytest.raises(ValidationError, match="SERVICE_TOKEN"):
        Settings(_env_file=None, app_env="production", service_token=None)


@pytest.mark.parametrize(
    "assistant_token",
    [None, "change-me-for-assistant-integration", "too-short"],
)
def test_production_requires_a_strong_assistant_token(assistant_token: str | None) -> None:
    with pytest.raises(ValidationError, match="ASSISTANT_SERVICE_TOKEN"):
        Settings(
            _env_file=None,
            app_env="production",
            service_token="admin-token",
            assistant_service_token=assistant_token,
        )


def test_production_accepts_a_strong_assistant_token_and_rotation_value() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        service_token="admin-token",
        assistant_service_token="a" * 32,
        assistant_service_token_previous="b" * 32,
    )

    assert settings.assistant_service_token is not None


def test_production_rejects_duplicate_assistant_rotation_token() -> None:
    with pytest.raises(ValidationError, match="must differ"):
        Settings(
            _env_file=None,
            app_env="production",
            service_token="admin-token",
            assistant_service_token="a" * 32,
            assistant_service_token_previous="a" * 32,
        )


@pytest.mark.parametrize("app_env", ["staging", "production"])
@pytest.mark.parametrize(
    ("setting_name", "environment_name"),
    [
        ("m2_embedding_provider", "M2_EMBEDDING_PROVIDER"),
        ("m3_llm_provider", "M3_LLM_PROVIDER"),
        ("m5_rerank_provider", "M5_RERANK_PROVIDER"),
    ],
)
def test_deployed_environments_reject_fake_model_providers(
    app_env: str, setting_name: str, environment_name: str
) -> None:
    with pytest.raises(ValidationError, match=rf"{environment_name}=fake is forbidden"):
        Settings(
            _env_file=None,
            app_env=app_env,
            service_token="configured",
            assistant_service_token="a" * 32 if app_env == "production" else None,
            **{setting_name: "fake"},
        )


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


def test_worker_name_is_configurable_for_independent_queue_workers() -> None:
    settings = Settings(_env_file=None, rq_worker_name="rag-index-worker")

    assert settings.rq_worker_name == "rag-index-worker"


def test_chat_execution_mode_only_supports_formal_and_manual_rollback() -> None:
    assert Settings(_env_file=None).chat_execution_mode == "legacy_rag"
    assert (
        Settings(_env_file=None, chat_execution_mode="technical_support").chat_execution_mode
        == "technical_support"
    )
    with pytest.raises(ValidationError):
        Settings(_env_file=None, chat_execution_mode="shadow")


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
