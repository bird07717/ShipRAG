from __future__ import annotations

import asyncio
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile, status

from app.api.dependencies import get_app_settings, get_resources
from app.api.schemas import (
    DocumentDeleteResponse,
    DocumentResponse,
    DocumentUploadResponse,
    GarbageCollectionResponse,
    ImageAssetResponse,
    IndexBuildRequest,
    IndexBuildResponse,
    IndexResponse,
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
    TaskResponse,
)
from app.core.config import Settings
from app.core.resources import AppResources
from app.ingestion.repository import IngestionRepository
from app.services.ingestion import IngestionService

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
    "/knowledge-bases/{knowledge_id}/indexes/gc",
    response_model=GarbageCollectionResponse,
    tags=["indexes"],
)
async def gc_indexes(
    knowledge_id: UUID,
    service: Annotated[IngestionService, Depends(get_ingestion_service)],
) -> dict[str, Any]:
    return await service.collect_garbage(kb_id=knowledge_id)


@router.delete(
    "/indexes/{index_id}",
    response_model=GarbageCollectionResponse,
    tags=["indexes"],
)
async def delete_index(
    index_id: UUID,
    service: Annotated[IngestionService, Depends(get_ingestion_service)],
) -> dict[str, Any]:
    return await service.delete_index(index_id)


@router.post(
    "/indexes/{index_id}/retry",
    response_model=IndexBuildResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["indexes"],
)
async def retry_index(
    index_id: UUID,
    service: Annotated[IngestionService, Depends(get_ingestion_service)],
    activate_on_success: Annotated[bool, Query()] = True,
) -> dict[str, Any]:
    build = await service.retry_build(index_id, activate_on_success=activate_on_success)
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


@router.get("/tasks/{task_id}", response_model=TaskResponse, tags=["tasks"])
async def get_task(
    task_id: UUID,
    repository: Annotated[IngestionRepository, Depends(get_ingestion_repository)],
) -> dict[str, Any]:
    return await repository.get_task(task_id)
