from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from redis import Redis
from rq import Queue

from app.core.config import Settings, get_settings
from app.core.resources import AppResources
from app.document_profiles.repository import DocumentProfileRepository
from app.document_profiles.service import DocumentProfileService
from app.ingestion.embedding import create_embedding_provider
from app.ingestion.image_understanding import (
    create_ocr_provider,
    create_vision_provider,
)
from app.ingestion.model_config import load_build_models
from app.ingestion.pipeline import IndexBuildResult, IndexPipeline
from app.ingestion.repository import IngestionRepository
from app.tasks.repository import TaskRecordRepository

logger = logging.getLogger(__name__)


def enqueue_index_build(settings: Settings, index_id: UUID, task_id: UUID) -> None:
    connection = Redis.from_url(settings.redis_url.get_secret_value())
    try:
        queue = Queue("index_build", connection=connection)
        queue.enqueue(
            process_index_task,
            str(index_id),
            str(task_id),
            job_id=str(task_id),
            job_timeout=settings.rq_index_job_timeout_seconds,
            result_ttl=86_400,
            failure_ttl=604_800,
        )
    finally:
        connection.close()


async def _submit_post_build_profile(
    settings: Settings,
    resources: AppResources,
    result: IndexBuildResult,
    task_id: UUID,
) -> None:
    """Generate pending document attributes after a successful RAG build.

    Profile generation is best-effort: it never creates an index-bound
    candidate and a queue/model failure never fails the completed RAG build.
    """
    index = await IngestionRepository(resources.database, settings).get_index(result.index_id)
    knowledge_id = UUID(str(index["kb_id"]))
    try:
        submission = await DocumentProfileService(
            DocumentProfileRepository(resources.database), settings
        ).request_generation(knowledge_id)
        # The index task is already terminal when this follow-up is queued.
        # Preserve the relation so clients that were tracking the RAG build
        # can continue polling the independent Profile task and refresh the
        # review workspace when it settles.
        await TaskRecordRepository(resources.database).merge_metadata(
            task_id,
            {
                "profile_task_id": str(submission["task_id"]),
                "profile_task_type": str(submission["task_type"]),
            },
        )
    except Exception:
        logger.exception(
            "Could not submit document Profile generation for index %s", result.index_id
        )


async def _submit_post_build_rag_cards(
    settings: Settings,
    resources: AppResources,
    result: IndexBuildResult,
    task_id: UUID,
) -> None:
    """Queue index-bound RAG cards without affecting build success."""
    try:
        from app.rag.document_card_repository import DocumentCardRepository
        from app.rag.document_card_service import DocumentCardService

        index = await IngestionRepository(resources.database, settings).get_index(result.index_id)
        knowledge_id = UUID(str(index["kb_id"]))
        service = DocumentCardService(
            DocumentCardRepository(resources.database, settings), settings
        )
        # 卡片跨版本复用：构建成功后先把 REUSE 文档的卡片行从上一版
        # 索引复制过来（review_status 原样保留）。生成任务现有的指纹跳
        # 过逻辑会把这些行视为已就绪，只为新增/变更文档调用 LLM。复制
        # 源用 pipeline 运行时快照的 prev_index_id，与摄取层 REUSE 判定
        # 同源，构建期间人工切换活动指针不会造成错位。
        if result.prev_index_id is not None:
            copied = await service.copy_cards(result.prev_index_id, result.index_id)
            if copied:
                logger.info(
                    "Reused %d RAG document cards from index %s into %s",
                    copied,
                    result.prev_index_id,
                    result.index_id,
                )
        submission = await service.request_generation(knowledge_id, index_id=result.index_id)
        await TaskRecordRepository(resources.database).merge_metadata(
            task_id,
            {
                "rag_document_card_task_id": str(submission["task_id"]),
                "rag_document_card_task_type": str(submission["task_type"]),
            },
        )
    except Exception:
        logger.exception("Could not submit RAG document cards for index %s", result.index_id)


async def _run_index_task(index_id: UUID, task_id: UUID) -> dict[str, int | str]:
    settings = get_settings()
    resources = AppResources.create(settings)
    try:
        # Snapshot build-bound model configs once: admin edits during the
        # build must not leak into it (same snapshot semantics as begin_turn).
        configs = await load_build_models(resources.database, settings)
        pipeline = IndexPipeline(
            resources.database,
            resources.minio,
            settings,
            embedding_provider=create_embedding_provider(settings, configs["EMBEDDING"].snapshot),
            ocr_provider=create_ocr_provider(settings, configs["OCR"].snapshot),
            vision_provider=create_vision_provider(settings, configs["VISION"].snapshot),
            model_sources={model_type: config.source for model_type, config in configs.items()},
        )
        try:
            result = await pipeline.run(index_id, task_id)
        except Exception:
            # ADR-0024：构建失败不再自动重试。rebuild_required 已由
            # _mark_failed 重新置位，管理台以“待重建”提示，由管理员
            # 决定处置（修复文档后重建，或删除失败索引）。
            raise
        if result.cancelled:
            return {
                "index_id": str(result.index_id),
                "cancelled": True,
            }
        await _submit_post_build_profile(settings, resources, result, task_id)
        await _submit_post_build_rag_cards(settings, resources, result, task_id)
        return {
            "index_id": str(result.index_id),
            "cancelled": False,
            "document_count": result.document_count,
            "element_count": result.element_count,
            "chunk_count": result.chunk_count,
        }
    finally:
        await resources.close()


def process_index_task(index_id: str, task_id: str) -> dict[str, int | str]:
    return asyncio.run(_run_index_task(UUID(index_id), UUID(task_id)))
