"""Small, knowledge-scoped admin configuration; no approval workflow."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.assistant.readiness import publication_checks
from app.common.errors import ApiError
from app.common.suggestions import validate_suggestions
from app.gateway.repository import GatewayRepository


class AssistantConfiguration:
    def __init__(self, gateway: GatewayRepository) -> None:
        self.gateway = gateway

    @staticmethod
    async def _read(
        connection: AsyncConnection, knowledge_id: UUID, *, lock: bool = False
    ) -> dict[str, Any]:
        row = (
            (
                await connection.execute(
                    text(
                        "SELECT id AS knowledge_id, status, assistant_online, "
                        "assistant_suggestions AS suggestions, "
                        "assistant_config_revision AS revision "
                        "FROM knowledge_base WHERE id = :id" + (" FOR UPDATE" if lock else "")
                    ),
                    {"id": knowledge_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ApiError("KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在", 404)
        return dict(row)

    async def get(self, knowledge_id: UUID, *, public: bool = False) -> dict[str, Any]:
        check_details: dict[str, list[str]] = {}
        async with self.gateway.engine.connect() as connection:
            config = await self._read(connection, knowledge_id)
            if public and (not config["assistant_online"] or config["status"] != "ENABLED"):
                raise ApiError("KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在", 404)
            blockers = await self.gateway.assistant_runtime_blockers(
                knowledge_id,
                connection=connection,
                check_details=check_details if not public else None,
            )
        result = {**config, "ready": not blockers, "blockers": blockers}
        if not public:
            result["checks"] = publication_checks(blockers, check_details)
        return result

    async def update(
        self,
        knowledge_id: UUID,
        *,
        revision: int,
        suggestions: list[str] | None = None,
        online: bool | None = None,
    ) -> dict[str, Any]:
        async with self.gateway.engine.begin() as connection:
            config = await self._read(connection, knowledge_id, lock=True)
            if config["revision"] != revision:
                raise ApiError("ASSISTANT_CONFIG_CONFLICT", "配置已被修改，请重新加载后再操作", 409)
            if config["status"] == "DELETING":
                raise ApiError("KNOWLEDGE_BASE_DELETING", "知识库正在删除", 409)
            next_online = config["assistant_online"] if online is None else online
            next_suggestions = config["suggestions"] if suggestions is None else suggestions
            try:
                next_suggestions = validate_suggestions(next_suggestions)
            except ValueError as exc:
                # Taking a KB offline must remain possible even for legacy data.
                if online is not False:
                    raise ApiError("VALIDATION_ERROR", str(exc), 422) from exc
            if next_online and len(next_suggestions) != 3:
                raise ApiError(
                    "ASSISTANT_SUGGESTIONS_REQUIRED", "上线需配置三条完整且不重复的推荐问题", 409
                )
            if online is True:
                blockers = await self.gateway.assistant_runtime_blockers(
                    knowledge_id, connection=connection
                )
                if blockers:
                    raise ApiError(
                        "KNOWLEDGE_BASE_NOT_READY",
                        "知识库尚未就绪，无法上线",
                        409,
                        {"blockers": blockers},
                    )
            row = (
                (
                    await connection.execute(
                        text(
                            "UPDATE knowledge_base SET assistant_online = :online, "
                            "assistant_suggestions = CAST(:suggestions AS jsonb), "
                            "assistant_config_revision = assistant_config_revision + 1, "
                            "updated_at = now() "
                            "WHERE id = :id RETURNING id AS knowledge_id, assistant_online, "
                            "assistant_suggestions AS suggestions, "
                            "assistant_config_revision AS revision"
                        ),
                        {
                            "id": knowledge_id,
                            "online": next_online,
                            "suggestions": json.dumps(next_suggestions, ensure_ascii=False),
                        },
                    )
                )
                .mappings()
                .one()
            )
        return dict(row)
