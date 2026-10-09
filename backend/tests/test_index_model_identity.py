"""Model/index consistency: no network or paid model calls."""

from __future__ import annotations

import importlib.util
import json
import os
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from minio import Minio
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.common.errors import ApiError
from app.core.config import Settings
from app.gateway.repository import GatewayRepository
from app.ingestion.embedding import FakeEmbeddingProvider, create_embedding_provider
from app.ingestion.model_identity import (
    configured_embedding_identity,
    index_embedding_matches,
    legacy_embedding_unchanged_sql,
    model_fingerprint,
    provider_identity,
)
from app.ingestion.models import content_revision_for_mime
from app.ingestion.pipeline import IndexPipeline
from app.rag.document_card_repository import DocumentCardRepository
from app.rag.models import ModelSnapshot, Turn
from app.rag.repository import RagRepository
from app.services.rag import RagService


def settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, app_env="test", **overrides)


def snapshot(**overrides: Any) -> ModelSnapshot:
    return ModelSnapshot(
        **{
            "provider": "siliconflow",
            "model_name": "same-name",
            "base_url": "https://models.example/v1/",
            "api_key": SecretStr("test-key"),
            "parameters": {},
            **overrides,
        }
    )


def identity(config: ModelSnapshot) -> dict[str, Any]:
    return configured_embedding_identity(
        settings(),
        provider=config.provider,
        model_name=config.model_name,
        base_url=config.base_url,
        has_key=True,
    )


@pytest.mark.parametrize("provider", ["siliconflow", "zhipu", "dashscope"])
@pytest.mark.parametrize("base_url", [None, "https://models.example/v1/"])
@pytest.mark.asyncio
async def test_readiness_identity_matches_actual_embedding_factory(
    provider: str, base_url: str | None
) -> None:
    config = snapshot(provider=provider, base_url=base_url)
    embedder = create_embedding_provider(settings(), config)
    try:
        assert provider_identity(embedder, "EMBEDDING", settings()) == identity(config)
    finally:
        await embedder.aclose()


@pytest.mark.parametrize(
    "changes",
    [{"provider": "dashscope"}, {"base_url": "https://other.example/v1"}, {"model_name": "new"}],
)
def test_same_name_or_dimension_does_not_allow_incompatible_embedding(
    changes: dict[str, Any],
) -> None:
    stored = {"version": 1, "EMBEDDING": identity(snapshot())}
    assert not index_embedding_matches(stored, "same-name", identity(snapshot(**changes)))


def test_key_rotation_unused_parameters_and_trailing_slash_do_not_change_identity() -> None:
    original = snapshot()
    rotated = replace(
        original,
        api_key=SecretStr("rotated-key"),
        base_url="https://models.example/v1",
        parameters={"secret_source": "database"},
    )
    assert identity(original) == identity(rotated)
    assert "test-key" not in json.dumps(identity(original))
    assert "rotated-key" not in json.dumps(identity(rotated))


@pytest.mark.parametrize("model_type", ["EMBEDDING", "OCR", "VISION"])
def test_every_build_model_participates_in_reuse_fingerprint(model_type: str) -> None:
    original = {"version": 1, model_type: {"provider": "same", "model_name": "old"}}
    changed = {"version": 1, model_type: {"provider": "same", "model_name": "new"}}
    assert model_fingerprint(original) != model_fingerprint(changed)
    assert model_fingerprint(original) == model_fingerprint(dict(reversed(list(original.items()))))


def test_unknown_or_incomplete_snapshot_never_falls_back_to_name_only() -> None:
    actual = identity(snapshot())
    assert not index_embedding_matches({"version": 1}, "same-name", actual)
    assert not index_embedding_matches({"version": 999, "EMBEDDING": actual}, "same-name", actual)
    assert not index_embedding_matches({}, "same-name", actual, legacy_unchanged=False)
    assert index_embedding_matches({}, "same-name", actual, legacy_unchanged=True)


def make_turn(config: ModelSnapshot, stored: dict[str, Any]) -> Turn:
    return Turn(
        trace_id=uuid4(),
        conversation_id=uuid4(),
        user_message_id=uuid4(),
        assistant_message_id=uuid4(),
        knowledge_id=uuid4(),
        index_id=uuid4(),
        embedding_model_name="same-name",
        llm=config,
        history=[],
        embedding=config,
        index_build_models=stored,
    )


def test_query_mismatch_fails_before_http_client_or_environment_fallback() -> None:
    service = RagService(cast(Any, object()), settings())
    turn = make_turn(
        snapshot(base_url="https://changed.example/v1"),
        {
            "version": 1,
            "EMBEDDING": identity(snapshot()),
        },
    )
    with pytest.raises(ApiError) as caught:
        service._select_query_embedding_provider(turn)
    assert caught.value.code == "INDEX_EMBEDDING_MISMATCH"


