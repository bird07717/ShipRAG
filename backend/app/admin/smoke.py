from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
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


def _remove_queued_job(redis_url: str, task_id: UUID) -> None:
    connection = Redis.from_url(redis_url)
    try:
        queue = Queue("index_build", connection=connection)
        job = queue.fetch_job(str(task_id))
        if job is None:
            raise RuntimeError("M6 management build was not enqueued")
        queue.remove(job)
        job.delete(remove_from_queue=False)
    finally:
        connection.close()


async def _remove_prefix(resources: AppResources, bucket: str, prefix: str) -> None:
    objects = await asyncio.to_thread(
        lambda: list(resources.minio.list_objects(bucket, prefix=prefix, recursive=True))
    )
    for item in objects:
        await asyncio.to_thread(resources.minio.remove_object, bucket, item.object_name)


async def run() -> dict[str, Any]:
    settings = get_settings().model_copy(
        update={
            "m2_embedding_provider": "fake",
            "m3_llm_provider": "fake",
            "m4_ocr_provider": "fake",
            "m4_vision_provider": "fake",
            "m5_rerank_provider": "fake",
        }
    )
    resources = AppResources.create(settings)
    knowledge_id: UUID | None = None
    index_ids: list[UUID] = []
    headers: dict[str, str] = {}
    if settings.service_token is not None:
        headers["Authorization"] = f"Bearer {settings.service_token.get_secret_value()}"
    try:
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "m6-smoke.docx"
            create_docx_fixture(path)
            with TestClient(create_app(settings)) as client:
                knowledge_response = client.post(
                    f"{settings.api_prefix}/knowledge-bases",
                    headers=headers,
                    json={"name": f"m6-smoke-{uuid4()}", "description": "M6 acceptance"},
                )
                knowledge_response.raise_for_status()
                knowledge_id = UUID(knowledge_response.json()["id"])
                with path.open("rb") as source:
                    upload_response = client.post(
                        f"{settings.api_prefix}/knowledge-bases/{knowledge_id}/documents",
                        headers={**headers, "Idempotency-Key": f"m6-upload-{uuid4()}"},
                        files={
                            "file": (
                                path.name,
                                source,
                                "application/vnd.openxmlformats-officedocument."
                                "wordprocessingml.document",
                            )
                        },
                        data={"request_build": "false"},
                    )
                upload_response.raise_for_status()

        repository = IngestionRepository(resources.database, settings)
        first = await repository.request_build(knowledge_id, activate_on_success=True)
        assert first.index_id is not None and first.task_id is not None
        index_ids.append(first.index_id)
        await IndexPipeline(resources.database, resources.minio, settings).run(
            first.index_id, first.task_id
        )
        initial_kb = await repository.get_knowledge_base(knowledge_id)
        if initial_kb["active_index_id"] != first.index_id:
            raise RuntimeError("Initial index was not activated")

        with TestClient(create_app(settings)) as client:
            build_idempotency_key = f"m6-build-{uuid4()}"
            build_response = client.post(
                f"{settings.api_prefix}/knowledge-bases/{knowledge_id}/indexes/build",
                headers={**headers, "Idempotency-Key": build_idempotency_key},
                json={"reason": "MANUAL", "activate_on_success": False},
            )
            build_response.raise_for_status()
            build = build_response.json()
            replay_response = client.post(
                f"{settings.api_prefix}/knowledge-bases/{knowledge_id}/indexes/build",
                headers={**headers, "Idempotency-Key": build_idempotency_key},
                json={"reason": "MANUAL", "activate_on_success": False},
            )
            replay_response.raise_for_status()
            if replay_response.json() != build:
                raise RuntimeError("Index build idempotency replay changed the response")
            second_index_id = UUID(build["index_id"])
            second_task_id = UUID(build["task_id"])
            index_ids.append(second_index_id)
            await asyncio.to_thread(
                _remove_queued_job,
                settings.redis_url.get_secret_value(),
                second_task_id,
            )

        during_build = await repository.get_knowledge_base(knowledge_id)
        if during_build["active_index_id"] != first.index_id:
            raise RuntimeError("BUILDING snapshot changed the online index")
        await IndexPipeline(resources.database, resources.minio, settings).run(
            second_index_id, second_task_id
        )
        ready = await repository.get_index(second_index_id)
        after_validation = await repository.get_knowledge_base(knowledge_id)
        if ready["status"] != "READY" or after_validation["active_index_id"] != first.index_id:
            raise RuntimeError("Manual publication boundary was not preserved")

        with TestClient(create_app(settings)) as client:
            activate_response = client.post(
                f"{settings.api_prefix}/indexes/{second_index_id}/activate", headers=headers
            )
            activate_response.raise_for_status()
            indexes_response = client.get(
                f"{settings.api_prefix}/knowledge-bases/{knowledge_id}/indexes",
                headers=headers,
            )
            indexes_response.raise_for_status()
            tasks_response = client.get(
                f"{settings.api_prefix}/indexes/{second_index_id}/tasks", headers=headers
            )
            tasks_response.raise_for_status()
            playground_response = client.post(
                f"{settings.api_prefix}/rag/playground",
                headers=headers,
                json={
                    "knowledge_id": str(knowledge_id),
                    "question": "数据库默认端口是多少？",
                    "options": {
                        "vector_top_k": 5,
                        "bm25_top_k": 5,
                        "fusion_top_k": 5,
                        "rerank_top_n": 5,
                    },
                },
            )
            playground_response.raise_for_status()
            trace = playground_response.json()
            traces_response = client.get(
                f"{settings.api_prefix}/traces",
                headers=headers,
                params={"knowledge_id": str(knowledge_id), "mode": "PLAYGROUND"},
            )
            traces_response.raise_for_status()
            models_response = client.get(f"{settings.api_prefix}/models", headers=headers)
            models_response.raise_for_status()

        final_kb = await repository.get_knowledge_base(knowledge_id)
        first_index = await repository.get_index(first.index_id)
        if final_kb["active_index_id"] != second_index_id:
            raise RuntimeError("Manual activation did not update the Active pointer")
        if first_index["status"] != "DEPRECATED":
            raise RuntimeError("Prior Active index was not deprecated")
        if trace["mode"] != "PLAYGROUND" or trace["status"] != "COMPLETED":
            raise RuntimeError("Playground did not produce a completed trace")
        if trace["index_id"] != str(second_index_id):
            raise RuntimeError("Playground did not freeze the newly Active index")
        if not trace["retrieval_result"].get("fusion_candidates"):
            raise RuntimeError("Playground trace is missing retrieval diagnostics")
        if not traces_response.json():
            raise RuntimeError("Trace list did not expose the Playground run")
        if not tasks_response.json() or len(indexes_response.json()) != 2:
            raise RuntimeError("Index management views are incomplete")
        if any("api_key_ciphertext" in model for model in models_response.json()):
            raise RuntimeError("Model API exposed secret storage fields")

        failed = await repository.request_build(knowledge_id, activate_on_success=True)
        assert failed.index_id is not None and failed.task_id is not None
        index_ids.append(failed.index_id)
        await repository.mark_build_enqueue_failed(failed.index_id, failed.task_id)
        after_failed_build = await repository.get_knowledge_base(knowledge_id)
        if after_failed_build["active_index_id"] != second_index_id:
            raise RuntimeError("Failed build disturbed the online Active index")
        return {
            "status": "passed",
            "knowledge_id": str(knowledge_id),
            "old_index_status": first_index["status"],
            "new_index_status": activate_response.json()["status"],
            "online_during_build": str(during_build["active_index_id"]),
            "manual_ready_preserved_old_active": True,
            "build_idempotency_verified": True,
            "atomic_switch_verified": True,
            "failed_build_preserved_active": True,
            "playground_trace_id": trace["trace_id"],
            "playground_index_id": trace["index_id"],
            "playground_status": trace["status"],
            "trace_list_verified": True,
            "model_secret_fields_hidden": True,
        }
    finally:
        if knowledge_id is not None:
            async with resources.database.begin() as connection:
                await connection.execute(
                    text("UPDATE knowledge_base SET active_index_id = NULL WHERE id = :id"),
                    {"id": knowledge_id},
                )
                await connection.execute(
                    text("DELETE FROM knowledge_base WHERE id = :id"), {"id": knowledge_id}
                )
            await _remove_prefix(
                resources,
                settings.minio_document_bucket,
                f"knowledge-bases/{knowledge_id}/",
            )
        for index_id in index_ids:
            await _remove_prefix(
                resources,
                settings.minio_image_bucket,
                f"indexes/{index_id}/",
            )
        await resources.close()


def main() -> None:
    print(json.dumps(asyncio.run(run()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
