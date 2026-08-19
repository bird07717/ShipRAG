from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Literal, TypedDict

from sqlalchemy import text

from app.core.config import Settings
from app.core.resources import AppResources


class CheckResult(TypedDict, total=False):
    status: Literal["ok", "error"]
    latency_ms: float
    detail: str
    version: str


class ReadinessResult(TypedDict):
    status: Literal["ready", "not_ready"]
    checks: dict[str, CheckResult]


class HealthService:
    def __init__(self, settings: Settings, resources: AppResources) -> None:
        self._settings = settings
        self._resources = resources

    async def check(self) -> ReadinessResult:
        checks = await asyncio.gather(
            self._bounded(self._check_postgres),
            self._bounded(self._check_redis),
            self._bounded(self._check_minio),
        )
        result_by_name = dict(zip(("postgres", "redis", "minio"), checks, strict=True))
        overall: Literal["ready", "not_ready"] = (
            "ready" if all(item["status"] == "ok" for item in checks) else "not_ready"
        )
        return {"status": overall, "checks": result_by_name}

    async def _bounded(self, check: Callable[[], Awaitable[CheckResult]]) -> CheckResult:
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                check(), timeout=self._settings.readiness_timeout_seconds
            )
            result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            return result
        except TimeoutError:
            return {
                "status": "error",
                "detail": "dependency check timed out",
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        except Exception:
            return {
                "status": "error",
                "detail": "dependency unavailable",
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            }

    async def _check_postgres(self) -> CheckResult:
        async with self._resources.database.connect() as connection:
            version = (await connection.execute(text("SHOW server_version"))).scalar_one()
            rows = await connection.execute(
                text("SELECT extname FROM pg_extension WHERE extname = ANY(:extensions)"),
                {"extensions": list(self._settings.postgres_extension_list)},
            )
            installed = {str(row[0]) for row in rows}
        missing = set(self._settings.postgres_extension_list) - installed
        if missing:
            return {"status": "error", "detail": "required database extension is missing"}
        return {"status": "ok", "version": str(version)}

    async def _check_redis(self) -> CheckResult:
        if not await self._resources.redis.ping():
            return {"status": "error", "detail": "PING did not return success"}
        info = await self._resources.redis.info(section="server")
        return {"status": "ok", "version": str(info.get("redis_version", "unknown"))}

    async def _check_minio(self) -> CheckResult:
        buckets = (self._settings.minio_document_bucket, self._settings.minio_image_bucket)
        exists = await asyncio.gather(
            *(asyncio.to_thread(self._resources.minio.bucket_exists, bucket) for bucket in buckets)
        )
        if not all(exists):
            return {"status": "error", "detail": "required private bucket is missing"}
        return {"status": "ok"}