@pytest.mark.asyncio
async def test_query_compatible_frozen_identity_survives_key_rotation() -> None:
    config = snapshot(api_key=SecretStr("new-key"))
    service = RagService(cast(Any, object()), settings())
    provider = service._select_query_embedding_provider(
        make_turn(
            config,
            {
                "version": 1,
                "EMBEDDING": identity(snapshot()),
            },
        )
    )
    try:
        assert provider_identity(provider, "EMBEDDING", settings()) == identity(config)
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_matching_model_reuses_vectors_and_preserves_fingerprint() -> None:
    connection = AsyncMock()
    old_doc = uuid4()
    connection.execute.side_effect = [
        SimpleNamespace(scalar_one_or_none=lambda: old_doc),
        SimpleNamespace(mappings=lambda: []),
        *[None] * 8,
    ]
    engine = MagicMock()
    engine.connect.return_value.__aenter__.return_value = connection
    engine.begin.return_value.__aenter__.return_value = connection
    pipeline = IndexPipeline(cast(AsyncEngine, engine), cast(Minio, object()), settings())
    pipeline._update_task = AsyncMock()  # type: ignore[method-assign]
    pipeline._update_document_status = AsyncMock()  # type: ignore[method-assign]
    pipeline._process_document = AsyncMock()  # type: ignore[method-assign]
    await pipeline._reuse_document(
        {"id": uuid4()},
        {
            "index_document_id": uuid4(),
            "document_id": uuid4(),
            "kb_id": uuid4(),
            "source_hash": "same-hash",
            "filename": "test.docx",
        },
        uuid4(),
        uuid4(),
        1,
        1,
    )
    pipeline._process_document.assert_not_awaited()
    lookup = connection.execute.call_args_list[0].args[1]
    assert lookup["model_fingerprint"] == pipeline.model_fingerprint
    writes = connection.execute.call_args_list
    assert any("dc.embedding" in str(call.args[0]) for call in writes)
    saved = json.loads(writes[-1].args[1]["metadata"])
    assert saved["build_mode"] == "REUSE"
    assert saved["model_fingerprint"] == pipeline.model_fingerprint


@pytest.mark.asyncio
async def test_reparse_resets_task_reuse_marker_before_processing() -> None:
    pipeline = IndexPipeline(cast(AsyncEngine, object()), cast(Minio, object()), settings())
    pipeline._update_task = AsyncMock()  # type: ignore[method-assign]
    pipeline._update_document_status = AsyncMock()  # type: ignore[method-assign]
    pipeline._download_object = AsyncMock(side_effect=ValueError("stop before parse"))  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="stop before parse"):
        await pipeline._process_document(
            {"id": uuid4()},
            {
                "index_document_id": uuid4(),
                "document_id": uuid4(),
                "filename": "test.docx",
                "minio_bucket": "test",
                "minio_object_key": "test",
            },
            uuid4(),
            1,
            1,
        )
    assert pipeline._update_task.call_args.kwargs["metadata"]["reused"] is False


@pytest.mark.parametrize("changed", [False, True])
@pytest.mark.asyncio
async def test_model_audit_does_not_mark_unchanged_form_identity_as_changed(changed: bool) -> None:
    model_id = uuid4()
    existing = {
        "id": model_id,
        "model_type": "EMBEDDING",
        "provider": "siliconflow",
        "model_name": "same-name",
        "base_url": "https://models.example/v1",
    }
    connection = AsyncMock()
    connection.execute.return_value.mappings = lambda: SimpleNamespace(
        one_or_none=lambda: existing,
        one=lambda: existing,
    )
    engine = MagicMock()
    engine.begin.return_value.__aenter__.return_value = connection
    repository = RagRepository(cast(AsyncEngine, engine), settings())
    repository._record_history = AsyncMock()  # type: ignore[method-assign]
    repository._public_model_row = lambda row: row  # type: ignore[method-assign]
    await repository.update_model_config(
        model_id,
        provider="siliconflow",
        model_name="same-name",
        base_url="https://changed.example/v1" if changed else existing["base_url"],
    )
    fields = repository._record_history.call_args.args[3]
    assert fields == (["base_url"] if changed else [])


