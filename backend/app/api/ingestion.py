from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile, status

from app.api.dependencies import get_app_settings, get_resources
from app.api.schemas import (
    DocumentDeleteResponse,
    DocumentProfileContextResponse,
    DocumentResponse,
    DocumentUpdate,
    DocumentUploadResponse,
    GarbageCollectionResponse,
    ImageAssetResponse,
    IndexBuildRequest,
    IndexBuildResponse,
    IndexResponse,
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
    TaskResponse,
    TaskSubmissionResponse,
)
from app.common.errors import ApiError
from app.core.config import Settings
from app.core.resources import AppResources
from app.document_profiles.repository import DocumentProfileRepository
from app.document_profiles.service import DocumentProfileService
from app.ingestion.repository import IngestionRepository
from app.releases.models import RagIndexRollbackRequest
from app.services.ingestion import IngestionService
from app.tasks.repository import TaskRecordRepository

router = APIRouter()


def get_ingestion_repository(
    settings: Annotated[Settings, Depends(get_app_settings)],
    resources: Annotated[AppResources, Depends(get_resources)],
) -> IngestionRepository:
    return IngestionRepository(resources.database, settings)


def get_ingestion_service(
    repository: Annotated[IngestionRepository, Depends(get_ingestion_repository)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    resources: Annotated[AppResources, Depends(get_resources)],
) -> IngestionService:
    return IngestionService(repository, resources.minio, settings)


def get_document_profile_repository(
    resources: Annotated[AppResources, Depends(get_resources)],
) -> DocumentProfileRepository:
    return DocumentProfileRepository(resources.database)


def get_document_profile_service(
    repository: Annotated[DocumentProfileRepository, Depends(get_document_profile_repository)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> DocumentProfileService:
    return DocumentProfileService(repository, settings)


@router.post(
    "/knowledge-bases",
    response_model=KnowledgeBaseResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["knowledge-bases"],
)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    repository: Annotated[IngestionRepository, Depends(get_ingestion_repository)],
) -> dict[str, Any]:
    return await repository.create_knowledge_base(payload.name.strip(), payload.description)


@router.get(
    "/knowledge-bases",
    response_model=list[KnowledgeBaseResponse],
    tags=["knowledge-bases"],
)
async def list_knowledge_bases(
    repository: Annotated[IngestionRepository, Depends(get_ingestion_repository)],
) -> list[dict[str, Any]]:
    return await repository.list_knowledge_bases()


@router.get(
    "/knowledge-bases/{knowledge_id}",
    response_model=KnowledgeBaseResponse,
    tags=["knowledge-bases"],
)
async def get_knowledge_base(
    knowledge_id: UUID,
    repository: Annotated[IngestionRepository, Depends(get_ingestion_repository)],
) -> dict[str, Any]:
    return await repository.get_knowledge_base(knowledge_id)


@router.delete(
    "/knowledge-bases/{knowledge_id}",
    response_model=TaskSubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["knowledge-bases"],
)
async def delete_knowledge_base(
    knowledge_id: UUID,
    service: Annotated[IngestionService, Depends(get_ingestion_service)],
) -> dict[str, Any]:
    return await service.request_knowledge_base_deletion(knowledge_id)


@router.post(
    "/knowledge-bases/{knowledge_id}/profiles/rebuild",
    response_model=TaskSubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["knowledge-bases"],
)
async def rebuild_knowledge_profiles(
    knowledge_id: UUID,
    service: Annotated[DocumentProfileService, Depends(get_document_profile_service)],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=200)
    ] = None,
) -> dict[str, Any]:
    """Compatibility endpoint for the old "rebuild drafts" control.

    It now queues document attribute regeneration and never consults a
    candidate release, so a published historical release cannot block it.
    """
    del idempotency_key  # durable task de-duplication is knowledge-base scoped
    return await service.request_generation(knowledge_id)


