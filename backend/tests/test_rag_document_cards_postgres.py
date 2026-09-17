from __future__ import annotations

import hashlib
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.common.errors import ApiError
from app.core.config import Settings
from app.gateway.repository import GatewayRepository
from app.ingestion.embedding import FakeEmbeddingProvider
from app.ingestion.repository import IngestionRepository
from app.rag.document_card_repository import DocumentCardRepository
from app.rag.document_card_service import DocumentCardService
from app.rag.document_cards import DocumentCardPayload, content_fingerprint
from app.rag.llm import FakeLlmProvider
from app.rag.models import ModelSnapshot
from app.rag.repository import RagRepository
from app.support.repository import SupportRepository
from app.tasks.repository import TaskRecordRepository

pytestmark = pytest.mark.asyncio


def _database_url() -> str:
    url = os.getenv("SUPPORT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SUPPORT_TEST_DATABASE_URL is not configured")
    return url


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=SecretStr(_database_url()),
        redis_url=SecretStr("redis://:password@127.0.0.1:1/0"),
        m2_embedding_provider="fake",
    )


@asynccontextmanager
async def _fixture() -> AsyncIterator[tuple[AsyncEngine, UUID, UUID, list[UUID]]]:
    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    knowledge_id, index_id = uuid4(), uuid4()
    document_ids = [uuid4(), uuid4()]
    try:
        async with engine.begin() as connection:
            model_id = await connection.scalar(
                text("SELECT id FROM model_config WHERE model_type = 'EMBEDDING' LIMIT 1")
            )
            await connection.execute(
                text("INSERT INTO knowledge_base (id,name,status) VALUES (:id,:name,'ENABLED')"),
                {"id": knowledge_id, "name": f"rag-card-{knowledge_id}"},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO knowledge_index (
                        id,kb_id,version,status,embedding_model_id,embedding_model_name,
                        embedding_dimension,build_reason
                    ) VALUES (:id,:kb,1,'ACTIVE',:model,'test-embedding',1024,'INITIAL')
                    """
                ),
                {"id": index_id, "kb": knowledge_id, "model": model_id},
            )
            await connection.execute(
                text("UPDATE knowledge_base SET active_index_id=:idx WHERE id=:kb"),
                {"idx": index_id, "kb": knowledge_id},
            )
            for position, document_id in enumerate(document_ids, 1):
                digest = hashlib.sha256(str(document_id).encode()).hexdigest()
                index_document_id = uuid4()
                await connection.execute(
                    text(
                        """
                        INSERT INTO document_source (
                            id,kb_id,filename,display_name,minio_bucket,minio_object_key,
                            file_hash,file_size,mime_type
                        ) VALUES (
                            :id,:kb,:filename,:name,'bucket',:key,:hash,1,
                            'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                        )
                        """
                    ),
                    {
                        "id": document_id,
                        "kb": knowledge_id,
                        "filename": f"document-{position}.docx",
                        "name": f"文档 {position}",
                        "key": f"rag-card/{document_id}/source.docx",
                        "hash": digest,
                    },
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO index_document (id,index_id,document_id,source_hash,status)
                        VALUES (:id,:idx,:doc,:hash,'READY')
                        """
                    ),
                    {"id": index_document_id, "idx": index_id, "doc": document_id, "hash": digest},
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO document_element (
                            id,index_id,index_document_id,document_id,element_type,
                            sequence_no,content,section_path
                        ) VALUES (:id,:idx,:idoc,:doc,'TEXT',1,:content,'["操作"]')
                        """
                    ),
                    {
                        "id": uuid4(),
                        "idx": index_id,
                        "idoc": index_document_id,
                        "doc": document_id,
                        "content": f"文档 {position} 的配置步骤",
                    },
                )
        yield engine, knowledge_id, index_id, document_ids
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM knowledge_base WHERE id=:id"), {"id": knowledge_id}
            )
        await engine.dispose()


def _payload(summary: str = "配置设备的操作步骤") -> DocumentCardPayload:
    return DocumentCardPayload(
        document_type="PROCEDURE",
        summary=summary,
        components=["VAU"],
        tasks=["配置设备"],
        applicable_questions=["如何配置设备"],
        key_topics=["网络"],
        has_procedure=True,
    )


async def _save(
    repository: DocumentCardRepository,
    knowledge_id: UUID,
    index_id: UUID,
    document_id: UUID,
) -> None:
    document = (await repository.generation_documents(knowledge_id, index_id, document_id))[0]
    saved = await repository.save_generated(
        knowledge_id=knowledge_id,
        index_id=index_id,
        document_id=document_id,
        payload=_payload(),
        embedding=[0.0] * 1024,
        fingerprint=content_fingerprint(document),
        generator_snapshot={"provider": "fake", "model_name": "fake"},
        replace_confirmed=False,
    )
    assert saved is True


async def test_card_review_concurrency_rename_and_confirmed_protection() -> None:
    async with _fixture() as (engine, knowledge_id, index_id, document_ids):
        repository = DocumentCardRepository(engine, _settings())
        document_id = document_ids[0]
        await _save(repository, knowledge_id, index_id, document_id)
        card = await repository.get_card(knowledge_id, index_id, document_id)
        assert card is not None
        routing_before = card["routing_text"]

        confirmed = await repository.review_card(
            knowledge_id=knowledge_id,
            index_id=index_id,
            document_id=document_id,
            expected_revision=card["revision"],
            review_status="CONFIRMED",
        )
        with pytest.raises(ApiError) as conflict:
            await repository.review_card(
                knowledge_id=knowledge_id,
                index_id=index_id,
                document_id=document_id,
                expected_revision=card["revision"],
                review_status="REJECTED",
            )
        assert conflict.value.code == "RAG_DOCUMENT_CARD_UPDATE_CONFLICT"

        document = (await repository.generation_documents(knowledge_id, index_id, document_id))[0]
        overwritten = await repository.save_generated(
            knowledge_id=knowledge_id,
            index_id=index_id,
            document_id=document_id,
            payload=_payload("新的自动结果"),
            embedding=[0.1] * 1024,
            fingerprint=content_fingerprint(document),
            generator_snapshot={"provider": "fake"},
            replace_confirmed=False,
            expected_revision=confirmed["revision"],
        )
        assert overwritten is False
        current = await repository.get_card(knowledge_id, index_id, document_id)
        assert current is not None
        assert current["review_status"] == "CONFIRMED"
        assert current["revision"] == confirmed["revision"]

        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE document_source SET display_name='新名称' WHERE id=:id"),
                {"id": document_id},
            )
        renamed = await repository.get_card(knowledge_id, index_id, document_id)
        assert renamed is not None
        assert renamed["document_name"] == "新名称"
        assert renamed["routing_text"] == routing_before

        replaced = await repository.save_generated(
            knowledge_id=knowledge_id,
            index_id=index_id,
            document_id=document_id,
            payload=_payload("显式覆盖结果"),
            embedding=[0.2] * 1024,
            fingerprint=content_fingerprint(document),
            generator_snapshot={"provider": "fake"},
            replace_confirmed=True,
            expected_revision=confirmed["revision"],
        )
        assert replaced is True
        replacement = await repository.get_card(knowledge_id, index_id, document_id)
        assert replacement is not None
        assert replacement["review_status"] == "AUTO"
        assert replacement["card_payload"]["summary"] == "显式覆盖结果"
        stale_worker_write = await repository.save_generated(
            knowledge_id=knowledge_id,
            index_id=index_id,
            document_id=document_id,
            payload=_payload("过期任务结果"),
            embedding=[0.3] * 1024,
            fingerprint=content_fingerprint(document),
            generator_snapshot={"provider": "fake"},
            replace_confirmed=True,
            expected_revision=confirmed["revision"],
        )
        assert stale_worker_write is False


async def test_readiness_and_document_soft_delete_remove_cards() -> None:
    async with _fixture() as (engine, knowledge_id, index_id, document_ids):
        settings = _settings()
        repository = DocumentCardRepository(engine, settings)
        await _save(repository, knowledge_id, index_id, document_ids[0])
        readiness = await repository.readiness(knowledge_id, index_id)
        assert readiness["expected_count"] == 2
        assert readiness["ready_count"] == 1
        assert readiness["missing_count"] == 1
        assert readiness["shadow_ready"] is False

        await IngestionRepository(engine, settings).delete_document(
            document_ids[0], request_build=False
        )
        assert await repository.get_card(knowledge_id, index_id, document_ids[0]) is None
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM rag_document_card WHERE document_id=:id"),
                    {"id": document_ids[0]},
                )
                == 0
            )


async def test_assistant_gate_requires_every_active_document_card_to_be_confirmed() -> None:
    async with _fixture() as (engine, knowledge_id, index_id, document_ids):
        settings = _settings()
        cards = DocumentCardRepository(engine, settings)
        gateway = GatewayRepository(engine, settings)

        assert await gateway._assistant_rag_understanding_blockers(knowledge_id) == [
            "RAG 文档理解未全部确认：已确认 0/2 份；缺失 2 份"
        ]

        for document_id in document_ids:
            await _save(cards, knowledge_id, index_id, document_id)
        assert await gateway._assistant_rag_understanding_blockers(knowledge_id) == [
            "RAG 文档理解未全部确认：已确认 0/2 份；待人工确认 2 份"
        ]

        for document_id in document_ids:
            card = await cards.get_card(knowledge_id, index_id, document_id)
            assert card is not None
            await cards.review_card(
                knowledge_id=knowledge_id,
                index_id=index_id,
                document_id=document_id,
                expected_revision=card["revision"],
                review_status="CONFIRMED",
            )
        assert await gateway._assistant_rag_understanding_blockers(knowledge_id) == []


async def test_online_card_search_is_scoped_and_excludes_rejected_cards() -> None:
    async with _fixture() as (engine, knowledge_id, index_id, document_ids):
        settings = _settings()
        cards = DocumentCardRepository(engine, settings)
        for position, document_id in enumerate(document_ids):
            document = (await cards.generation_documents(knowledge_id, index_id, document_id))[0]
            payload = _payload("VAU 图像配置步骤" if position == 0 else "PDC 接线检查步骤")
            payload = payload.model_copy(
                update={
                    "components": ["VAU"] if position == 0 else ["PDC"],
                    "tasks": ["配置图像"] if position == 0 else ["检查接线"],
                    "applicable_questions": (
                        ["如何配置 VAU 图像"] if position == 0 else ["如何检查 PDC 接线"]
                    ),
                }
            )
            embedding = [0.0] * 1024
            embedding[position] = 1.0
            assert await cards.save_generated(
                knowledge_id=knowledge_id,
                index_id=index_id,
                document_id=document_id,
                payload=payload,
                embedding=embedding,
                fingerprint=content_fingerprint(document),
                generator_snapshot={"provider": "fake"},
                replace_confirmed=False,
            )

        repository = RagRepository(engine, settings)
        query_embedding = [0.0] * 1024
        query_embedding[0] = 1.0
        vector = await repository.document_card_vector_search(
            knowledge_id=knowledge_id,
            index_id=index_id,
            embedding=query_embedding,
            limit=5,
        )
        lexical = await repository.document_card_bm25_search(
            knowledge_id=knowledge_id,
            index_id=index_id,
            query="VAU 图像配置",
            limit=5,
        )
        assert vector[0].document_id == document_ids[0]
        assert lexical[0].document_id == document_ids[0]

        current = await cards.get_card(knowledge_id, index_id, document_ids[0])
        assert current is not None
        await cards.review_card(
            knowledge_id=knowledge_id,
            index_id=index_id,
            document_id=document_ids[0],
            expected_revision=current["revision"],
            review_status="REJECTED",
        )
        after_reject = await repository.document_card_vector_search(
            knowledge_id=knowledge_id,
            index_id=index_id,
            embedding=query_embedding,
            limit=5,
        )
        assert all(item.document_id != document_ids[0] for item in after_reject)


async def test_index_gc_foreign_key_cascades_cards() -> None:
    async with _fixture() as (engine, knowledge_id, index_id, document_ids):
        repository = DocumentCardRepository(engine, _settings())
        await _save(repository, knowledge_id, index_id, document_ids[0])
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE knowledge_base SET active_index_id=NULL WHERE id=:id"),
                {"id": knowledge_id},
            )
            await connection.execute(
                text("DELETE FROM knowledge_index WHERE id=:id"), {"id": index_id}
            )
            count = await connection.scalar(
                text("SELECT count(*) FROM rag_document_card WHERE index_id=:id"),
                {"id": index_id},
            )
        assert count == 0


async def test_card_generation_task_is_single_flight_per_index() -> None:
    async with _fixture() as (engine, knowledge_id, index_id, document_ids):
        async with engine.begin() as connection:
            first = await TaskRecordRepository.create_rag_document_card_task_in_connection(
                connection,
                kb_id=knowledge_id,
                index_id=index_id,
                document_id=document_ids[0],
            )
            second = await TaskRecordRepository.create_rag_document_card_task_in_connection(
                connection,
                kb_id=knowledge_id,
                index_id=index_id,
                document_id=document_ids[1],
            )
        assert first is not None
        assert second is None
        records = TaskRecordRepository(engine)
        assert await records.request_cancel(first) is True
        async with engine.begin() as connection:
            third = await TaskRecordRepository.create_rag_document_card_task_in_connection(
                connection,
                kb_id=knowledge_id,
                index_id=index_id,
                document_id=None,
            )
        assert third is not None
        await records.request_cancel(third)


async def test_generation_task_creates_auto_cards_and_shadow_readiness(monkeypatch) -> None:
    async with _fixture() as (engine, knowledge_id, index_id, document_ids):
        settings = _settings()
        repository = DocumentCardRepository(engine, settings)
        task_id = uuid4()
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO task_record (
                        id,task_type,status,stage,kb_id,index_id,metadata
                    ) VALUES (
                        :id,'RAG_DOCUMENT_CARD_GENERATE','RUNNING','GENERATING_CARDS',
                        :kb,:idx,CAST(:metadata AS jsonb)
                    )
                    """
                ),
                {
                    "id": task_id,
                    "kb": knowledge_id,
                    "idx": index_id,
                    "metadata": json.dumps(
                        {"knowledge_id": str(knowledge_id), "index_id": str(index_id)}
                    ),
                },
            )

        async def fake_llm_snapshot(_self):
            return ModelSnapshot(provider="fake", model_name="fake-card-model", parameters={})

        monkeypatch.setattr(SupportRepository, "get_llm", fake_llm_snapshot)
        monkeypatch.setattr(
            "app.rag.document_card_service.create_llm_provider",
            lambda _settings, _snapshot: FakeLlmProvider(
                completions=[_payload().model_dump_json()] * len(document_ids)
            ),
        )
        monkeypatch.setattr(
            "app.rag.document_card_service.create_embedding_provider",
            lambda _settings, _snapshot: FakeEmbeddingProvider(1024),
        )
        monkeypatch.setattr(
            repository,
            "embedding_snapshot",
            lambda _index_id: _async_value(
                ModelSnapshot(provider="fake", model_name="fake-embedding", parameters={})
            ),
        )
        task = await TaskRecordRepository(engine).get(task_id)
        assert task is not None

        result = await DocumentCardService(repository, settings).execute_generation_task(
            task, TaskRecordRepository(engine)
        )

        assert result["generated_count"] == 2
        assert result["failed_count"] == 0
        cards = await repository.list_cards(knowledge_id, index_id)
        assert len(cards) == 2
        assert {card["review_status"] for card in cards} == {"AUTO"}
        readiness = await repository.readiness(knowledge_id, index_id)
        assert readiness["shadow_ready"] is True
        assert readiness["ready"] is False