def migration_statements(action: str) -> list[str]:
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0048_index_build_models.py"
    spec = importlib.util.spec_from_file_location("index_identity_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    statements: list[str] = []
    module.op = SimpleNamespace(execute=statements.append)
    getattr(module, action)()
    return statements


class _ReadinessRows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> _ReadinessRows:
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def __iter__(self) -> Any:
        return iter(self.rows)


class _ReadinessEngine:
    def __init__(self, results: list[list[dict[str, Any]]]) -> None:
        self.results = results

    def connect(self) -> _ReadinessEngine:
        return self

    async def __aenter__(self) -> _ReadinessEngine:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def execute(self, *_args: Any) -> _ReadinessRows:
        return _ReadinessRows(self.results.pop(0))


@pytest.mark.parametrize("compatible", [True, False])
@pytest.mark.asyncio
async def test_assistant_readiness_checks_endpoint_not_just_model_name(compatible: bool) -> None:
    model_id, index_id = uuid4(), uuid4()
    kb = {
        "status": "ENABLED",
        "rag_active_index_id": index_id,
        "support_active_index_id": index_id,
        "rag_index_status": "ACTIVE",
        "support_index_status": "ACTIVE",
        "profile_count": 1,
        "ready_profile_count": 1,
        "embedding_model_id": model_id,
        "embedding_model_name": "same-name",
        "build_models": {"version": 1, "EMBEDDING": identity(snapshot())},
    }
    models = [
        {
            "model_type": name,
            "provider": "fake",
            "model_name": "fake",
            "stored_key_configured": False,
        }
        for name in ["LLM", "RERANK"]
    ] + [
        {
            "id": model_id,
            "model_type": "EMBEDDING",
            "provider": "siliconflow",
            "model_name": "same-name",
            "stored_key_configured": True,
            "base_url": snapshot().base_url if compatible else "https://other.example/v1",
        }
    ]
    repository = GatewayRepository(cast(AsyncEngine, _ReadinessEngine([[kb], models])), settings())
    readiness = await repository.get_readiness(uuid4())
    assert readiness["rag"]["ready"] is compatible
    if not compatible:
        assert any("Embedding" in item for item in readiness["rag"]["blockers"])


@pytest_asyncio.fixture
async def identity_db() -> AsyncIterator[AsyncEngine]:
    url = os.getenv("SUPPORT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SUPPORT_TEST_DATABASE_URL is not configured")
    schema = "identity_test_" + uuid4().hex
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE TABLE knowledge_index (id uuid PRIMARY KEY, embedding_model_id uuid, "
                    "embedding_model_name text, status text, created_at timestamptz DEFAULT now())"
                )
            )
            await connection.execute(
                text(
                    "CREATE TABLE model_config_history (model_config_id uuid, "
                    "changed_fields jsonb, "
                    "created_at timestamptz DEFAULT now())"
                )
            )
            await connection.execute(
                text(
                    "CREATE TABLE model_config (id uuid PRIMARY KEY, provider text, "
                    "model_name text, "
                    "parameters jsonb DEFAULT '{}', base_url text, api_key_ciphertext bytea, "
                    "api_key_nonce bytea, api_key_key_version integer)"
                )
            )
            await connection.execute(
                text(
                    "CREATE TABLE index_document (id uuid PRIMARY KEY, index_id uuid, "
                    "document_id uuid, "
                    "source_hash text, status text, metadata jsonb, started_at timestamptz)"
                )
            )
            await connection.execute(
                text(
                    "CREATE TABLE task_record (id uuid PRIMARY KEY, status text, "
                    "stage text, progress int, "
                    "attempt int DEFAULT 0, started_at timestamptz, updated_at timestamptz, "
                    "metadata jsonb DEFAULT '{}')"
                )
            )
            for statement in migration_statements("upgrade"):
                await connection.execute(text(statement))
        yield engine
    finally:
        await engine.dispose()
        # Only this test's UUID-named schema; never drop development tables.
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.asyncio
async def test_migration_keeps_legacy_empty_and_downgrades_in_isolated_schema(
    identity_db: AsyncEngine,
) -> None:
    async with identity_db.begin() as connection:
        await connection.execute(
            text("INSERT INTO knowledge_index (id) VALUES (:id)"), {"id": uuid4()}
        )
        assert (
            await connection.execute(text("SELECT build_models FROM knowledge_index"))
        ).scalar_one() == {}
        for statement in migration_statements("downgrade"):
            await connection.execute(text(statement))
        assert (
            await connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name='knowledge_index' AND column_name='build_models'"
                )
            )
        ).scalar_one() == 0


@pytest.mark.parametrize("changed_fields, expected", [(["api_key"], True), (["base_url"], False)])
@pytest.mark.asyncio
async def test_legacy_audit_distinguishes_key_rotation_from_identity_change(
    identity_db: AsyncEngine,
    changed_fields: list[str],
    expected: bool,
) -> None:
    model_id = uuid4()
    async with identity_db.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO knowledge_index (id, embedding_model_id, created_at) "
                "VALUES (:id, :model_id, '2020-01-01')"
            ),
            {"id": uuid4(), "model_id": model_id},
        )
        await connection.execute(
            text(
                "INSERT INTO model_config_history (model_config_id, changed_fields) "
                "VALUES (:id, CAST(:fields AS jsonb))"
            ),
            {"id": model_id, "fields": json.dumps(changed_fields)},
        )
        result = await connection.execute(
            text("SELECT " + legacy_embedding_unchanged_sql("i") + " FROM knowledge_index i")
        )
        assert result.scalar_one() is expected