@router.post(
    "/knowledge-bases/{knowledge_id}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["documents"],
)
async def upload_document(
    knowledge_id: UUID,
    service: Annotated[IngestionService, Depends(get_ingestion_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
    file: Annotated[UploadFile, File()],
    display_name: Annotated[str | None, Form(max_length=500)] = None,
    request_build: Annotated[bool, Form()] = True,
) -> dict[str, Any]:
    try:
        return await service.upload_document(
            knowledge_id=knowledge_id,
            upload=file,
            display_name=display_name,
            request_build=request_build,
            idempotency_key=idempotency_key,
        )
    finally:
        await file.close()


@router.get(
    "/knowledge-bases/{knowledge_id}/documents",
    response_model=list[DocumentResponse],
    tags=["documents"],
)
async def list_documents(
    knowledge_id: UUID,
    repository: Annotated[IngestionRepository, Depends(get_ingestion_repository)],
) -> list[dict[str, Any]]:
    return await repository.list_documents(knowledge_id)


@router.get("/documents/{document_id}", response_model=DocumentResponse, tags=["documents"])
async def get_document(
    document_id: UUID,
    repository: Annotated[IngestionRepository, Depends(get_ingestion_repository)],
) -> dict[str, Any]:
    return await repository.get_document(document_id)


@router.patch("/documents/{document_id}", response_model=DocumentResponse, tags=["documents"])
async def update_document(
    document_id: UUID,
    payload: DocumentUpdate,
    service: Annotated[IngestionService, Depends(get_ingestion_service)],
) -> dict[str, Any]:
    return await service.rename_document(document_id, payload.display_name)


@router.get(
    "/documents/{document_id}/profile-context",
    response_model=DocumentProfileContextResponse,
    tags=["documents"],
)
async def get_document_profile_context(
    document_id: UUID,
    repository: Annotated[DocumentProfileRepository, Depends(get_document_profile_repository)],
) -> dict[str, Any]:
    """Online and pending document Profile attributes for Documents."""
    context = await repository.document_context(document_id)
    if context is None:
        raise ApiError("DOCUMENT_NOT_FOUND", "文档不存在", 404)
    return context


@router.delete(
    "/documents/{document_id}",
    response_model=DocumentDeleteResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["documents"],
)
async def delete_document(
    document_id: UUID,
    service: Annotated[IngestionService, Depends(get_ingestion_service)],
    request_build: Annotated[bool, Query()] = True,
) -> dict[str, Any]:
    return await service.delete_document(document_id, request_build=request_build)


@router.get("/documents/{document_id}/elements", tags=["documents"])
async def list_elements(
    document_id: UUID,
    repository: Annotated[IngestionRepository, Depends(get_ingestion_repository)],
    index_id: Annotated[UUID | None, Query()] = None,
) -> dict[str, Any]:
    resolved, items = await repository.list_elements(document_id, index_id)
    return {"index_id": resolved, "items": items}


@router.get("/documents/{document_id}/index-results", tags=["documents"])
async def list_index_results(
    document_id: UUID,
    repository: Annotated[IngestionRepository, Depends(get_ingestion_repository)],
    index_id: Annotated[UUID | None, Query()] = None,
) -> dict[str, Any]:
    resolved, items = await repository.list_index_results(document_id, index_id)
    return {"index_id": resolved, "items": items}


@router.get("/documents/{document_id}/chunks", tags=["documents"])
async def list_chunks(
    document_id: UUID,
    repository: Annotated[IngestionRepository, Depends(get_ingestion_repository)],
    index_id: Annotated[UUID | None, Query()] = None,
) -> dict[str, Any]:
    resolved, items = await repository.list_chunks(document_id, index_id)
    return {"index_id": resolved, "items": items}


@router.get("/documents/{document_id}/parent-chunks", tags=["documents"])
async def list_parent_chunks(
    document_id: UUID,
    repository: Annotated[IngestionRepository, Depends(get_ingestion_repository)],
    index_id: Annotated[UUID | None, Query()] = None,
) -> dict[str, Any]:
    resolved, items = await repository.list_parent_chunks(document_id, index_id)
    return {"index_id": resolved, "items": items}


@router.get("/image-assets/{image_asset_id}", response_model=ImageAssetResponse, tags=["documents"])
async def get_image_asset(
    image_asset_id: UUID,
    repository: Annotated[IngestionRepository, Depends(get_ingestion_repository)],
) -> dict[str, Any]:
    asset = await repository.get_image_asset(image_asset_id)
    asset.pop("minio_bucket", None)
    asset.pop("minio_object_key", None)
    return asset


@router.get("/image-assets/{image_asset_id}/content", tags=["documents"])
async def get_image_asset_content(
    image_asset_id: UUID,
    repository: Annotated[IngestionRepository, Depends(get_ingestion_repository)],
    resources: Annotated[AppResources, Depends(get_resources)],
) -> Response:
    asset = await repository.get_image_asset(image_asset_id)

    def download() -> bytes:
        upstream = resources.minio.get_object(asset["minio_bucket"], asset["minio_object_key"])
        try:
            return bytes(upstream.read())
        finally:
            upstream.close()
            upstream.release_conn()

    content = await asyncio.to_thread(download)
    return Response(
        content,
        media_type=asset["mime_type"],
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/indexes/{index_id}", response_model=IndexResponse, tags=["indexes"])
async def get_index(
    index_id: UUID,
    repository: Annotated[IngestionRepository, Depends(get_ingestion_repository)],
) -> dict[str, Any]:
    return await repository.get_index(index_id)


@router.get(
    "/knowledge-bases/{knowledge_id}/indexes",
    response_model=list[IndexResponse],
    tags=["indexes"],
)
async def list_indexes(
    knowledge_id: UUID,
    repository: Annotated[IngestionRepository, Depends(get_ingestion_repository)],
) -> list[dict[str, Any]]:
    return await repository.list_indexes(knowledge_id)


@router.post(
    "/knowledge-bases/{knowledge_id}/indexes/build",
    response_model=IndexBuildResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["indexes"],
)
async def request_index_build(
    knowledge_id: UUID,
    payload: IndexBuildRequest,
    service: Annotated[IngestionService, Depends(get_ingestion_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> dict[str, Any]:
    build = await service.request_build(
        knowledge_id,
        reason=payload.reason,
        activate_on_success=payload.activate_on_success,
        idempotency_key=idempotency_key,
    )
    return {
        "requested": build.requested,
        "coalesced": build.coalesced,
        "index_id": build.index_id,
        "task_id": build.task_id,
        "rebuild_required": build.rebuild_required,
    }


@router.post(
    "/indexes/{index_id}/activate",
    response_model=IndexResponse,
    tags=["indexes"],
)
async def activate_index(
    index_id: UUID,
    repository: Annotated[IngestionRepository, Depends(get_ingestion_repository)],
) -> dict[str, Any]:
    return await repository.activate_index(index_id)


@router.post(
    "/knowledge-bases/{knowledge_id}/rag-index/rollback",
    response_model=IndexResponse,
    tags=["indexes"],
)
async def rollback_rag_index(
    knowledge_id: UUID,
    payload: RagIndexRollbackRequest,
    repository: Annotated[IngestionRepository, Depends(get_ingestion_repository)],
) -> dict[str, Any]:
    """Roll the RAG online index back to an explicitly chosen snapshot.

    Support pointers and frozen conversation versions are not touched; new
    sessions bind to the rollback target while old sessions keep their
    frozen snapshot.
    """
    return await repository.rollback_rag_index(knowledge_id, payload.target_index_id)


@router.post(
    "/knowledge-bases/{knowledge_id}/indexes/gc",
    response_model=GarbageCollectionResponse,
    tags=["indexes"],
)
async def gc_indexes(
    knowledge_id: UUID,
    service: Annotated[IngestionService, Depends(get_ingestion_service)],
) -> dict[str, Any]:
    return await service.collect_garbage(kb_id=knowledge_id)


@router.post("/gc", response_model=GarbageCollectionResponse, tags=["indexes"])
async def gc_all_indexes(
    service: Annotated[IngestionService, Depends(get_ingestion_service)],
) -> dict[str, Any]:
    """Run fleet-wide GC across every knowledge base.

    Same pipeline as the per-KB endpoint: expired conversations are purged
    first, then obsolete indexes are physically deleted with their MinIO
    image assets.  Runs inline so the response reports what was collected.
    """
    return await service.collect_garbage()


@router.delete(
    "/indexes/{index_id}",
    response_model=GarbageCollectionResponse,
    tags=["indexes"],
)
async def delete_index(
    index_id: UUID,
    service: Annotated[IngestionService, Depends(get_ingestion_service)],
    force: bool = False,
) -> dict[str, Any]:
    return await service.delete_index(index_id, force=force)


@router.post(
    "/indexes/{index_id}/retry",
    response_model=IndexBuildResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["indexes"],
)
async def retry_index(
    index_id: UUID,
    service: Annotated[IngestionService, Depends(get_ingestion_service)],
) -> dict[str, Any]:
    build = await service.retry_build(index_id, activate_on_success=False)
    return {
        "requested": build.requested,
        "coalesced": build.coalesced,
        "index_id": build.index_id,
        "task_id": build.task_id,
        "rebuild_required": build.rebuild_required,
    }


@router.get(
    "/indexes/{index_id}/tasks",
    response_model=list[TaskResponse],
    tags=["tasks"],
)
async def list_index_tasks(
    index_id: UUID,
    repository: Annotated[IngestionRepository, Depends(get_ingestion_repository)],
) -> list[dict[str, Any]]:
    return await repository.list_index_tasks(index_id)


@router.get(
    "/tasks",
    response_model=list[TaskResponse],
    tags=["tasks"],
)
async def list_tasks(
    repository: Annotated[IngestionRepository, Depends(get_ingestion_repository)],
    task_status: Annotated[
        Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"] | None,
        Query(alias="status"),
    ] = None,
    knowledge_id: Annotated[UUID | None, Query()] = None,
    created_from: Annotated[datetime | None, Query(alias="from")] = None,
    created_to: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    """Global task list across indexes; `status=FAILED` is the failure-review
    entry point that previously required knowing the index_id first."""
    return await repository.list_tasks(
        status=task_status,
        knowledge_id=knowledge_id,
        created_from=created_from,
        created_to=created_to,
        limit=limit,
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse, tags=["tasks"])
async def get_task(
    task_id: UUID,
    repository: Annotated[IngestionRepository, Depends(get_ingestion_repository)],
) -> dict[str, Any]:
    return await repository.get_task(task_id)


@router.post("/tasks/{task_id}/cancel", response_model=TaskResponse, tags=["tasks"])
async def cancel_task(
    task_id: UUID,
    repository: Annotated[IngestionRepository, Depends(get_ingestion_repository)],
    resources: Annotated[AppResources, Depends(get_resources)],
) -> dict[str, Any]:
    """Cooperatively cancel an active background task.

    Cancellation is a database state transition: the worker polls
    task_record.status between documents and stops scheduling new work, so
    in-flight documents finish and the task settles as CANCELLED.  Only
    DOCUMENT_PROFILE_GENERATE supports this today; other task types keep
    running and would misreport their real outcome.
    """
    task = await repository.get_task(task_id)
    if task["task_type"] != "DOCUMENT_PROFILE_GENERATE":
        raise ApiError("TASK_NOT_CANCELLABLE", "该任务类型暂不支持取消", 409)
    status = str(task["status"])
    if status == "CANCELLED":
        return task
    if status not in {"QUEUED", "RUNNING"}:
        raise ApiError("TASK_NOT_CANCELLABLE", "任务已结束，无法取消", 409)
    task_records = TaskRecordRepository(resources.database)
    await task_records.request_cancel(task_id)
    return await repository.get_task(task_id)