async def test_generation_task_reports_an_all_document_failure(monkeypatch) -> None:
    async with _fixture() as (engine, knowledge_id, index_id, document_ids):
        settings = _settings()
        repository = DocumentCardRepository(engine, settings)
        task_id = uuid4()
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO task_record (
                        id,task_type,status,stage,kb_id,index_id,metadata
                    ) VALUES (
                        :id,'RAG_DOCUMENT_CARD_GENERATE','RUNNING','GENERATING_CARDS',
                        :kb,:idx,CAST(:metadata AS jsonb)
                    )
                    """
                ),
                {
                    "id": task_id,
                    "kb": knowledge_id,
                    "idx": index_id,
                    "metadata": json.dumps(
                        {"knowledge_id": str(knowledge_id), "index_id": str(index_id)}
                    ),
                },
            )

        async def fake_llm_snapshot(_self):
            return ModelSnapshot(provider="fake", model_name="fake-card-model", parameters={})

        monkeypatch.setattr(SupportRepository, "get_llm", fake_llm_snapshot)
        monkeypatch.setattr(
            "app.rag.document_card_service.create_llm_provider",
            lambda _settings, _snapshot: FakeLlmProvider(
                completions=["not-json"] * (len(document_ids) * 2)
            ),
        )
        monkeypatch.setattr(
            "app.rag.document_card_service.create_embedding_provider",
            lambda _settings, _snapshot: FakeEmbeddingProvider(1024),
        )
        monkeypatch.setattr(
            repository,
            "embedding_snapshot",
            lambda _index_id: _async_value(
                ModelSnapshot(provider="fake", model_name="fake-embedding", parameters={})
            ),
        )
        records = TaskRecordRepository(engine)
        task = await records.get(task_id)
        assert task is not None

        with pytest.raises(ApiError) as exc_info:
            await DocumentCardService(repository, settings).execute_generation_task(task, records)

        assert exc_info.value.code == "RAG_DOCUMENT_CARD_GENERATION_FAILED"
        current = await records.get(task_id)
        assert current is not None
        assert current["metadata"]["generated_count"] == 0
        assert current["metadata"]["failed_count"] == len(document_ids)
        cards = await repository.list_cards(knowledge_id, index_id)
        assert {card["generation_status"] for card in cards} == {"FAILED"}


async def test_generation_retry_uses_the_revision_of_each_selected_document(monkeypatch) -> None:
    async with _fixture() as (engine, knowledge_id, index_id, _document_ids):
        settings = _settings()
        repository = DocumentCardRepository(engine, settings)
        documents = await repository.generation_documents(knowledge_id, index_id)
        failed_id = UUID(str(documents[0]["document_id"]))
        ready_id = UUID(str(documents[-1]["document_id"]))
        await repository.prepare_generation(
            knowledge_id=knowledge_id,
            index_id=index_id,
            document_ids=[failed_id],
            replace_confirmed=False,
        )
        await repository.record_failure(
            knowledge_id=knowledge_id,
            index_id=index_id,
            document_id=failed_id,
            error_code="STRUCTURED_OUTPUT_INVALID",
            error_message="格式错误",
        )
        await _save(repository, knowledge_id, index_id, ready_id)
        task_id = uuid4()
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO task_record (
                        id,task_type,status,stage,kb_id,index_id,metadata
                    ) VALUES (
                        :id,'RAG_DOCUMENT_CARD_GENERATE','RUNNING','GENERATING_CARDS',
                        :kb,:idx,CAST(:metadata AS jsonb)
                    )
                    """
                ),
                {
                    "id": task_id,
                    "kb": knowledge_id,
                    "idx": index_id,
                    "metadata": json.dumps(
                        {"knowledge_id": str(knowledge_id), "index_id": str(index_id)}
                    ),
                },
            )

        async def fake_llm_snapshot(_self):
            return ModelSnapshot(provider="fake", model_name="fake-card-model", parameters={})

        monkeypatch.setattr(SupportRepository, "get_llm", fake_llm_snapshot)
        monkeypatch.setattr(
            "app.rag.document_card_service.create_llm_provider",
            lambda _settings, _snapshot: FakeLlmProvider(
                completions=[_payload().model_dump_json()]
            ),
        )
        monkeypatch.setattr(
            "app.rag.document_card_service.create_embedding_provider",
            lambda _settings, _snapshot: FakeEmbeddingProvider(1024),
        )
        monkeypatch.setattr(
            repository,
            "embedding_snapshot",
            lambda _index_id: _async_value(
                ModelSnapshot(provider="fake", model_name="fake-embedding", parameters={})
            ),
        )
        records = TaskRecordRepository(engine)
        task = await records.get(task_id)
        assert task is not None

        result = await DocumentCardService(repository, settings).execute_generation_task(
            task, records
        )

        assert result["selected_count"] == 1
        assert result["generated_count"] == 1
        assert result["failed_count"] == 0
        assert result["protected_confirmed_count"] == 0
        retried = await repository.get_card(knowledge_id, index_id, failed_id)
        assert retried is not None
        assert retried["generation_status"] == "READY"


async def _async_value(value):
    return value
