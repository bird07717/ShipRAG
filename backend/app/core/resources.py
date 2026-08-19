from __future__ import annotations

from dataclasses import dataclass

import urllib3
from minio import Minio
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings


@dataclass(slots=True)
class AppResources:
    database: AsyncEngine
    redis: Redis
    minio: Minio

    @classmethod
    def create(cls, settings: Settings) -> AppResources:
        timeout = settings.readiness_timeout_seconds
        http_client = urllib3.PoolManager(
            num_pools=4,
            maxsize=4,
            timeout=urllib3.Timeout(connect=timeout, read=timeout),
            retries=False,
        )
        return cls(
            database=create_async_engine(
                settings.database_url.get_secret_value(),
                pool_pre_ping=True,
                pool_recycle=1800,
                connect_args={"timeout": timeout},
            ),
            redis=Redis.from_url(
                settings.redis_url.get_secret_value(),
                socket_connect_timeout=timeout,
                socket_timeout=timeout,
                retry_on_timeout=False,
            ),
            minio=Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key.get_secret_value(),
                secret_key=settings.minio_secret_key.get_secret_value(),
                secure=settings.minio_secure,
                http_client=http_client,
            ),
        )

    async def close(self) -> None:
        await self.redis.aclose()
        await self.database.dispose()
