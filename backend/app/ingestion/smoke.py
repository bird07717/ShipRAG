from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from redis import Redis
from rq import Queue
from sqlalchemy import text

from app.core.config import get_settings
from app.core.resources import AppResources
from app.ingestion.pipeline import IndexPipeline
from app.ingestion.repository import IngestionRepository
from app.m0.fixtures import create_docx_fixture
from app.main import create_app
from app.tasks.ingestion import enqueue_index_build


async def _remove_prefix(resources: AppResources, bucket: str, prefix: str) -> None:
    objects = await asyncio.to_thread(
        lambda: list(resources.minio.list_objects(bucket, prefix=prefix, recursive=True))
    )
    for item in objects:
        await asyncio.to_thread(resources.minio.remove_object, bucket, item.object_name)


def _verify_and_remove_queued_job(settings_url: str, task_id: UUID) -> None:
    connection = Redis.from_url(settings_url)
    try:
        queue = Queue("index_build", connection=connection)
        job = queue.fetch_job(str(task_id))
        if job is None:
            raise RuntimeError("M2 build was not enqueued in RQ")
        queue.remove(job)
        job.delete(remove_from_queue=False)
    finally:
        connection.close()


async def run(
    provider: Literal["fake", "siliconflow"] = "fake",
    ocr_provider: Literal["fake", "siliconflow", "disabled"] = "fake",
    vision_provider: Literal["fake", "zhipu", "disabled"] = "fake",
) -> dict[str, object]:
    base_settings = get_settings()
    settings = base_settings.model_copy(
        update={
            "m2_embedding_provider": provider,
            "m4_ocr_provider": ocr_provider,
            "m4_vision_provider": vision_provider,
        }
    )
    resources = AppResources.create(settings)
    knowledge_id: UUID | None = None
    smoke_index_id: UUID | None = None
    try:
        headers: dict[str, str] = {}
        if settings.service_token is not None:
            headers["Authorization"] = f"Bearer {settings.service_token.get_secret_value()}"
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "m2-smoke.docx"
            create_docx_fixture(path)
            with TestClient(create_app(settings)) as client:
                knowledge_response = client.post(
                    f"{settings.api_prefix}/knowledge-bases",
                    json={
                        "name": f"m2-smoke-{uuid4()}",
                        "description": "M2 automated smoke fixture",
                    },
                    headers=headers,
                )
                knowledge_response.raise_for_status()
                knowledge_id = UUID(knowledge_response.json()["id"])
                upload_headers = {
                    **headers,
                    "Idempotency-Key": f"m2-smoke-{uuid4()}",
                }
                with path.open("rb") as docx_file:
                    upload_response = client.post(
                        f"{settings.api_prefix}/knowledge-bases/{knowledge_id}/documents",
                        headers=upload_headers,
                        files={
                            "file": (
                                "m2-smoke.docx",
                                docx_file,
                                "application/vnd.openxmlformats-officedocument."
                                "wordprocessingml.document",
                            )
                        },
                        data={"display_name": "M2 Smoke", "request_build": "false"},
                    )
                upload_response.raise_for_status()
                uploaded = upload_response.json()
                listed_response = client.get(
                    f"{settings.api_prefix}/knowledge-bases/{knowledge_id}/documents",
                    headers=headers,
                )
                listed_response.raise_for_status()
                if len(listed_response.json()) != 1:
                    raise RuntimeError("M2 document list did not contain the upload")

        repository = IngestionRepository(resources.database, settings)
        document_id = UUID(uploaded["document"]["id"])
        build = await repository.request_build(knowledge_id, "MANUAL")
        assert build.index_id is not None and build.task_id is not None
        smoke_index_id = build.index_id
        await asyncio.to_thread(enqueue_index_build, settings, build.index_id, build.task_id)
        await asyncio.to_thread(
            _verify_and_remove_queued_job,
            settings.redis_url.get_secret_value(),
            build.task_id,
        )
        pipeline = IndexPipeline(resources.database, resources.minio, settings)
        selected_provider = pipeline.embedding_provider.provider
        result = await pipeline.run(build.index_id, build.task_id)
        index = await repository.get_index(build.index_id)
        resolved_element_index, elements = await repository.list_elements(document_id, None)
        resolved_chunk_index, chunks = await repository.list_chunks(document_id, None)
        if index["status"] != "ACTIVE":
            raise RuntimeError("M2 smoke index was not activated")
        if resolved_element_index != build.index_id or resolved_chunk_index != build.index_id:
            raise RuntimeError("M2 smoke preview did not resolve the active index")
        if not any(element["element_type"] == "IMAGE" for element in elements):
            raise RuntimeError("M2 smoke did not persist an IMAGE element")
        image_element = next(element for element in elements if element["element_type"] == "IMAGE")
        expected_ocr_status = "SKIPPED" if ocr_provider == "disabled" else "READY"
        expected_vision_status = "SKIPPED" if vision_provider == "disabled" else "READY"
        if image_element["ocr_status"] != expected_ocr_status:
            raise RuntimeError("M4 smoke OCR status was not persisted")
        if image_element["vision_status"] != expected_vision_status:
            raise RuntimeError("M4 smoke Vision status was not persisted")
        if expected_ocr_status == "READY" and not image_element["ocr_text"]:
            raise RuntimeError("M4 smoke OCR text is empty")
        if expected_vision_status == "READY" and not image_element["vision_caption"]:
            raise RuntimeError("M4 smoke Vision caption is empty")
        image_asset_id = image_element["image_asset_id"]
        with TestClient(create_app(settings)) as client:
            asset_response = client.get(
                f"{settings.api_prefix}/image-assets/{image_asset_id}", headers=headers
            )
            asset_response.raise_for_status()
            asset_body = asset_response.json()
            if "minio_bucket" in asset_body or "minio_object_key" in asset_body:
                raise RuntimeError("M4 image metadata API exposed storage internals")
            content_response = client.get(
                f"{settings.api_prefix}/image-assets/{image_asset_id}/content",
                headers=headers,
            )
            content_response.raise_for_status()
            if not content_response.content or not content_response.headers[
                "content-type"
            ].startswith("image/"):
                raise RuntimeError("M4 image content API did not return an image")
        if not any(chunk["chunk_type"] == "MIXED" for chunk in chunks):
            raise RuntimeError("M2 smoke did not persist a MIXED chunk")
        if not all(chunk["embedding_ready"] for chunk in chunks):
            raise RuntimeError("M2 smoke found a chunk without embedding")
        return {
            "status": "passed",
            "knowledge_id": str(knowledge_id),
            "index_id": str(build.index_id),
            "document_count": result.document_count,
            "element_count": result.element_count,
            "chunk_count": result.chunk_count,
            "embedding_dimension": settings.embedding_dimension,
            "embedding_provider": selected_provider,
            "ocr_provider": pipeline.ocr_provider.provider,
            "vision_provider": pipeline.vision_provider.provider,
            "ocr_status": image_element["ocr_status"],
            "vision_status": image_element["vision_status"],
            "image_element_present": True,
            "mixed_chunk_present": True,
            "image_asset_api_verified": True,
            "rq_enqueue_verified": True,
        }
    finally:
        if knowledge_id is not None:
            async with resources.database.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE knowledge_base SET active_index_id = NULL WHERE id = :knowledge_id"
                    ),
                    {"knowledge_id": knowledge_id},
                )
                await connection.execute(
                    text("DELETE FROM knowledge_base WHERE id = :knowledge_id"),
                    {"knowledge_id": knowledge_id},
                )
            await _remove_prefix(
                resources,
                settings.minio_document_bucket,
                f"knowledge-bases/{knowledge_id}/",
            )
            if smoke_index_id is not None:
                await _remove_prefix(
                    resources,
                    settings.minio_image_bucket,
                    f"indexes/{smoke_index_id}/",
                )
        await resources.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the M2 ingestion end-to-end smoke test")
    parser.add_argument(
        "--provider",
        choices=("fake", "siliconflow"),
        default="fake",
        help="Embedding provider used by the temporary build",
    )
    parser.add_argument(
        "--ocr-provider",
        choices=("fake", "siliconflow", "disabled"),
        default="fake",
    )
    parser.add_argument("--vision-provider", choices=("fake", "zhipu", "disabled"), default="fake")
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(run(args.provider, args.ocr_provider, args.vision_provider)),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
