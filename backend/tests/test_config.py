from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_development_env_example_is_loadable_with_safe_rag_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "MODEL_SECRET_KEY_PREVIOUS",
        "MODEL_SECRET_KEY_PREVIOUS_VERSION",
        "M3_RAG_SCOPE_MAX_TOKENS",
        "M3_RAG_FULL_DOCUMENT_LIMIT",
        "M3_RAG_FULL_CONTEXT_TOKEN_BUDGET",
        "M3_RAG_ANSWER_MAX_TOKENS",
        "M3_RAG_ANSWER_TIMEOUT_SECONDS",
        "M3_RAG_ANSWER_THINKING_ENABLED",
        "M3_RAG_ANSWER_THINKING_BUDGET",
        "M3_RAG_HISTORY_MAX_ROUNDS",
        "M3_RAG_ANSWER_REPAIR_ATTEMPTS",
        "M3_RAG_QUERY_INTERPRETER_MAX_TOKENS",
        "M3_RAG_QUERY_INTERPRETER_TIMEOUT_SECONDS",
        "M3_RAG_QUERY_VARIANT_LIMIT",
        "M3_RAG_DOCUMENT_CARD_TOP_K",
        "M3_DOC_LOCK_BEST_FLOOR",
        "M3_DOC_SINGLE_CLARIFY_BEST_FLOOR",
        "M3_TURN_DEADLINE_SECONDS",
        "M3_QUERY_EMBEDDING_TIMEOUT_SECONDS",
        "M3_QUERY_EMBEDDING_RETRIES",
        "M3_RERANK_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=PROJECT_ROOT / ".env.example")

    assert settings.model_secret_key_previous is None
    assert settings.model_secret_key_previous_version is None
    assert settings.m3_rag_scope_max_tokens == 800
    assert settings.m3_rag_full_document_limit == 3
    assert settings.m3_rag_full_context_token_budget == 20_000
    assert settings.m3_rag_answer_max_tokens == 4_096
    assert settings.m3_rag_answer_timeout_seconds == 45.0
    assert settings.m3_rag_answer_thinking_enabled is False
    assert settings.m3_rag_answer_thinking_budget == 2_048
    assert settings.m3_rag_history_max_rounds == 4
    assert settings.m3_rag_answer_repair_attempts == 1
    assert settings.m3_rag_query_interpreter_max_tokens == 512
    assert settings.m3_rag_query_interpreter_timeout_seconds == 25.0
    assert settings.m3_rag_query_variant_limit == 3
    assert settings.m3_rag_document_card_top_k == 5
    assert settings.m3_doc_lock_best_floor == 0.70
    assert settings.m3_doc_single_clarify_best_floor == 0.65
    assert settings.m3_turn_deadline_seconds == 90.0
    assert settings.m3_query_embedding_timeout_seconds == 10.0
    assert settings.m3_query_embedding_retries == 1
    assert settings.m3_rerank_timeout_seconds == 15.0


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("m3_rag_query_variant_limit", 0),
        ("m3_rag_document_card_top_k", 21),
        ("m3_rag_query_interpreter_timeout_seconds", 0),
        ("m3_rag_query_interpreter_max_tokens", 99),
        ("m3_rag_full_document_limit", 0),
        ("m3_rag_history_max_rounds", 21),
        ("m3_rag_full_context_token_budget", 99),
        ("m3_rag_answer_max_tokens", 100_001),
        ("m3_rag_answer_thinking_budget", 99),
        ("m3_rag_answer_timeout_seconds", 61),
        ("m3_rag_answer_repair_attempts", 3),
    ],
)
def test_semantic_orchestration_limits_are_bounded(name: str, value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{name: value})


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


def test_rag_v2_timeouts_must_leave_turn_completion_budget() -> None:
    with pytest.raises(ValidationError, match="leave time inside the turn deadline"):
        Settings(
            _env_file=None,
            app_env="test",
            m3_turn_deadline_seconds=60,
            m3_rag_query_interpreter_timeout_seconds=15,
            m3_rag_answer_timeout_seconds=45,
        )


def test_assistant_stale_window_defaults_are_safe() -> None:
    settings = Settings(_env_file=None, app_env="test")
    assert settings.assistant_request_stale_seconds > settings.m3_turn_deadline_seconds


def test_assistant_stale_window_must_exceed_turn_deadline() -> None:
    # RAG turns are deadline-bounded, so the stale window must clear the
    # deadline or auto recovery could reap a live turn.
    with pytest.raises(ValidationError, match="exceed the turn deadline"):
        Settings(
            _env_file=None,
            app_env="test",
            m3_turn_deadline_seconds=300,
            assistant_request_stale_seconds=300,
        )


def test_assistant_stale_window_ignores_provider_timeout_while_support_frozen() -> None:
    # Support-first policy is frozen (ADR-0021): a slow provider cannot extend
    # a live RAG turn beyond the turn deadline, so the provider timeout must
    # not constrain the stale window.
    settings = Settings(
        _env_file=None,
        app_env="test",
        m3_provider_timeout_seconds=600,
        assistant_request_stale_seconds=600,
    )
    assert settings.assistant_request_stale_seconds == 600


def test_assistant_stale_window_zero_disables_recovery_bound() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        m3_turn_deadline_seconds=90,
        assistant_request_stale_seconds=0,
    )
    assert settings.assistant_request_stale_seconds == 0


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


def test_assistant_request_retention_defaults_to_seven_days_and_allows_disable() -> None:
    assert Settings(_env_file=None).assistant_request_retention_days == 7
    assert (
        Settings(
            _env_file=None, assistant_request_retention_days=0
        ).assistant_request_retention_days
        == 0
    )


@pytest.mark.parametrize("days", [-1, 3_651])
def test_assistant_request_retention_rejects_out_of_range_values(days: int) -> None:
    with pytest.raises(ValidationError, match="Assistant request retention days"):
        Settings(_env_file=None, assistant_request_retention_days=days)
