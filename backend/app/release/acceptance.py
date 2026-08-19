from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import socket
import subprocess
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.config import PROJECT_ROOT, Settings, get_settings
from app.core.resources import AppResources
from app.m0.fixtures import create_docx_fixture


class ReleaseAcceptanceError(RuntimeError):
    pass


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[position], 2)


def _run_command(*command: str, timeout: float = 120) -> str:
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout.strip()


def _compose(*arguments: str, timeout: float = 120) -> str:
    return _run_command("docker", "compose", *arguments, timeout=timeout)


def _assert_port_available(port: int) -> None:
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise ReleaseAcceptanceError(f"M7 acceptance port {port} is already in use") from exc


def _start_process(
    command: list[str], environment: dict[str, str], log: Any
) -> subprocess.Popen[Any]:
    return subprocess.Popen(
        command,
        cwd=PROJECT_ROOT / "backend",
        env=environment,
        stdout=log,
        stderr=subprocess.STDOUT,
    )


def _stop_process(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


async def _remove_prefix(resources: AppResources, bucket: str, prefix: str) -> None:
    objects = await asyncio.to_thread(
        lambda: list(resources.minio.list_objects(bucket, prefix=prefix, recursive=True))
    )
    for item in objects:
        await asyncio.to_thread(resources.minio.remove_object, bucket, item.object_name)


async def _wait_for_http(client: httpx.AsyncClient, path: str, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            response = await client.get(path)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.25)
    raise ReleaseAcceptanceError(f"Service did not become available at {path}")


async def _wait_for_readiness(
    client: httpx.AsyncClient, *, ready: bool, timeout_seconds: float
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    expected_status = 200 if ready else 503
    while time.monotonic() < deadline:
        try:
            response = await client.get("/health/ready")
            if response.status_code == expected_status:
                return dict(response.json())
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.5)
    state = "ready" if ready else "not ready"
    raise ReleaseAcceptanceError(f"Readiness did not become {state}")


async def _wait_for_task(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    task_id: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = await client.get(f"/api/v1/tasks/{task_id}", headers=headers)
        response.raise_for_status()
        task = dict(response.json())
        if task["status"] == "SUCCEEDED":
            return task
        if task["status"] == "FAILED":
            raise ReleaseAcceptanceError(
                f"Index task failed at {task['stage']}: {task.get('error_code')}"
            )
        await asyncio.sleep(0.25)
    raise ReleaseAcceptanceError(f"Index task {task_id} timed out")


async def _run_load(
    operation: Callable[[int], Awaitable[None]],
    *,
    requests: int,
    concurrency: int,
) -> dict[str, float | int]:
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    errors = 0
    started = time.perf_counter()

    async def invoke(position: int) -> None:
        nonlocal errors
        async with semaphore:
            request_started = time.perf_counter()
            try:
                await operation(position)
            except Exception:
                errors += 1
            finally:
                latencies.append((time.perf_counter() - request_started) * 1000)

    await asyncio.gather(*(invoke(position) for position in range(requests)))
    elapsed = time.perf_counter() - started
    return {
        "requests": requests,
        "concurrency": concurrency,
        "errors": errors,
        "error_rate": round(errors / requests, 4),
        "throughput_rps": round(requests / elapsed, 2),
        "p50_ms": _percentile(latencies, 0.50),
        "p95_ms": _percentile(latencies, 0.95),
        "p99_ms": _percentile(latencies, 0.99),
    }


def _backup_restore(settings: Settings, knowledge_id: UUID) -> dict[str, Any]:
    container_id = _compose("ps", "-q", "postgres")
    if not container_id:
        raise ReleaseAcceptanceError("PostgreSQL container was not found")
    database_url = make_url(settings.database_url.get_secret_value())
    user = database_url.username or "rag"
    database = database_url.database or "rag_platform"
    restore_database = f"m7_restore_{uuid4().hex[:12]}"
    dump_path = f"/tmp/{restore_database}.dump"
    try:
        _run_command(
            "docker",
            "exec",
            container_id,
            "pg_dump",
            "-U",
            user,
            "-d",
            database,
            "-Fc",
            "-f",
            dump_path,
        )
        _run_command("docker", "exec", container_id, "createdb", "-U", user, restore_database)
        _run_command(
            "docker",
            "exec",
            container_id,
            "pg_restore",
            "-U",
            user,
            "-d",
            restore_database,
            "--exit-on-error",
            dump_path,
        )
        audit = _run_command(
            "docker",
            "exec",
            container_id,
            "psql",
            "-U",
            user,
            "-d",
            restore_database,
            "-Atc",
            (
                "SELECT version_num FROM alembic_version;"
                "SELECT count(*) FROM pg_extension WHERE extname IN ('vector','pg_search');"
                f"SELECT count(*) FROM knowledge_base WHERE id = '{knowledge_id}';"
            ),
        ).splitlines()
        if audit != ["0006", "2", "1"]:
            raise ReleaseAcceptanceError(f"Backup restore audit failed: {audit}")
        return {
            "format": "pg_dump-custom",
            "alembic_version": audit[0],
            "required_extensions": int(audit[1]),
            "knowledge_base_rows": int(audit[2]),
        }
    finally:
        subprocess.run(
            ["docker", "exec", container_id, "dropdb", "-U", user, "--if-exists", restore_database],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
        )
        subprocess.run(
            ["docker", "exec", container_id, "rm", "-f", dump_path],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
        )


async def run(
    *,
    port: int = 18007,
    load_requests: int = 200,
    load_concurrency: int = 32,
    rag_requests: int = 50,
    p95_limit_ms: float = 3_000,
    recovery_timeout: float = 90,
    task_timeout: float = 180,
) -> dict[str, Any]:
    _assert_port_available(port)
    base_settings = get_settings()
    settings = base_settings.model_copy(
        update={
            "app_env": "test",
            "m2_embedding_provider": "fake",
            "m3_llm_provider": "fake",
            "m4_ocr_provider": "fake",
            "m4_vision_provider": "fake",
            "m5_rerank_provider": "fake",
        }
    )
    if settings.service_token is None:
        raise ReleaseAcceptanceError("M7 requires SERVICE_TOKEN to verify the auth boundary")
    environment = {
        **os.environ,
        "APP_ENV": "test",
        "M2_EMBEDDING_PROVIDER": "fake",
        "M3_LLM_PROVIDER": "fake",
        "M4_OCR_PROVIDER": "fake",
        "M4_VISION_PROVIDER": "fake",
        "M5_RERANK_PROVIDER": "fake",
    }
    python = str(PROJECT_ROOT / ".venv" / "bin" / "python")
    headers = {"Authorization": f"Bearer {settings.service_token.get_secret_value()}"}
    base_url = f"http://127.0.0.1:{port}"
    resources = AppResources.create(settings)
    knowledge_id: UUID | None = None
    index_ids: list[UUID] = []
    backend_process: subprocess.Popen[Any] | None = None
    worker_process: subprocess.Popen[Any] | None = None
    stopped_services: set[str] = set()
    with tempfile.TemporaryDirectory() as temporary_directory:
        temp = Path(temporary_directory)
        backend_log = (temp / "backend.log").open("w")
        worker_log = (temp / "worker.log").open("w")
        try:
            backend_process = _start_process(
                [
                    python,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                environment,
                backend_log,
            )
            worker_process = _start_process([python, "-m", "app.worker"], environment, worker_log)
            async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
                await _wait_for_http(client, "/health/live", recovery_timeout)
                ready = await _wait_for_readiness(
                    client, ready=True, timeout_seconds=recovery_timeout
                )
                unauthorized = await client.get("/api/v1/system/info")
                if unauthorized.status_code != 401:
                    raise ReleaseAcceptanceError("Protected API accepted a request without token")
                authorized = await client.get("/api/v1/system/info", headers=headers)
                authorized.raise_for_status()

                knowledge_response = await client.post(
                    "/api/v1/knowledge-bases",
                    headers=headers,
                    json={"name": f"m7-release-{uuid4()}", "description": "M7 release fixture"},
                )
                knowledge_response.raise_for_status()
                knowledge_id = UUID(knowledge_response.json()["id"])
                document_path = temp / "m7-release.docx"
                create_docx_fixture(document_path)
                upload_response = await client.post(
                    f"/api/v1/knowledge-bases/{knowledge_id}/documents",
                    headers={**headers, "Idempotency-Key": f"m7-upload-{uuid4()}"},
                    files={
                        "file": (
                            document_path.name,
                            document_path.read_bytes(),
                            "application/vnd.openxmlformats-officedocument."
                            "wordprocessingml.document",
                        )
                    },
                )
                upload_response.raise_for_status()
                upload = upload_response.json()
                first_index_id = UUID(upload["build_request"]["index_id"])
                first_task_id = str(upload["build_request"]["task_id"])
                index_ids.append(first_index_id)
                first_task = await _wait_for_task(client, headers, first_task_id, task_timeout)

                document_id = str(upload["document"]["id"])
                elements_response, chunks_response = await asyncio.gather(
                    client.get(f"/api/v1/documents/{document_id}/elements", headers=headers),
                    client.get(f"/api/v1/documents/{document_id}/chunks", headers=headers),
                )
                elements_response.raise_for_status()
                chunks_response.raise_for_status()
                elements = elements_response.json()["items"]
                chunks = chunks_response.json()["items"]
                if not elements or not chunks:
                    raise ReleaseAcceptanceError("Worker did not persist Elements and Chunks")

                playground_response = await client.post(
                    "/api/v1/rag/playground",
                    headers=headers,
                    json={
                        "knowledge_id": str(knowledge_id),
                        "question": "数据库默认端口是多少？",
                        "options": {"vector_top_k": 5, "bm25_top_k": 5, "rerank_top_n": 5},
                    },
                )
                playground_response.raise_for_status()
                playground = playground_response.json()
                if playground["status"] != "COMPLETED" or not playground["sources"]:
                    raise ReleaseAcceptanceError("Playground integration did not complete")

                chat_response = await client.post(
                    "/api/v1/chat/stream",
                    headers={**headers, "Accept": "text/event-stream"},
                    json={"knowledge_id": str(knowledge_id), "question": "数据库端口？"},
                )
                chat_response.raise_for_status()
                event_names = [
                    line.removeprefix("event: ")
                    for line in chat_response.text.splitlines()
                    if line.startswith("event: ")
                ]
                if event_names[:2] != ["trace", "source"] or event_names[-1:] != ["done"]:
                    raise ReleaseAcceptanceError(f"Unexpected SSE event order: {event_names}")

                async def live_operation(_: int) -> None:
                    response = await client.get("/health/live")
                    response.raise_for_status()

                async def database_operation(_: int) -> None:
                    response = await client.get(
                        f"/api/v1/knowledge-bases/{knowledge_id}", headers=headers
                    )
                    response.raise_for_status()

                async def rag_operation(position: int) -> None:
                    response = await client.post(
                        "/api/v1/rag/playground",
                        headers=headers,
                        json={
                            "knowledge_id": str(knowledge_id),
                            "question": f"并发请求 {position}：数据库默认端口是多少？",
                            "options": {
                                "vector_top_k": 5,
                                "bm25_top_k": 5,
                                "fusion_top_k": 5,
                                "rerank_top_n": 5,
                            },
                        },
                    )
                    response.raise_for_status()
                    if response.json()["status"] != "COMPLETED":
                        raise ReleaseAcceptanceError("Concurrent RAG request did not complete")

                live_load, database_load, rag_load = await asyncio.gather(
                    _run_load(
                        live_operation,
                        requests=load_requests,
                        concurrency=load_concurrency,
                    ),
                    _run_load(
                        database_operation,
                        requests=load_requests,
                        concurrency=load_concurrency,
                    ),
                    _run_load(
                        rag_operation,
                        requests=rag_requests,
                        concurrency=min(load_concurrency, rag_requests),
                    ),
                )
                for name, metric in {
                    "liveness": live_load,
                    "database": database_load,
                    "rag": rag_load,
                }.items():
                    if metric["errors"] != 0:
                        raise ReleaseAcceptanceError(f"{name} load test returned errors: {metric}")
                    if float(metric["p95_ms"]) > p95_limit_ms:
                        raise ReleaseAcceptanceError(
                            f"{name} p95 exceeded {p95_limit_ms} ms: {metric}"
                        )

                _stop_process(worker_process)
                worker_process = None
                queued_response = await client.post(
                    f"/api/v1/knowledge-bases/{knowledge_id}/indexes/build",
                    headers={**headers, "Idempotency-Key": f"m7-worker-recovery-{uuid4()}"},
                    json={"reason": "MANUAL", "activate_on_success": True},
                )
                queued_response.raise_for_status()
                queued = queued_response.json()
                second_index_id = UUID(queued["index_id"])
                index_ids.append(second_index_id)
                queued_task = await client.get(
                    f"/api/v1/tasks/{queued['task_id']}", headers=headers
                )
                if queued_task.json()["status"] != "QUEUED":
                    raise ReleaseAcceptanceError("Task did not remain queued while Worker was down")
                worker_process = _start_process(
                    [python, "-m", "app.worker"], environment, worker_log
                )
                recovered_task = await _wait_for_task(
                    client, headers, str(queued["task_id"]), task_timeout
                )

                recovery: dict[str, Any] = {"worker_restart": recovered_task["status"]}
                _stop_process(worker_process)
                worker_process = None
                for service in ("redis", "minio", "postgres"):
                    _compose("stop", service)
                    stopped_services.add(service)
                    unavailable = await _wait_for_readiness(
                        client, ready=False, timeout_seconds=recovery_timeout
                    )
                    live_during_failure = await client.get("/health/live")
                    if live_during_failure.status_code != 200:
                        raise ReleaseAcceptanceError(f"Liveness failed while {service} was down")
                    _compose("up", "-d", service)
                    stopped_services.remove(service)
                    restored = await _wait_for_readiness(
                        client, ready=True, timeout_seconds=recovery_timeout
                    )
                    recovery[service] = {
                        "unavailable_status": unavailable["status"],
                        "restored_status": restored["status"],
                    }

                post_recovery = await client.get(
                    f"/api/v1/knowledge-bases/{knowledge_id}", headers=headers
                )
                post_recovery.raise_for_status()
                backup_restore = await asyncio.to_thread(_backup_restore, settings, knowledge_id)

                return {
                    "status": "passed",
                    "integration": {
                        "auth_boundary": True,
                        "readiness": ready["status"],
                        "upload_status": upload["document"]["status"],
                        "first_index_task": first_task["status"],
                        "elements": len(elements),
                        "chunks": len(chunks),
                        "playground_status": playground["status"],
                        "sse_events": event_names,
                    },
                    "load": {
                        "threshold_p95_ms": p95_limit_ms,
                        "liveness": live_load,
                        "database": database_load,
                        "rag": rag_load,
                    },
                    "recovery": recovery,
                    "backup_restore": backup_restore,
                }
        finally:
            _stop_process(worker_process)
            _stop_process(backend_process)
            for service in stopped_services:
                try:
                    await asyncio.to_thread(_compose, "up", "-d", service)
                except Exception:
                    pass
            if stopped_services:
                await asyncio.sleep(2)
            if knowledge_id is not None:
                try:
                    async with resources.database.begin() as connection:
                        await connection.execute(
                            text("UPDATE knowledge_base SET active_index_id = NULL WHERE id = :id"),
                            {"id": knowledge_id},
                        )
                        await connection.execute(
                            text("DELETE FROM knowledge_base WHERE id = :id"),
                            {"id": knowledge_id},
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
                except Exception as exc:
                    raise ReleaseAcceptanceError("M7 fixture cleanup failed") from exc
            await resources.close()
            backend_log.close()
            worker_log.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run M7 release integration acceptance")
    parser.add_argument("--port", type=int, default=18007)
    parser.add_argument("--load-requests", type=int, default=200)
    parser.add_argument("--load-concurrency", type=int, default=32)
    parser.add_argument("--rag-requests", type=int, default=50)
    parser.add_argument("--p95-limit-ms", type=float, default=3_000)
    parser.add_argument("--recovery-timeout", type=float, default=90)
    parser.add_argument("--task-timeout", type=float, default=180)
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(
                run(
                    port=args.port,
                    load_requests=args.load_requests,
                    load_concurrency=args.load_concurrency,
                    rag_requests=args.rag_requests,
                    p95_limit_ms=args.p95_limit_ms,
                    recovery_timeout=args.recovery_timeout,
                    task_timeout=args.task_timeout,
                )
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
