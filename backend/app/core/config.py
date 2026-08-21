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

    app_name: str = "Enterprise RAG Platform"
    app_env: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"
    service_token: SecretStr | None = None
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"

    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://rag:rag-dev-password@127.0.0.1:15432/rag_platform"
    )
    required_postgres_extensions: str = "vector,pg_search"
    redis_url: SecretStr = SecretStr("redis://:rag-redis-password@127.0.0.1:16379/0")
    rq_queues: str = "ingestion,index_build,maintenance"
    rq_index_job_timeout_seconds: int = 7_200

    minio_endpoint: str = "127.0.0.1:19000"
    minio_access_key: SecretStr = SecretStr("rag-minio")
    minio_secret_key: SecretStr = SecretStr("rag-minio-password")
    minio_secure: bool = False
    minio_document_bucket: str = "rag-documents"
    minio_image_bucket: str = "rag-images"

    readiness_timeout_seconds: float = 2.0
    model_secret_key: SecretStr | None = None
    embedding_dimension: int = 1024

    siliconflow_api_key: SecretStr | None = None
    zhipu_api_key: SecretStr | None = None
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1/"
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4/"
    m0_target_embedding_dimension: int = 1024
    m0_provider_timeout_seconds: float = 90.0

    m2_embedding_provider: Literal["auto", "siliconflow", "fake"] = "auto"
    m2_embedding_batch_size: int = 8
    m2_docx_max_bytes: int = 52_428_800
    m2_docx_max_entries: int = 2_000
    m2_docx_max_uncompressed_bytes: int = 209_715_200
    m2_docx_max_entry_bytes: int = 52_428_800
    m2_docx_max_compression_ratio: float = 100.0
    m2_chunk_target_chars: int = 1_200
    m2_chunk_max_chars: int = 2_000
    m2_chunk_overlap_paragraphs: int = 1
    m2_parent_chunk_max_chars: int = 8_000

    m3_llm_provider: Literal["auto", "zhipu", "fake"] = "auto"
    m3_vector_top_k: int = 10
    m3_context_max_chunks: int = 8
    m3_context_token_budget: int = 6_000
    m3_history_max_messages: int = 10
    m3_history_token_budget: int = 2_000
    m3_question_max_chars: int = 4_000
    m3_prompt_max_chars: int = 60_000
    m3_prompt_token_budget: int = 30_000
    m3_llm_max_tokens: int = 2_048
    m3_llm_temperature: float = 0.1
    m3_trace_retain_prompt: bool = True
    m3_provider_timeout_seconds: float = 120.0

    m3_doc_agg_t_high: float = 0.9
    m3_doc_agg_t_low: float = 0.5
    m3_doc_agg_ratio: float = 1.8
    m3_doc_agg_min_hits: int = 2
    m3_doc_agg_max_hits: int = 3
    m3_doc_stay_score: float = 0.35
    m3_doc_switch_gap: float = 0.25
    m3_doc_lock_best_floor: float = 0.75
    m3_doc_delivery_max_tokens: int = 8_000

    m3_query_rewrite_enabled: bool = True
    m3_query_rewrite_max_chars: int = 40
    m3_query_rewrite_max_tokens: int = 192
    m3_query_rewrite_timeout_seconds: float = 8.0

    m4_ocr_provider: Literal["auto", "siliconflow", "fake", "disabled"] = "auto"
    m4_vision_provider: Literal["auto", "zhipu", "fake", "disabled"] = "auto"
    m4_image_concurrency: int = 8
    m4_ocr_max_tokens: int = 4_096
    m4_vision_max_tokens: int = 1_024
    m4_max_output_chars: int = 20_000
    m4_provider_timeout_seconds: float = 120.0

    m5_rerank_provider: Literal["auto", "siliconflow", "fake"] = "auto"
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

    @field_validator("rq_index_job_timeout_seconds")
    @classmethod
    def validate_rq_index_job_timeout(cls, value: int) -> int:
        if not 60 <= value <= 86_400:
            raise ValueError("RQ index job timeout must be between 60 and 86400 seconds")
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

    @field_validator("m3_vector_top_k", "m3_context_max_chunks", "m3_history_max_messages")
    @classmethod
    def validate_m3_count_limits(cls, value: int) -> int:
        if not 1 <= value <= 100:
            raise ValueError("M3 count limits must be between 1 and 100")
        return value

    @field_validator("m3_context_token_budget", "m3_history_token_budget", "m3_llm_max_tokens")
    @classmethod
    def validate_m3_token_limits(cls, value: int) -> int:
        if not 100 <= value <= 100_000:
            raise ValueError("M3 token limits must be between 100 and 100000")
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

    @field_validator("m3_doc_delivery_max_tokens")
    @classmethod
    def validate_m3_doc_delivery_tokens(cls, value: int) -> int:
        if not 1_000 <= value <= 100_000:
            raise ValueError("M3 doc delivery token limit must be between 1000 and 100000")
        return value

    @field_validator("m3_query_rewrite_max_chars")
    @classmethod
    def validate_query_rewrite_chars(cls, value: int) -> int:
        if not 10 <= value <= 500:
            raise ValueError("M3 query rewrite char limit must be between 10 and 500")
        return value

    @field_validator("m3_query_rewrite_max_tokens")
    @classmethod
    def validate_query_rewrite_tokens(cls, value: int) -> int:
        if not 16 <= value <= 1_024:
            raise ValueError("M3 query rewrite token limit must be between 16 and 1024")
        return value

    @field_validator("m3_query_rewrite_timeout_seconds")
    @classmethod
    def validate_query_rewrite_timeout(cls, value: float) -> float:
        if not 1 <= value <= 60:
            raise ValueError("M3 query rewrite timeout must be between 1 and 60 seconds")
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

    @field_validator("siliconflow_base_url", "zhipu_base_url")
    @classmethod
    def validate_provider_base_url(cls, value: str) -> str:
        if not value.startswith("https://") or not value.endswith("/"):
            raise ValueError("provider base URL must use HTTPS and end with '/'")
        return value

    @model_validator(mode="after")
    def validate_production_security(self) -> Self:
        if self.app_env == "production" and not self.service_token:
            raise ValueError("SERVICE_TOKEN is required in production")
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
