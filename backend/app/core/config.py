from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Enterprise Technical Support Assistant"
    app_env: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"
    service_token: SecretStr | None = None
    # The external Assistant API has a deliberately narrower credential than
    # the administrative API.  Keeping the previous value supports a safe
    # rolling rotation at the reverse-proxy / business-backend boundary.
    assistant_service_token: SecretStr | None = None
    assistant_service_token_previous: SecretStr | None = None
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"

    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://rag:rag-dev-password@127.0.0.1:15432/rag_platform"
    )
    required_postgres_extensions: str = "vector,pg_search"
    redis_url: SecretStr = SecretStr("redis://:rag-redis-password@127.0.0.1:16379/0")
    rq_queues: str = "ingestion,index_build,maintenance"
    rq_worker_name: str = "rag-worker"
    rq_index_job_timeout_seconds: int = 7_200
    rq_maintenance_job_timeout_seconds: int = 7_200

    minio_endpoint: str = "127.0.0.1:19000"
    minio_access_key: SecretStr = SecretStr("rag-minio")
    minio_secret_key: SecretStr = SecretStr("rag-minio-password")
    minio_secure: bool = False
    minio_document_bucket: str = "rag-documents"
    minio_image_bucket: str = "rag-images"

    readiness_timeout_seconds: float = 2.0
    model_secret_key: SecretStr | None = None
    model_secret_key_version: int = 1
    model_secret_key_previous: SecretStr | None = None
    model_secret_key_previous_version: int | None = None
    model_probe_timeout_seconds: float = 15.0
    embedding_dimension: int = 1024

    siliconflow_api_key: SecretStr | None = None
    zhipu_api_key: SecretStr | None = None
    dashscope_api_key: SecretStr | None = None
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1/"
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4/"
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1/"
    m0_target_embedding_dimension: int = 1024
    m0_provider_timeout_seconds: float = 90.0

    m2_embedding_provider: Literal["auto", "siliconflow", "dashscope", "fake"] = "auto"
    m2_embedding_batch_size: int = 8
    m2_docx_max_bytes: int = 52_428_800
    m2_docx_max_entries: int = 2_000
    m2_docx_max_uncompressed_bytes: int = 209_715_200
    m2_docx_max_entry_bytes: int = 52_428_800
    m2_docx_max_compression_ratio: float = 100.0
    m2_pdf_max_bytes: int = 104_857_600
    m2_pdf_max_pages: int = 200
    m2_chunk_target_chars: int = 1_200
    m2_chunk_max_chars: int = 2_000
    m2_chunk_overlap_paragraphs: int = 1
    m2_parent_chunk_max_chars: int = 8_000

    m3_llm_provider: Literal["auto", "zhipu", "siliconflow", "dashscope", "fake"] = "auto"
    m3_vector_top_k: int = 10
    m3_context_max_chunks: int = 8
    m3_context_token_budget: int = 6_000
    m3_history_max_messages: int = 10
    m3_history_token_budget: int = 4_000
    m3_question_max_chars: int = 4_000
    m3_prompt_max_chars: int = 60_000
    m3_prompt_token_budget: int = 30_000
    m3_turn_deadline_seconds: float = 90.0
    m3_llm_max_tokens: int = 2_048
    m3_llm_temperature: float = 0.1
    m3_provider_timeout_seconds: float = 120.0
    m3_rag_full_document_limit: int = 3
    m3_rag_full_context_token_budget: int = 20_000
    m3_rag_answer_max_tokens: int = 4_096
    m3_rag_answer_timeout_seconds: float = 45.0
    m3_rag_answer_thinking_enabled: bool = False
    m3_rag_answer_thinking_budget: int = 2_048
    m3_rag_history_max_rounds: int = 4
    m3_rag_answer_repair_attempts: int = 1

    # Document Profile generation (Profile Review page). One short structured
    # draft call per document. Keep concurrency low so a
    # transient provider slowdown cannot stall the entire maintenance task.
    profile_generation_concurrency: int = 2
    profile_llm_timeout_seconds: float = 30.0
    profile_llm_retry_attempts: int = 2
    # Profile is only a high-confidence shortcut. Broad aliases can surface
    # candidates but never force a document choice; uncertainty falls to RAG.
    profile_direct_threshold: float = 0.84
    profile_candidate_threshold: float = 0.60
    profile_direct_gap: float = 0.08
    profile_canonical_weight: float = 0.60
    profile_summary_weight: float = 0.25
    profile_alias_weight: float = 0.15
    profile_generic_alias_penalty: float = 0.35

    # RAG is the default chat chain. Setting "technical_support" restores the
    # Support-first route (with RAG fallback) for compatibility.
    chat_execution_mode: Literal["legacy_rag", "technical_support"] = "legacy_rag"
    support_history_max_messages: int = 8
    support_llm_max_tokens: int = 2_048
    support_llm_temperature: float = 0.1
    support_rag_fallback_enabled: bool = True

    m3_doc_agg_t_high: float = 0.9
    m3_doc_agg_t_low: float = 0.5
    m3_doc_agg_ratio: float = 1.8
    m3_doc_agg_min_hits: int = 2
    m3_doc_agg_max_hits: int = 3
    m3_doc_stay_score: float = 0.35
    m3_doc_switch_gap: float = 0.25
    # Caption-free search_text costs image-heavy documents a few points of
    # best-chunk rerank score; 0.70 keeps strong multi-hit aggregates
    # (score >= t_high with >= min_hits) locking while still blocking the
    # mediocre keyword-overlap case the floor exists for (best ~0.5).
    m3_doc_lock_best_floor: float = 0.70
    # A single-option clarify needs a confident best chunk; a lone weak
    # candidate falls through to the catalog-aware routing answer instead.
    m3_doc_single_clarify_best_floor: float = 0.65
    m3_rag_scope_max_tokens: int = 800
    m3_rag_query_interpreter_max_tokens: int = 512
    m3_rag_query_interpreter_timeout_seconds: float = 25.0
    m3_rag_document_card_top_k: int = 5
    # Fast-fail ceilings below the turn deadline: a hung upstream request
    # retries once (embedding, idempotent and cheap) or degrades to
    # rank-fallback routing (rerank) instead of burning the whole turn
    # budget into a 504 (observed once on 2026-09-01: a transient SiliconFlow
    # stall held the embedding call for the full 45s).
    m3_query_embedding_timeout_seconds: float = 10.0
    m3_query_embedding_retries: int = 1
    m3_rerank_timeout_seconds: float = 15.0

    m4_ocr_provider: Literal["auto", "siliconflow", "dashscope", "fake", "disabled"] = "auto"
    m4_vision_provider: Literal["auto", "zhipu", "dashscope", "fake", "disabled"] = "auto"
    m4_image_concurrency: int = 8
    m4_ocr_max_tokens: int = 4_096
    m4_vision_max_tokens: int = 1_024
    m4_max_output_chars: int = 20_000
    m4_provider_timeout_seconds: float = 120.0

    m5_rerank_provider: Literal["auto", "siliconflow", "dashscope", "fake"] = "auto"
    m5_bm25_top_k: int = 10
    m5_fusion_top_k: int = 20
    m5_rerank_top_n: int = 10
    m5_rrf_k: int = 60
    m5_rerank_max_images_per_document: int = 1
    m5_rerank_image_byte_budget: int = 5_242_880
    m5_provider_timeout_seconds: float = 120.0

    index_gc_enabled: bool = True
    index_gc_retention_count: int = 3
    index_gc_retention_days: int = 7

    # Periodic fleet-wide GC interval for the maintenance worker's
    # self-rescheduling delayed job.  0 disables the periodic run; manual
    # GC (admin button / POST /gc) is always available.
    index_gc_interval_seconds: int = 86_400

    # Frozen conversations keep old indexes alive for GC; conversations idle
    # longer than this window are purged by the auditable GC step.  0 keeps
    # them forever.
    conversation_retention_days: int = 7

    # Terminal Assistant requests that never became associated with a
    # conversation retain their idempotent result for this window.  Linked
    # requests follow the conversation lifecycle through ON DELETE CASCADE.
    # 0 disables orphan pruning.
    assistant_request_retention_days: int = 7

    # Assistant requests still RUNNING after this window were abandoned by a
    # crashed/killed API process: the maintenance worker fails them
    # automatically on startup and periodically afterwards.  Blocked
    # conversations then self-heal instead of waiting for the manual
    # ``app.assistant.recovery`` command.  The validator enforces a bound
    # above the worst-case legitimate turn (see validate_assistant_stale_window).
    # 0 disables automatic recovery.
    assistant_request_stale_seconds: int = 600

    # Process-wide admission gate for chat turn generation (RAG and Support,
    # admin playground and external Assistant alike).  Turns queued beyond
    # the limit fail fast with 503 SERVICE_BUSY after the wait window.
    # 0 disables the gate.
    chat_turn_concurrency_limit: int = 64
    chat_turn_wait_seconds: float = 2.0

    # Conversation content is visible for seven days.  Trace payload is
    # redacted after that window and retained as a minimal operations record
    # for a further period.
    trace_full_retention_days: int = 7
    trace_summary_retention_days: int = 30

    # Failed-request log retention; 0 disables pruning (keep forever).
    request_error_log_retention_days: int = 30

    # Terminal task-record retention; 0 disables pruning (keep forever).
    # SUCCEEDED/FAILED/CANCELLED rows older than the window are pruned by the
    # periodic GC sweep, keeping a small per-KB floor of newest records.
    task_record_retention_days: int = 90

    siliconflow_embedding_model: str = "Qwen/Qwen3-VL-Embedding-8B"
    siliconflow_rerank_model: str = "Qwen/Qwen3-VL-Reranker-8B"
    siliconflow_ocr_model: str = "deepseek-ai/DeepSeek-OCR"
    zhipu_llm_model: str = "glm-5.2"
    zhipu_vision_model: str = "glm-5v-turbo"

    @field_validator("api_prefix")
    @classmethod
    def validate_api_prefix(cls, value: str) -> str:
        if not value.startswith("/") or value.endswith("/"):
            raise ValueError("api_prefix must start with '/' and must not end with '/'")
        return value

    @field_validator("readiness_timeout_seconds")
    @classmethod
    def validate_readiness_timeout(cls, value: float) -> float:
        if not 0.1 <= value <= 30:
            raise ValueError("readiness timeout must be between 0.1 and 30 seconds")
        return value

    @field_validator("index_gc_retention_days")
    @classmethod
    def validate_index_gc_retention_days(cls, value: int) -> int:
        if not 1 <= value <= 3_650:
            raise ValueError("index GC retention days must be between 1 and 3650")
        return value

    @field_validator("index_gc_interval_seconds")
    @classmethod
    def validate_index_gc_interval(cls, value: int) -> int:
        if not 0 <= value <= 2_592_000:
            raise ValueError("index GC interval must be between 0 (disabled) and 2592000 seconds")
        return value

    @field_validator("conversation_retention_days")
    @classmethod
    def validate_conversation_retention_days(cls, value: int) -> int:
        if not 0 <= value <= 3_650:
            raise ValueError("conversation retention days must be between 0 and 3650")
        return value

    @field_validator("assistant_request_retention_days")
    @classmethod
    def validate_assistant_request_retention_days(cls, value: int) -> int:
        if not 0 <= value <= 3_650:
            raise ValueError("Assistant request retention days must be between 0 and 3650")
        return value

    @field_validator("assistant_request_stale_seconds")
    @classmethod
    def validate_assistant_request_stale_seconds(cls, value: int) -> int:
        if not 0 <= value <= 86_400:
            raise ValueError("Assistant request stale seconds must be between 0 and 86400")
        return value

    @field_validator("chat_turn_concurrency_limit")
    @classmethod
    def validate_chat_turn_concurrency_limit(cls, value: int) -> int:
        if not 0 <= value <= 1_000:
            raise ValueError("Chat turn concurrency limit must be between 0 and 1000")
        return value

    @field_validator("chat_turn_wait_seconds")
    @classmethod
    def validate_chat_turn_wait_seconds(cls, value: float) -> float:
        if not 0.1 <= value <= 30:
            raise ValueError("Chat turn wait seconds must be between 0.1 and 30")
        return value

    @field_validator("trace_full_retention_days", "trace_summary_retention_days")
    @classmethod
    def validate_trace_retention_days(cls, value: int) -> int:
        if not 0 <= value <= 3_650:
            raise ValueError("trace retention days must be between 0 and 3650")
        return value

    @field_validator("request_error_log_retention_days")
    @classmethod
    def validate_request_error_log_retention_days(cls, value: int) -> int:
        if not 0 <= value <= 3_650:
            raise ValueError("request error log retention days must be between 0 and 3650")
        return value

    @field_validator("task_record_retention_days")
    @classmethod
    def validate_task_record_retention_days(cls, value: int) -> int:
        if not 0 <= value <= 3_650:
            raise ValueError("task record retention days must be between 0 and 3650")
        return value

    @field_validator("rq_index_job_timeout_seconds")
    @classmethod
    def validate_rq_index_job_timeout(cls, value: int) -> int:
        if not 60 <= value <= 86_400:
            raise ValueError("RQ index job timeout must be between 60 and 86400 seconds")
        return value

    @field_validator("rq_maintenance_job_timeout_seconds")
    @classmethod
    def validate_rq_maintenance_job_timeout(cls, value: int) -> int:
        if not 60 <= value <= 86_400:
            raise ValueError("RQ maintenance job timeout must be between 60 and 86400 seconds")
        return value

    @field_validator("profile_generation_concurrency")
    @classmethod
    def validate_profile_generation_concurrency(cls, value: int) -> int:
        if not 1 <= value <= 16:
            raise ValueError("profile generation concurrency must be between 1 and 16")
        return value

    @field_validator("profile_llm_retry_attempts")
    @classmethod
    def validate_profile_llm_retry_attempts(cls, value: int) -> int:
        if not 1 <= value <= 5:
            raise ValueError("profile LLM retry attempts must be between 1 and 5")
        return value

    @field_validator("m0_target_embedding_dimension")
    @classmethod
    def validate_m0_embedding_dimension(cls, value: int) -> int:
        supported = {64, 128, 256, 512, 768, 1024, 1536, 2048, 2560, 4096}
        if value not in supported:
            raise ValueError("M0 target dimension is not documented for Qwen3-VL-Embedding-8B")
        return value

    @field_validator("embedding_dimension")
    @classmethod
    def validate_embedding_dimension(cls, value: int) -> int:
        if value != 1024:
            raise ValueError(
                "embedding dimension is frozen at 1024; changing it requires a schema migration"
            )
        return value

    @field_validator("m0_provider_timeout_seconds")
    @classmethod
    def validate_provider_timeout(cls, value: float) -> float:
        if not 5 <= value <= 300:
            raise ValueError("M0 provider timeout must be between 5 and 300 seconds")
        return value

    @field_validator("m2_embedding_batch_size")
    @classmethod
    def validate_embedding_batch_size(cls, value: int) -> int:
        if not 1 <= value <= 64:
            raise ValueError("M2 embedding batch size must be between 1 and 64")
        return value

    @field_validator(
        "m2_docx_max_bytes", "m2_docx_max_uncompressed_bytes", "m2_docx_max_entry_bytes"
    )
    @classmethod
    def validate_docx_byte_limits(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("M2 DOCX byte limits must be positive")
        return value

    @field_validator("m2_docx_max_entries")
    @classmethod
    def validate_docx_entry_limit(cls, value: int) -> int:
        if not 10 <= value <= 100_000:
            raise ValueError("M2 DOCX entry limit must be between 10 and 100000")
        return value

    @field_validator("m2_docx_max_compression_ratio")
    @classmethod
    def validate_docx_compression_ratio(cls, value: float) -> float:
        if not 1 <= value <= 10_000:
            raise ValueError("M2 DOCX compression ratio must be between 1 and 10000")
        return value

    @field_validator("m2_chunk_target_chars", "m2_chunk_max_chars", "m2_parent_chunk_max_chars")
    @classmethod
    def validate_chunk_chars(cls, value: int) -> int:
        if not 100 <= value <= 20_000:
            raise ValueError("M2 chunk character limits must be between 100 and 20000")
        return value

    @field_validator("m2_chunk_overlap_paragraphs")
    @classmethod
    def validate_chunk_overlap(cls, value: int) -> int:
        if not 0 <= value <= 10:
            raise ValueError("M2 chunk overlap paragraphs must be between 0 and 10")
        return value

    @model_validator(mode="after")
    def validate_chunk_limits(self) -> Self:
        if self.m2_chunk_target_chars > self.m2_chunk_max_chars:
            raise ValueError("M2 chunk target chars cannot exceed max chars")
        if self.m2_parent_chunk_max_chars < self.m2_chunk_max_chars:
            raise ValueError("M2 parent chunk max chars cannot be smaller than child max chars")
        return self

    @model_validator(mode="after")
    def validate_assistant_stale_window(self) -> Self:
        # Auto recovery must never reap a turn that could still be live.
        # RAG turns are bounded by the overall turn deadline, so with the
        # Support-first policy frozen (ADR-0021) that deadline is the
        # worst-case legitimate turn duration.  If SUPPORT_WITH_RAG_FALLBACK
        # is ever re-enabled, raise this bound above
        # 2 * m3_provider_timeout_seconds + m3_turn_deadline_seconds.
        if (
            self.assistant_request_stale_seconds > 0
            and self.assistant_request_stale_seconds <= self.m3_turn_deadline_seconds
        ):
            raise ValueError(
                "Assistant request stale seconds must exceed the turn deadline or be 0 (disabled)"
            )
        return self

    @field_validator("m3_vector_top_k", "m3_context_max_chunks", "m3_history_max_messages")
    @classmethod
    def validate_m3_count_limits(cls, value: int) -> int:
        if not 1 <= value <= 100:
            raise ValueError("M3 count limits must be between 1 and 100")
        return value

    @field_validator(
        "m3_context_token_budget",
        "m3_history_token_budget",
        "m3_llm_max_tokens",
        "m3_rag_scope_max_tokens",
        "m3_rag_query_interpreter_max_tokens",
        "m3_rag_full_context_token_budget",
        "m3_rag_answer_max_tokens",
        "m3_rag_answer_thinking_budget",
    )
    @classmethod
    def validate_m3_token_limits(cls, value: int) -> int:
        if not 100 <= value <= 100_000:
            raise ValueError("M3 token limits must be between 100 and 100000")
        return value

    @field_validator(
        "m3_rag_document_card_top_k",
        "m3_rag_full_document_limit",
        "m3_rag_history_max_rounds",
    )
    @classmethod
    def validate_semantic_orchestration_counts(cls, value: int) -> int:
        if not 1 <= value <= 20:
            raise ValueError("RAG semantic orchestration counts must be between 1 and 20")
        return value

    @field_validator("m3_question_max_chars", "m3_prompt_max_chars")
    @classmethod
    def validate_m3_character_limits(cls, value: int) -> int:
        if not 100 <= value <= 1_000_000:
            raise ValueError("M3 character limits must be between 100 and 1000000")
        return value

    @field_validator("m3_prompt_token_budget")
    @classmethod
    def validate_m3_prompt_token_budget(cls, value: int) -> int:
        if not 1_000 <= value <= 200_000:
            raise ValueError("M3 prompt token budget must be between 1000 and 200000")
        return value

    @field_validator("m3_turn_deadline_seconds")
    @classmethod
    def validate_m3_turn_deadline(cls, value: float) -> float:
        if not 1.0 <= value <= 300.0:
            raise ValueError("M3 turn deadline must be between 1 and 300 seconds")
        return value

    @field_validator("m3_llm_temperature")
    @classmethod
    def validate_m3_temperature(cls, value: float) -> float:
        if not 0 <= value <= 2:
            raise ValueError("M3 LLM temperature must be between 0 and 2")
        return value

    @field_validator("m3_provider_timeout_seconds")
    @classmethod
    def validate_m3_timeout(cls, value: float) -> float:
        if not 5 <= value <= 600:
            raise ValueError("M3 provider timeout must be between 5 and 600 seconds")
        return value

    @field_validator("profile_llm_timeout_seconds")
    @classmethod
    def validate_profile_llm_timeout(cls, value: float) -> float:
        if not 5 <= value <= 600:
            raise ValueError("profile LLM timeout must be between 5 and 600 seconds")
        return value

    @field_validator(
        "profile_direct_threshold",
        "profile_candidate_threshold",
        "profile_direct_gap",
        "profile_canonical_weight",
        "profile_summary_weight",
        "profile_alias_weight",
        "profile_generic_alias_penalty",
    )
    @classmethod
    def validate_profile_routing_scores(cls, value: float) -> float:
        if not 0 < value <= 1:
            raise ValueError("profile routing scores must be between 0 and 1")
        return value

    @field_validator(
        "m3_doc_agg_t_high",
        "m3_doc_agg_t_low",
        "m3_doc_stay_score",
        "m3_doc_switch_gap",
        "m3_doc_lock_best_floor",
    )
    @classmethod
    def validate_m3_doc_scores(cls, value: float) -> float:
        if not 0 < value <= 1:
            raise ValueError("M3 doc routing scores must be between 0 and 1")
        return value

    @field_validator("m3_doc_agg_ratio")
    @classmethod
    def validate_m3_doc_ratio(cls, value: float) -> float:
        if not 1 <= value <= 10:
            raise ValueError("M3 doc routing ratio must be between 1 and 10")
        return value

    @field_validator("m3_doc_agg_min_hits", "m3_doc_agg_max_hits")
    @classmethod
    def validate_m3_doc_hits(cls, value: int) -> int:
        if not 1 <= value <= 10:
            raise ValueError("M3 doc routing hit limits must be between 1 and 10")
        return value

    @field_validator(
        "m3_query_embedding_timeout_seconds",
        "m3_rerank_timeout_seconds",
        "m3_rag_query_interpreter_timeout_seconds",
        "m3_rag_answer_timeout_seconds",
    )
    @classmethod
    def validate_upstream_call_timeouts(cls, value: float) -> float:
        if not 0.1 <= value <= 60.0:
            raise ValueError("M3 upstream call timeouts must be between 0.1 and 60 seconds")
        return value

    @field_validator("m3_query_embedding_retries")
    @classmethod
    def validate_query_embedding_retries(cls, value: int) -> int:
        if not 0 <= value <= 3:
            raise ValueError("M3 query embedding retries must be between 0 and 3")
        return value

    @field_validator("m3_rag_answer_repair_attempts")
    @classmethod
    def validate_answer_repair_attempts(cls, value: int) -> int:
        if not 0 <= value <= 2:
            raise ValueError("RAG V2 answer repair attempts must be between 0 and 2")
        return value

    @field_validator("m4_image_concurrency")
    @classmethod
    def validate_m4_concurrency(cls, value: int) -> int:
        if not 1 <= value <= 16:
            raise ValueError("M4 image concurrency must be between 1 and 16")
        return value

    @field_validator("m4_ocr_max_tokens", "m4_vision_max_tokens", "m4_max_output_chars")
    @classmethod
    def validate_m4_output_limits(cls, value: int) -> int:
        if not 1 <= value <= 100_000:
            raise ValueError("M4 output limits must be between 1 and 100000")
        return value

    @field_validator("m4_provider_timeout_seconds")
    @classmethod
    def validate_m4_timeout(cls, value: float) -> float:
        if not 5 <= value <= 600:
            raise ValueError("M4 provider timeout must be between 5 and 600 seconds")
        return value

    @field_validator(
        "m5_bm25_top_k",
        "m5_fusion_top_k",
        "m5_rerank_top_n",
        "m5_rerank_max_images_per_document",
    )
    @classmethod
    def validate_m5_count_limits(cls, value: int) -> int:
        if not 1 <= value <= 100:
            raise ValueError("M5 count limits must be between 1 and 100")
        return value

    @field_validator("m5_rrf_k")
    @classmethod
    def validate_m5_rrf_k(cls, value: int) -> int:
        if not 1 <= value <= 10_000:
            raise ValueError("M5 RRF k must be between 1 and 10000")
        return value

    @field_validator("m5_rerank_image_byte_budget")
    @classmethod
    def validate_m5_image_budget(cls, value: int) -> int:
        if not 1_024 <= value <= 52_428_800:
            raise ValueError("M5 rerank image budget must be between 1024 and 52428800")
        return value

    @field_validator("m5_provider_timeout_seconds")
    @classmethod
    def validate_m5_timeout(cls, value: float) -> float:
        if not 5 <= value <= 600:
            raise ValueError("M5 provider timeout must be between 5 and 600 seconds")
        return value

    @field_validator("siliconflow_base_url", "zhipu_base_url", "dashscope_base_url")
    @classmethod
    def validate_provider_base_url(cls, value: str) -> str:
        if not value.startswith("https://") or not value.endswith("/"):
            raise ValueError("provider base URL must use HTTPS and end with '/'")
        return value

    @model_validator(mode="after")
    def validate_production_security(self) -> Self:
        if (
            self.m3_rag_query_interpreter_timeout_seconds + self.m3_rag_answer_timeout_seconds
            >= self.m3_turn_deadline_seconds
        ):
            raise ValueError(
                "RAG V2 query and answer timeouts must leave time inside the turn deadline"
            )
        if self.app_env == "production" and not self.service_token:
            raise ValueError("SERVICE_TOKEN is required in production")
        if self.app_env == "production":
            assistant_token = (
                self.assistant_service_token.get_secret_value()
                if self.assistant_service_token is not None
                else ""
            )
            forbidden_assistant_tokens = {
                "change-me-for-assistant-integration",
                "change-me",
            }
            if (
                len(assistant_token) < 32
                or assistant_token != assistant_token.strip()
                or assistant_token in forbidden_assistant_tokens
            ):
                raise ValueError(
                    "ASSISTANT_SERVICE_TOKEN must be a non-placeholder random value "
                    "of at least 32 characters in production"
                )

            previous_token = (
                self.assistant_service_token_previous.get_secret_value()
                if self.assistant_service_token_previous is not None
                else ""
            )
            if previous_token and (
                len(previous_token) < 32
                or previous_token != previous_token.strip()
                or previous_token in forbidden_assistant_tokens
            ):
                raise ValueError(
                    "ASSISTANT_SERVICE_TOKEN_PREVIOUS must be a non-placeholder "
                    "random value of at least 32 characters"
                )
            if previous_token and previous_token == assistant_token:
                raise ValueError(
                    "ASSISTANT_SERVICE_TOKEN_PREVIOUS must differ from ASSISTANT_SERVICE_TOKEN"
                )
        if self.app_env in {"staging", "production"}:
            fake_modes = {
                "M2_EMBEDDING_PROVIDER": self.m2_embedding_provider,
                "M3_LLM_PROVIDER": self.m3_llm_provider,
                "M4_OCR_PROVIDER": self.m4_ocr_provider,
                "M4_VISION_PROVIDER": self.m4_vision_provider,
                "M5_RERANK_PROVIDER": self.m5_rerank_provider,
            }
            forbidden = [name for name, value in fake_modes.items() if value == "fake"]
            if forbidden:
                raise ValueError(
                    ", ".join(f"{name}=fake" for name in forbidden)
                    + " is forbidden in staging and production"
                )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def postgres_extension_list(self) -> tuple[str, ...]:
        return tuple(
            extension.strip()
            for extension in self.required_postgres_extensions.split(",")
            if extension.strip()
        )

    @property
    def queue_names(self) -> tuple[str, ...]:
        return tuple(queue.strip() for queue in self.rq_queues.split(",") if queue.strip())

    @staticmethod
    def has_secret(value: SecretStr | None) -> bool:
        return value is not None and bool(value.get_secret_value().strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