@pytest.mark.asyncio
async def test_worker_records_actual_model_after_queued_configuration_changed(
    identity_db: AsyncEngine,
) -> None:
    index_id, task_id, actual_model_id = uuid4(), uuid4(), uuid4()
    async with identity_db.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO knowledge_index "
                "(id, embedding_model_id, embedding_model_name, status) "
                "VALUES (:id, :model, 'queued-model', 'BUILDING')"
            ),
            {"id": index_id, "model": uuid4()},
        )
        await connection.execute(
            text("INSERT INTO task_record (id, status) VALUES (:id, 'RUNNING')"), {"id": task_id}
        )
    pipeline = IndexPipeline(
        identity_db,
        cast(Minio, object()),
        settings(),
        embedding_provider=FakeEmbeddingProvider(1024),
        embedding_model_id=actual_model_id,
    )
    await pipeline._record_build_models(index_id, task_id)
    async with identity_db.connect() as connection:
        row = (await connection.execute(text("SELECT * FROM knowledge_index"))).mappings().one()
        assert row["embedding_model_id"] == actual_model_id
        assert row["embedding_model_name"] == "deterministic-shake256"
        assert row["build_models"] == pipeline.build_models


@pytest.mark.parametrize("old_fingerprint", ["old-model", None])
@pytest.mark.asyncio
async def test_old_reuse_plan_reparses_when_actual_model_fingerprint_changed(
    identity_db: AsyncEngine,
    old_fingerprint: str | None,
) -> None:
    old_index, new_index, doc_id, old_doc, new_doc, task = [uuid4() for _ in range(6)]
    pipeline = IndexPipeline(identity_db, cast(Minio, object()), settings())
    async with identity_db.begin() as connection:
        for row_id, index_id, fingerprint in [
            (old_doc, old_index, old_fingerprint),
            (new_doc, new_index, "queued"),
        ]:
            await connection.execute(
                text(
                    "INSERT INTO index_document "
                    "(id, index_id, document_id, source_hash, status, metadata) "
                    "VALUES (:id, :index, :doc, 'hash', 'READY', CAST(:metadata AS jsonb))"
                ),
                {
                    "id": row_id,
                    "index": index_id,
                    "doc": doc_id,
                    "metadata": json.dumps(
                        {
                            "content_revision": content_revision_for_mime(None),
                            "model_fingerprint": fingerprint,
                        }
                    ),
                },
            )
        await connection.execute(
            text("INSERT INTO task_record (id, status) VALUES (:id, 'RUNNING')"), {"id": task}
        )
    pipeline._process_document = AsyncMock()  # type: ignore[method-assign]
    await pipeline._reuse_document(
        {"id": new_index},
        {
            "index_document_id": new_doc,
            "document_id": doc_id,
            "kb_id": uuid4(),
            "source_hash": "hash",
            "filename": "test.docx",
        },
        old_index,
        task,
        1,
        1,
    )
    pipeline._process_document.assert_awaited_once()


@pytest.mark.parametrize("compatible", [True, False])
@pytest.mark.asyncio
async def test_card_embedding_uses_same_index_identity_guard(
    identity_db: AsyncEngine,
    compatible: bool,
) -> None:
    model_id, index_id = uuid4(), uuid4()
    config = snapshot()
    async with identity_db.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO model_config (id, provider, model_name, base_url) "
                "VALUES (:id, 'siliconflow', 'same-name', :url)"
            ),
            {
                "id": model_id,
                "url": config.base_url if compatible else "https://changed.example/v1",
            },
        )
        await connection.execute(
            text(
                "INSERT INTO knowledge_index "
                "(id, embedding_model_id, embedding_model_name, build_models) "
                "VALUES (:id, :model, 'same-name', CAST(:models AS jsonb))"
            ),
            {
                "id": index_id,
                "model": model_id,
                "models": json.dumps({"version": 1, "EMBEDDING": identity(config)}),
            },
        )
    repository = DocumentCardRepository(
        identity_db, settings(siliconflow_api_key=SecretStr("test-key"))
    )
    if compatible:
        assert (await repository.embedding_snapshot(index_id)).base_url == config.base_url
    else:
        with pytest.raises(ApiError) as caught:
            await repository.embedding_snapshot(index_id)
        assert caught.value.code == "INDEX_EMBEDDING_MISMATCH"
