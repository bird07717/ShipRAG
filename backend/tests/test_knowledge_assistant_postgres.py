from __future__ import annotations

import asyncio
import importlib.util
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.api.assistant import welcome
from app.assistant.configuration import AssistantConfiguration
from app.common.errors import ApiError
from app.core.config import Settings
from app.gateway.repository import GatewayRepository
from app.ingestion.repository import IngestionRepository

QUESTIONS = ["如何检查设备电源", "如何查看系统状态", "如何执行设备自检"]


@pytest_asyncio.fixture
async def configuration():
    url = os.getenv("SUPPORT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SUPPORT_TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url)
    ids = [uuid4(), uuid4()]
    async with engine.begin() as conn:
        for kb_id in ids:
            await conn.execute(
                text("INSERT INTO knowledge_base (id, name) VALUES (:id, :name)"),
                {"id": kb_id, "name": f"assistant-config-test-{kb_id}"},
            )
    gateway = GatewayRepository(
        engine, Settings(_env_file=None, app_env="test", chat_execution_mode="legacy_rag")
    )
    try:
        yield AssistantConfiguration(gateway), gateway, ids
    finally:
        async with engine.begin() as conn:
            for kb_id in ids:
                await conn.execute(text("DELETE FROM knowledge_base WHERE id = :id"), {"id": kb_id})
        await engine.dispose()


async def test_private_defaults_and_actual_readiness(configuration: Any) -> None:
    service, gateway, ids = configuration
    result = await service.get(ids[0])
    assert result["suggestions"] == []
    assert result["assistant_online"] is False
    assert result["ready"] is False
    assert result["blockers"]
    await service.update(ids[0], revision=0, suggestions=QUESTIONS)
    with pytest.raises(ApiError) as failure:
        await service.update(ids[0], revision=1, online=True)
    assert failure.value.code == "KNOWLEDGE_BASE_NOT_READY"
    assert (await service.get(ids[0]))["revision"] == 1
    with pytest.raises(ApiError) as failure:
        await welcome(ids[0], gateway)
    assert failure.value.status_code == 404


async def test_multikb_edits_publication_and_offline_boundary(
    configuration: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, gateway, ids = configuration
    check = AsyncMock(return_value=[])
    monkeypatch.setattr(gateway, "assistant_runtime_blockers", check)
    await service.update(ids[0], revision=0, suggestions=QUESTIONS)
    other = ["如何升级另一设备", "如何更换另一设备", "如何检查另一设备"]
    await service.update(ids[1], revision=0, suggestions=other)
    for kb_id in ids:
        await service.update(kb_id, revision=1, online=True)
        assert isinstance(check.call_args.kwargs["connection"], AsyncConnection)
    assert (await welcome(ids[0], gateway))["suggestions"] == QUESTIONS
    assert (await welcome(ids[1], gateway))["suggestions"] == other
    new = ["如何重启设备系统", *QUESTIONS[1:]]
    await service.update(ids[0], revision=2, suggestions=new)
    assert (await welcome(ids[0], gateway))["suggestions"] == new
    with pytest.raises(ApiError) as failure:
        await service.update(ids[0], revision=3, suggestions=[])
    assert failure.value.code == "ASSISTANT_SUGGESTIONS_REQUIRED"
    check.return_value = ["模型不可用"]
    assert (await welcome(ids[0], gateway))["suggestions"] == []
    check.reset_mock()
    check.side_effect = AssertionError("offline must not check runtime")
    await service.update(ids[0], revision=3, online=False)
    check.assert_not_awaited()
    with pytest.raises(ApiError) as failure:
        await welcome(ids[0], gateway)
    assert failure.value.status_code == 404


async def test_concurrent_saves_conflict_without_cross_kb_writes(configuration: Any) -> None:
    service, _, ids = configuration
    results = await asyncio.gather(
        service.update(ids[0], revision=0, suggestions=QUESTIONS),
        service.update(ids[0], revision=0, suggestions=["如何升级设备系统"]),
        return_exceptions=True,
    )
    assert sum(isinstance(result, dict) for result in results) == 1
    failure = next(result for result in results if isinstance(result, ApiError))
    assert failure.code == "ASSISTANT_CONFIG_CONFLICT"
    assert (await service.get(ids[0]))["revision"] == 1
    assert (await service.get(ids[1]))["suggestions"] == []


async def test_public_description_update_is_atomic_and_does_not_create_builds(
    configuration: Any,
) -> None:
    _, gateway, ids = configuration
    repository = IngestionRepository(gateway.engine, Settings(_env_file=None, app_env="test"))
    before = await repository.get_knowledge_base(ids[0])
    async with gateway.engine.connect() as conn:
        task_count = (
            await conn.execute(
                text("SELECT count(*) FROM task_record WHERE kb_id = :id"), {"id": ids[0]}
            )
        ).scalar_one()
        index_count = (
            await conn.execute(
                text("SELECT count(*) FROM knowledge_index WHERE kb_id = :id"), {"id": ids[0]}
            )
        ).scalar_one()

    updated = await repository.update_knowledge_base_description(
        ids[0],
        description="VAU 图像配置和故障排查",
        expected_updated_at=before["updated_at"],
    )
    assert updated["description"] == "VAU 图像配置和故障排查"

    with pytest.raises(ApiError) as conflict:
        await repository.update_knowledge_base_description(
            ids[0],
            description="过期页面覆盖",
            expected_updated_at=before["updated_at"],
        )
    assert conflict.value.code == "KNOWLEDGE_BASE_UPDATE_CONFLICT"
    assert conflict.value.details["current_updated_at"] == updated["updated_at"].isoformat()

    async with gateway.engine.connect() as conn:
        assert (
            await conn.execute(
                text("SELECT count(*) FROM task_record WHERE kb_id = :id"), {"id": ids[0]}
            )
        ).scalar_one() == task_count
        assert (
            await conn.execute(
                text("SELECT count(*) FROM knowledge_index WHERE kb_id = :id"), {"id": ids[0]}
            )
        ).scalar_one() == index_count


async def test_offline_hides_existing_conversation_and_image(
    configuration: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, gateway, ids = configuration
    kb_id = ids[0]
    index_id, doc_id, idoc_id, element_id, asset_id, conv_id = [uuid4() for _ in range(6)]
    monkeypatch.setattr(gateway, "assistant_runtime_blockers", AsyncMock(return_value=[]))
    async with gateway.engine.begin() as conn:
        model_id = (
            await conn.execute(
                text("SELECT id FROM model_config WHERE model_type = 'EMBEDDING' LIMIT 1")
            )
        ).scalar_one()
        await conn.execute(
            text(
                "INSERT INTO knowledge_index (id, kb_id, version, status, embedding_model_id, "
                "embedding_model_name, embedding_dimension, build_reason) "
                "VALUES (:id, :kb, 1, 'ACTIVE', :model, 'test-model', 1024, 'INITIAL')"
            ),
            {"id": index_id, "kb": kb_id, "model": model_id},
        )
        await conn.execute(
            text(
                "INSERT INTO document_source (id, kb_id, filename, display_name, minio_bucket, "
                "minio_object_key, file_hash, file_size, mime_type) "
                "VALUES (:id, :kb, 'test.docx', '测试文档', 'test', :key, :hash, 0, 'test')"
            ),
            {"id": doc_id, "kb": kb_id, "key": str(doc_id), "hash": "0" * 64},
        )
        await conn.execute(
            text(
                "INSERT INTO index_document (id, index_id, document_id, source_hash, status) "
                "VALUES (:id, :idx, :doc, :hash, 'READY')"
            ),
            {"id": idoc_id, "idx": index_id, "doc": doc_id, "hash": "0" * 64},
        )
        await conn.execute(
            text(
                "INSERT INTO document_element (id, index_id, index_document_id, document_id, "
                "element_type, sequence_no) VALUES (:id, :idx, :idoc, :doc, 'IMAGE', 1)"
            ),
            {"id": element_id, "idx": index_id, "idoc": idoc_id, "doc": doc_id},
        )
        await conn.execute(
            text(
                "INSERT INTO image_asset (id, index_id, index_document_id, "
                "document_id, element_id, "
                "minio_bucket, minio_object_key, file_hash, mime_type, ocr_status, vision_status) "
                "VALUES (:id, :idx, :idoc, :doc, :element, 'test', :key, "
                ":hash, 'image/png', 'SKIPPED', 'SKIPPED')"
            ),
            {
                "id": asset_id,
                "idx": index_id,
                "idoc": idoc_id,
                "doc": doc_id,
                "element": element_id,
                "key": str(asset_id),
                "hash": "0" * 64,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO conversation (id, knowledge_id, user_id) "
                "VALUES (:id, :kb, 'config-user')"
            ),
            {"id": conv_id, "kb": kb_id},
        )
    await service.update(kb_id, revision=0, suggestions=QUESTIONS)
    await service.update(kb_id, revision=1, online=True)
    assert await gateway.get_assistant_conversation(
        conversation_id=conv_id, external_user_id="config-user"
    )
    assert await gateway.assistant_image_asset(knowledge_id=kb_id, image_asset_id=asset_id)
    with pytest.raises(ApiError):
        await gateway.assistant_image_asset(knowledge_id=ids[1], image_asset_id=asset_id)
    assert (
        await gateway.get_assistant_conversation(
            conversation_id=conv_id, external_user_id="another-user"
        )
        is None
    )
    await service.update(kb_id, revision=2, online=False)
    assert all(
        item["knowledge_id"] != kb_id for item in await gateway.list_assistant_knowledge_bases()
    )
    assert (
        await gateway.get_assistant_conversation(
            conversation_id=conv_id, external_user_id="config-user"
        )
        is None
    )
    assert (
        await gateway.list_assistant_conversations(
            knowledge_id=kb_id, external_user_id="config-user", limit=10
        )
        == []
    )
    with pytest.raises(ApiError) as failure:
        await gateway.assistant_image_asset(knowledge_id=kb_id, image_asset_id=asset_id)
    assert failure.value.status_code == 404
    with pytest.raises(ApiError) as failure:
        await gateway.ensure_assistant_knowledge_base_ready(knowledge_id=kb_id)
    assert failure.value.status_code == 404
    async with gateway.engine.connect() as conn:
        assert (
            await conn.execute(text("SELECT id FROM conversation WHERE id = :id"), {"id": conv_id})
        ).scalar_one() == conv_id


async def test_rejects_missing_deleting_and_incomplete_publication(configuration: Any) -> None:
    service, gateway, ids = configuration
    with pytest.raises(ApiError) as failure:
        await service.update(uuid4(), revision=0, suggestions=QUESTIONS)
    assert failure.value.status_code == 404
    with pytest.raises(ApiError) as failure:
        await service.update(ids[0], revision=0, online=True)
    assert failure.value.code == "ASSISTANT_SUGGESTIONS_REQUIRED"
    async with gateway.engine.begin() as conn:
        await conn.execute(
            text("UPDATE knowledge_base SET status = 'DELETING' WHERE id = :id"), {"id": ids[0]}
        )
    with pytest.raises(ApiError) as failure:
        await service.update(ids[0], revision=0, suggestions=QUESTIONS)
    assert failure.value.code == "KNOWLEDGE_BASE_DELETING"


async def test_0043_migration_preserves_online_only(configuration: Any) -> None:
    _, gateway, _ = configuration
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0043_assistant_suggestions.py"
    spec = importlib.util.spec_from_file_location("assistant_migration_0043", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # A temporary table shadows the real table for this transaction only.
    async with gateway.engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TEMP TABLE knowledge_base (id integer, assistant_online boolean) "
                "ON COMMIT DROP"
            )
        )
        await conn.execute(text("INSERT INTO knowledge_base VALUES (1, true), (2, false)"))

        def migrate(sync_connection: Any) -> None:
            with Operations.context(MigrationContext.configure(sync_connection)):
                module.upgrade()

        await conn.run_sync(migrate)
        rows = (
            (await conn.execute(text("SELECT * FROM knowledge_base ORDER BY id"))).mappings().all()
        )
        assert len(rows[0]["assistant_suggestions"]) == 3
        assert rows[1]["assistant_suggestions"] == []
        await conn.execute(
            text("INSERT INTO knowledge_base (id, assistant_online) VALUES (3, false)")
        )
        assert (
            await conn.execute(
                text("SELECT assistant_suggestions FROM knowledge_base WHERE id = 3")
            )
        ).scalar_one() == []

        def downgrade(sync_connection: Any) -> None:
            with Operations.context(MigrationContext.configure(sync_connection)):
                module.downgrade()

        await conn.run_sync(downgrade)
        assert (await conn.execute(text("SELECT count(*) FROM knowledge_base"))).scalar_one() == 3
