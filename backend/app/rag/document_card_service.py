"""Application service for RAG document-card generation and review."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.common.errors import ApiError
from app.core.config import Settings
from app.ingestion.embedding import EmbeddingError, EmbeddingProvider, create_embedding_provider
from app.ingestion.models import EmbeddingInput
from app.rag.document_card_repository import DocumentCardRepository
from app.rag.document_cards import (
    DOCUMENT_CARD_SCHEMA_VERSION,
    DocumentCardGenerator,
    DocumentCardPayload,
    build_routing_text,
    content_fingerprint,
)
from app.rag.llm import LlmError, LlmProvider, create_llm_provider
from app.support.repository import SupportRepository
from app.tasks.maintenance import enqueue_maintenance_task
from app.tasks.repository import TaskRecordRepository


class DocumentCardService:
    def __init__(self, repository: DocumentCardRepository, settings: Settings) -> None:
        self.repository = repository
        self.settings = settings

    async def list_cards(
        self, knowledge_id: UUID, *, index_id: UUID | None = None
    ) -> dict[str, Any]:
        index = await self.repository.resolve_index(knowledge_id, index_id)
        cards = await self.repository.list_cards(knowledge_id, UUID(str(index["id"])))
        readiness = await self.repository.readiness(knowledge_id, UUID(str(index["id"])))
        return {
            "knowledge_id": knowledge_id,
            "index_id": index["id"],
            "index_version": index["version"],
            "cards": cards,
            "readiness": readiness,
        }

    async def get_card(
        self,
        knowledge_id: UUID,
        document_id: UUID,
        *,
        index_id: UUID | None = None,
    ) -> dict[str, Any] | None:
        index = await self.repository.resolve_index(knowledge_id, index_id)
        return await self.repository.get_card(knowledge_id, UUID(str(index["id"])), document_id)

    async def readiness(
        self, knowledge_id: UUID, *, index_id: UUID | None = None
    ) -> dict[str, Any]:
        index = await self.repository.resolve_index(knowledge_id, index_id)
        return await self.repository.readiness(knowledge_id, UUID(str(index["id"])))

    async def request_generation(
        self,
        knowledge_id: UUID,
        *,
        index_id: UUID | None = None,
        document_id: UUID | None = None,
        replace_confirmed: bool = False,
    ) -> dict[str, Any]:
        index = await self.repository.resolve_index(knowledge_id, index_id)
        resolved_index_id = UUID(str(index["id"]))
        if document_id is not None:
            await self.repository.generation_documents(knowledge_id, resolved_index_id, document_id)
        metadata = {
            "knowledge_id": str(knowledge_id),
            "index_id": str(resolved_index_id),
            "index_version": int(index["version"]),
            "document_id": str(document_id) if document_id else None,
            "replace_confirmed": replace_confirmed,
        }
        async with self.repository.engine.begin() as connection:
            task_id = await TaskRecordRepository.create_rag_document_card_task_in_connection(
                connection,
                kb_id=knowledge_id,
                index_id=resolved_index_id,
                document_id=document_id,
                metadata=metadata,
            )
            if task_id is None:
                active = (
                    (
                        await connection.execute(
                            text(
                                """
                                SELECT id, status FROM task_record
                                WHERE index_id = :index_id
                                  AND task_type = 'RAG_DOCUMENT_CARD_GENERATE'
                                  AND status IN ('QUEUED', 'RUNNING')
                                ORDER BY created_at DESC LIMIT 1
                                """
                            ),
                            {"index_id": resolved_index_id},
                        )
                    )
                    .mappings()
                    .one()
                )
                return {
                    "task_id": active["id"],
                    "task_type": "RAG_DOCUMENT_CARD_GENERATE",
                    "status": active["status"],
                    "replayed": True,
                }
        assert task_id is not None
        try:
            await asyncio.to_thread(enqueue_maintenance_task, self.settings, task_id)
        except Exception as exc:
            await TaskRecordRepository(self.repository.engine).fail(task_id, exc)
            raise ApiError("UPSTREAM_UNAVAILABLE", "异步任务队列不可用", 503) from exc
        return {
            "task_id": task_id,
            "task_type": "RAG_DOCUMENT_CARD_GENERATE",
            "status": "QUEUED",
            "replayed": False,
        }

    async def execute_generation_task(
        self, task: dict[str, Any], task_records: TaskRecordRepository
    ) -> dict[str, Any]:
        task_id = UUID(str(task["id"]))
        metadata = dict(task.get("metadata") or {})
        knowledge_id = UUID(str(metadata.get("knowledge_id") or task["kb_id"]))
        index_id = UUID(str(metadata.get("index_id") or task["index_id"]))
        document_id = UUID(str(task["document_id"])) if task.get("document_id") else None
        replace_confirmed = bool(metadata.get("replace_confirmed", False))
        documents = await self.repository.generation_documents(knowledge_id, index_id, document_id)
        existing = {
            str(card["document_id"]): card
            for card in await self.repository.list_cards(knowledge_id, index_id)
        }
        selected: list[dict[str, Any]] = []
        skipped_count = 0
        protected_count = 0
        for document in documents:
            card = existing.get(str(document["document_id"]))
            fingerprint = content_fingerprint(document)
            if card and card["review_status"] == "CONFIRMED" and not replace_confirmed:
                skipped_count += 1
                protected_count += 1
                continue
            if (
                document_id is None
                and card
                and card["generation_status"] == "READY"
                and card["content_fingerprint"] == fingerprint
                and int(card["schema_version"]) == DOCUMENT_CARD_SCHEMA_VERSION
            ):
                skipped_count += 1
                continue
            selected.append(document)
        await self.repository.prepare_generation(
            knowledge_id=knowledge_id,
            index_id=index_id,
            document_ids=[UUID(str(item["document_id"])) for item in selected],
            replace_confirmed=replace_confirmed,
        )

        llm: LlmProvider | None = None
        embedding: EmbeddingProvider | None = None
        setup_error: Exception | None = None
        try:
            llm_snapshot = await SupportRepository(self.repository.engine, self.settings).get_llm()
            embedding_snapshot = await self.repository.embedding_snapshot(index_id)
            llm = create_llm_provider(self.settings, llm_snapshot)
            embedding = create_embedding_provider(self.settings, embedding_snapshot)
        except (ApiError, LlmError, EmbeddingError) as exc:
            setup_error = exc

        completed = 0
        failed = 0
        cancelled = False
        failures: list[dict[str, str]] = []
        generator = DocumentCardGenerator(self.settings)
        concurrency = max(1, min(self.settings.profile_generation_concurrency, len(selected) or 1))
        semaphore = asyncio.Semaphore(concurrency)
        stop_requested = False

        async def _process(document: dict[str, Any]) -> None:
            nonlocal completed, failed, cancelled, stop_requested, protected_count, skipped_count
            async with semaphore:
                if stop_requested or await task_records.status(task_id) != "RUNNING":
                    stop_requested = True
                    cancelled = True
                    return
                current_id = UUID(str(document["document_id"]))
                current_card = existing.get(str(current_id))
                try:
                    if setup_error is not None:
                        raise setup_error
                    assert llm is not None and embedding is not None
                    payload, snapshot = await generator.generate(document, llm)
                    vectors = await embedding.embed(
                        [EmbeddingInput(text=build_routing_text(payload))]
                    )
                    if len(vectors) != 1 or len(vectors[0]) != self.settings.embedding_dimension:
                        raise EmbeddingError("Embedding 返回维度不匹配")
                    snapshot["embedding_provider"] = embedding.provider
                    snapshot["embedding_model_name"] = embedding.model_name
                    saved = await self.repository.save_generated(
                        knowledge_id=knowledge_id,
                        index_id=index_id,
                        document_id=current_id,
                        payload=payload,
                        embedding=vectors[0],
                        fingerprint=content_fingerprint(document),
                        generator_snapshot=snapshot,
                        replace_confirmed=replace_confirmed,
                        expected_revision=(int(current_card["revision"]) if current_card else None),
                    )
                    if saved:
                        completed += 1
                    else:
                        # A reviewer confirmed the row while generation was in flight.
                        protected_count += 1
                        skipped_count += 1
                except Exception as exc:
                    failed += 1
                    code = str(getattr(exc, "code", type(exc).__name__))
                    message = str(getattr(exc, "message", None) or str(exc) or code)
                    await self.repository.record_failure(
                        knowledge_id=knowledge_id,
                        index_id=index_id,
                        document_id=current_id,
                        error_code=code,
                        error_message=message,
                    )
                    failures.append(
                        {
                            "document_id": str(current_id),
                            "error_code": code[:100],
                            "error_message": message[:200],
                        }
                    )
                await task_records.progress(
                    task_id,
                    stage="GENERATING_CARDS",
                    progress=5 + int((completed + failed) * 90 / max(1, len(selected))),
                    metadata={
                        "generated_count": completed,
                        "failed_count": failed,
                        "selected_count": len(selected),
                    },
                )

        try:
            await asyncio.gather(*(_process(document) for document in selected))
        finally:
            if llm is not None:
                await llm.aclose()
            if embedding is not None:
                await embedding.aclose()
        result = {
            "knowledge_id": str(knowledge_id),
            "index_id": str(index_id),
            "document_count": len(documents),
            "selected_count": len(selected),
            "generated_count": completed,
            "failed_count": failed,
            "skipped_count": skipped_count,
            "protected_confirmed_count": protected_count,
            "cancelled": cancelled,
            "failures": failures,
        }
        if selected and failed == len(selected) and completed == 0 and not cancelled:
            # A batch with no usable output is operationally failed, even
            # though each document error was isolated and persisted.  Keep
            # counts in task metadata so the admin UI can explain the result.
            await task_records.progress(
                task_id,
                stage="GENERATING_CARDS",
                progress=95,
                metadata={
                    "document_count": len(documents),
                    "selected_count": len(selected),
                    "generated_count": 0,
                    "failed_count": failed,
                    "skipped_count": skipped_count,
                },
            )
            first_code = failures[0]["error_code"] if failures else "UNKNOWN"
            raise ApiError(
                "RAG_DOCUMENT_CARD_GENERATION_FAILED",
                f"RAG 文档理解全部生成失败（{failed} 份，首个错误：{first_code}）",
                502,
            )
        return result

    async def update_card(
        self,
        *,
        knowledge_id: UUID,
        index_id: UUID,
        document_id: UUID,
        expected_revision: int,
        payload: DocumentCardPayload,
    ) -> dict[str, Any]:
        await self.repository.resolve_index(knowledge_id, index_id)
        document = (
            await self.repository.generation_documents(knowledge_id, index_id, document_id)
        )[0]
        payload = DocumentCardPayload.model_validate(
            {
                **payload.model_dump(mode="json"),
                "has_images": int(document.get("image_count") or 0) > 0,
            }
        )
        provider: EmbeddingProvider | None = None
        try:
            provider = create_embedding_provider(
                self.settings, await self.repository.embedding_snapshot(index_id)
            )
            vectors = await provider.embed([EmbeddingInput(text=build_routing_text(payload))])
        except (ApiError, EmbeddingError) as exc:
            if isinstance(exc, ApiError):
                raise
            raise ApiError("UPSTREAM_UNAVAILABLE", "卡片向量生成失败", 503) from exc
        finally:
            if provider is not None:
                await provider.aclose()
        if len(vectors) != 1 or len(vectors[0]) != self.settings.embedding_dimension:
            raise ApiError("UPSTREAM_INVALID_RESPONSE", "卡片向量维度不匹配", 502)
        return await self.repository.update_card(
            knowledge_id=knowledge_id,
            index_id=index_id,
            document_id=document_id,
            expected_revision=expected_revision,
            payload=payload,
            embedding=vectors[0],
            fingerprint=content_fingerprint(document),
        )

    async def review_card(
        self,
        *,
        knowledge_id: UUID,
        index_id: UUID,
        document_id: UUID,
        expected_revision: int,
        review_status: str,
    ) -> dict[str, Any]:
        await self.repository.resolve_index(knowledge_id, index_id)
        return await self.repository.review_card(
            knowledge_id=knowledge_id,
            index_id=index_id,
            document_id=document_id,
            expected_revision=expected_revision,
            review_status=review_status,
        )
