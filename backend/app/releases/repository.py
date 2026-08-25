from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.common.errors import ApiError


class ReleaseRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def get_release_for_index(self, index_id: UUID) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT r.*, i.version AS index_version, i.status AS index_status
                            FROM knowledge_release r
                            JOIN knowledge_index i ON i.id = r.index_id
                            WHERE r.index_id = :index_id
                            """
                        ),
                        {"index_id": index_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
        return dict(row) if row is not None else None

    async def list_releases(self, knowledge_id: UUID) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT r.*, i.version AS index_version, i.status AS index_status,
                               count(d.id) AS draft_count,
                               count(d.id) FILTER (WHERE d.status = 'APPROVED') AS approved_count
                        FROM knowledge_release r
                        JOIN knowledge_index i ON i.id = r.index_id
                        LEFT JOIN problem_profile_draft d ON d.release_id = r.id
                        WHERE r.kb_id = :kb_id
                        GROUP BY r.id, i.version, i.status
                        ORDER BY r.created_at DESC
                        """
                        ),
                        {"kb_id": knowledge_id},
                    )
                )
                .mappings()
                .all()
            )
        return [self._release_response(dict(row)) for row in rows]

    async def get_release(self, release_id: UUID) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            release = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT r.*, i.version AS index_version, i.status AS index_status
                            FROM knowledge_release r
                            JOIN knowledge_index i ON i.id = r.index_id
                            WHERE r.id = :id
                            """
                        ),
                        {"id": release_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if release is None:
                raise ApiError("RELEASE_NOT_FOUND", "发布候选不存在", 404)
            drafts = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT d.*, s.display_name AS document_name,
                               p.problem_code AS target_problem_code
                        FROM problem_profile_draft d
                        JOIN document_source s ON s.id = d.document_id
                        LEFT JOIN document_problem_profile p ON p.id = d.target_profile_id
                        WHERE d.release_id = :release_id
                        ORDER BY s.display_name, d.id
                        """
                        ),
                        {"release_id": release_id},
                    )
                )
                .mappings()
                .all()
            )
        response = self._release_response(dict(release))
        response["drafts"] = [self._draft_response(dict(row)) for row in drafts]
        response["draft_count"] = len(drafts)
        response["approved_count"] = sum(row["status"] == "APPROVED" for row in drafts)
        return response

    async def generation_documents(self, index_id: UUID) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT d.id AS document_id, d.kb_id, d.display_name, d.filename,
                               string_agg(
                                   CASE WHEN e.element_type IN ('TEXT', 'TABLE')
                                        THEN e.content ELSE '' END,
                                   E'\n\n' ORDER BY e.sequence_no
                               ) AS content
                        FROM index_document idoc
                        JOIN document_source d ON d.id = idoc.document_id
                        LEFT JOIN document_element e
                          ON e.index_id = idoc.index_id AND e.document_id = d.id
                        WHERE idoc.index_id = :index_id
                          AND COALESCE(idoc.metadata ->> 'build_mode', 'REBUILD') = 'REBUILD'
                        GROUP BY d.id, d.kb_id, d.display_name, d.filename
                        ORDER BY d.created_at, d.id
                        """
                        ),
                        {"index_id": index_id},
                    )
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]

    async def list_live_profiles(self, knowledge_id: UUID) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT id, document_id, kb_id, problem_code, canonical_question,
                               aliases, keywords, product_name, model_names, task_type, enabled
                        FROM document_problem_profile
                        WHERE kb_id = :kb_id
                        ORDER BY canonical_question
                        """
                        ),
                        {"kb_id": knowledge_id},
                    )
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]

    async def upsert_generated_draft(
        self,
        *,
        release_id: UUID,
        knowledge_id: UUID,
        document_id: UUID,
        operation: str,
        target_profile_id: UUID | None,
        profile: dict[str, Any],
        generation_source: str,
    ) -> dict[str, Any]:
        parameters = {
            "id": uuid4(),
            "release_id": release_id,
            "kb_id": knowledge_id,
            "document_id": document_id,
            "operation": operation,
            "target_profile_id": target_profile_id,
            "problem_code": profile["problem_code"],
            "canonical_question": profile["canonical_question"],
            "aliases": json.dumps(profile.get("aliases", []), ensure_ascii=False),
            "keywords": json.dumps(profile.get("keywords", []), ensure_ascii=False),
            "product_name": profile.get("product_name"),
            "model_names": json.dumps(profile.get("model_names", []), ensure_ascii=False),
            "task_type": profile.get("task_type"),
            "confidence": float(profile.get("confidence", 0)),
            "reasoning": profile.get("reasoning"),
            "generation_source": generation_source,
        }
        async with self.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                            INSERT INTO problem_profile_draft (
                                id, release_id, kb_id, document_id, operation,
                                target_profile_id, problem_code, canonical_question,
                                aliases, keywords, product_name, model_names, task_type,
                                generation_source, confidence, reasoning
                            ) VALUES (
                                :id, :release_id, :kb_id, :document_id, :operation,
                                :target_profile_id, :problem_code, :canonical_question,
                                CAST(:aliases AS jsonb), CAST(:keywords AS jsonb),
                                :product_name, CAST(:model_names AS jsonb), :task_type,
                                :generation_source, :confidence, :reasoning
                            )
                            ON CONFLICT (release_id, document_id) DO UPDATE
                            SET operation = EXCLUDED.operation,
                                target_profile_id = EXCLUDED.target_profile_id,
                                problem_code = EXCLUDED.problem_code,
                                canonical_question = EXCLUDED.canonical_question,
                                aliases = EXCLUDED.aliases,
                                keywords = EXCLUDED.keywords,
                                product_name = EXCLUDED.product_name,
                                model_names = EXCLUDED.model_names,
                                task_type = EXCLUDED.task_type,
                                generation_source = EXCLUDED.generation_source,
                                confidence = EXCLUDED.confidence,
                                reasoning = EXCLUDED.reasoning,
                                status = 'DRAFT', validation_result = '{}'::jsonb,
                                updated_at = now()
                            RETURNING *
                            """
                        ),
                        parameters,
                    )
                )
                .mappings()
                .one()
            )
        return self._draft_response(dict(row))

    async def update_draft(self, draft_id: UUID, changes: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "operation",
            "target_profile_id",
            "problem_code",
            "canonical_question",
            "aliases",
            "keywords",
            "product_name",
            "model_names",
            "task_type",
            "enabled",
            "status",
        }
        selected = {key: value for key, value in changes.items() if key in allowed}
        if not selected:
            raise ApiError("VALIDATION_ERROR", "没有可更新的草稿字段", 422)
        assignments: list[str] = []
        parameters: dict[str, Any] = {"id": draft_id}
        for key, value in selected.items():
            if key in {"aliases", "keywords", "model_names"}:
                assignments.append(f"{key} = CAST(:{key} AS jsonb)")
                parameters[key] = json.dumps(value, ensure_ascii=False)
            else:
                assignments.append(f"{key} = :{key}")
                parameters[key] = value
        assignments.extend(
            [
                "generation_source = 'MANUAL'",
                "validation_result = '{}'::jsonb",
                "updated_at = now()",
            ]
        )
        async with self.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            "UPDATE problem_profile_draft SET "
                            + ", ".join(assignments)
                            + " WHERE id = :id AND status <> 'PUBLISHED' RETURNING *"
                        ),
                        parameters,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ApiError("PROFILE_DRAFT_NOT_FOUND", "草稿不存在或已发布", 404)
            await connection.execute(
                text(
                    """
                    UPDATE knowledge_release
                    SET status = 'DRAFT', validation_result = '{}'::jsonb, updated_at = now()
                    WHERE id = :release_id AND status NOT IN ('PUBLISHED', 'CANCELLED')
                    """
                ),
                {"release_id": row["release_id"]},
            )
        return self._draft_response(dict(row))

    async def validation_snapshot(self, release_id: UUID) -> dict[str, Any]:
        release = await self.get_release(release_id)
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT d.id,
                               EXISTS (
                                   SELECT 1 FROM document_element e
                                   WHERE e.index_id = r.index_id
                                     AND e.document_id = d.document_id
                               ) AS document_published,
                               NOT EXISTS (
                                   SELECT 1 FROM document_element e
                                   LEFT JOIN image_asset a ON a.element_id = e.id
                                   WHERE e.index_id = r.index_id
                                     AND e.document_id = d.document_id
                                     AND e.element_type = 'IMAGE' AND a.id IS NULL
                               ) AS images_complete
                        FROM problem_profile_draft d
                        JOIN knowledge_release r ON r.id = d.release_id
                        WHERE d.release_id = :release_id
                        """
                        ),
                        {"release_id": release_id},
                    )
                )
                .mappings()
                .all()
            )
        readiness = {str(row["id"]): dict(row) for row in rows}
        for draft in release["drafts"]:
            draft["candidate_readiness"] = readiness.get(str(draft["id"]), {})
        release["live_profiles"] = await self.list_live_profiles(UUID(str(release["kb_id"])))
        return release

    async def save_validation(self, release_id: UUID, report: dict[str, Any]) -> dict[str, Any]:
        status = "READY_TO_PUBLISH" if report["valid"] else "VALIDATION_FAILED"
        async with self.engine.begin() as connection:
            updated = await connection.execute(
                text(
                    """
                    UPDATE knowledge_release
                    SET status = :status, validation_result = CAST(:report AS jsonb),
                        updated_at = now()
                    WHERE id = :id AND status NOT IN ('PUBLISHED', 'CANCELLED')
                    """
                ),
                {
                    "id": release_id,
                    "status": status,
                    "report": json.dumps(report, ensure_ascii=False),
                },
            )
            if updated.rowcount != 1:
                raise ApiError("RELEASE_NOT_EDITABLE", "发布候选已发布或已取消", 409)
        return await self.get_release(release_id)

    async def set_generation_error(self, release_id: UUID, error: str | None) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE knowledge_release SET generation_error = :error, updated_at = now()
                    WHERE id = :id AND status NOT IN ('PUBLISHED', 'CANCELLED')
                    """
                ),
                {"id": release_id, "error": error},
            )

    async def publish(self, release_id: UUID) -> dict[str, Any]:
        try:
            async with self.engine.begin() as connection:
                release = (
                    (
                        await connection.execute(
                            text(
                                """
                                SELECT r.*, i.status AS index_status, kb.active_index_id
                                FROM knowledge_release r
                                JOIN knowledge_index i ON i.id = r.index_id
                                JOIN knowledge_base kb ON kb.id = r.kb_id
                                WHERE r.id = :id FOR UPDATE OF r, i, kb
                                """
                            ),
                            {"id": release_id},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if release is None:
                    raise ApiError("RELEASE_NOT_FOUND", "发布候选不存在", 404)
                if release["status"] != "READY_TO_PUBLISH":
                    raise ApiError("RELEASE_NOT_VALIDATED", "发布候选尚未通过验证", 409)
                if release["index_status"] != "READY":
                    raise ApiError("INDEX_NOT_READY", "候选索引不是 READY 状态", 409)
                drafts = (
                    (
                        await connection.execute(
                            text(
                                """
                            SELECT * FROM problem_profile_draft
                            WHERE release_id = :release_id
                            ORDER BY created_at, id FOR UPDATE
                            """
                            ),
                            {"release_id": release_id},
                        )
                    )
                    .mappings()
                    .all()
                )
                if any(row["status"] != "APPROVED" for row in drafts):
                    raise ApiError("PROFILE_DRAFT_NOT_APPROVED", "所有草稿都必须先审核通过", 409)
                for row in drafts:
                    if row["operation"] == "NONE":
                        continue
                    present = await connection.execute(
                        text(
                            """
                            SELECT 1 FROM document_element
                            WHERE index_id = :index_id AND document_id = :document_id LIMIT 1
                            """
                        ),
                        {"index_id": release["index_id"], "document_id": row["document_id"]},
                    )
                    if present.scalar_one_or_none() is None:
                        raise ApiError("DOCUMENT_NOT_PUBLISHED", "草稿文档不在候选索引中", 409)
                    await self._publish_profile(connection, dict(row))

                old_index_id = release["active_index_id"]
                if old_index_id is not None:
                    await connection.execute(
                        text(
                            """
                            UPDATE knowledge_index SET status = 'DEPRECATED'
                            WHERE id = :id AND status = 'ACTIVE'
                            """
                        ),
                        {"id": old_index_id},
                    )
                await connection.execute(
                    text(
                        """
                        UPDATE knowledge_index
                        SET status = 'ACTIVE', activated_at = now()
                        WHERE id = :id AND status = 'READY'
                        """
                    ),
                    {"id": release["index_id"]},
                )
                await connection.execute(
                    text(
                        """
                        UPDATE knowledge_base SET active_index_id = :index_id, updated_at = now()
                        WHERE id = :kb_id
                        """
                    ),
                    {"index_id": release["index_id"], "kb_id": release["kb_id"]},
                )
                await connection.execute(
                    text(
                        """
                        UPDATE problem_profile_draft SET status = 'PUBLISHED', updated_at = now()
                        WHERE release_id = :release_id
                        """
                    ),
                    {"release_id": release_id},
                )
                await connection.execute(
                    text(
                        """
                        UPDATE knowledge_release
                        SET status = 'PUBLISHED', published_at = now(), updated_at = now()
                        WHERE id = :id
                        """
                    ),
                    {"id": release_id},
                )
        except ApiError:
            raise
        except SQLAlchemyError as exc:
            raise ApiError(
                "RELEASE_PUBLISH_CONFLICT", "发布时检测到 Profile 数据冲突", 409
            ) from exc
        return await self.get_release(release_id)

    @staticmethod
    async def _publish_profile(connection: AsyncConnection, draft: dict[str, Any]) -> None:
        parameters = {
            **draft,
            "aliases_json": json.dumps(list(draft["aliases"] or []), ensure_ascii=False),
            "keywords_json": json.dumps(list(draft["keywords"] or []), ensure_ascii=False),
            "models_json": json.dumps(list(draft["model_names"] or []), ensure_ascii=False),
        }
        if draft["operation"] == "UPDATE":
            if draft["target_profile_id"] is None:
                raise ApiError("UPDATE_TARGET_REQUIRED", "UPDATE 草稿缺少目标 Profile", 409)
            result = await connection.execute(
                text(
                    """
                    UPDATE document_problem_profile
                    SET document_id = :document_id, problem_code = :problem_code,
                        canonical_question = :canonical_question,
                        aliases = CAST(:aliases_json AS jsonb),
                        keywords = CAST(:keywords_json AS jsonb),
                        product_name = :product_name,
                        model_names = CAST(:models_json AS jsonb),
                        task_type = :task_type, enabled = :enabled, updated_at = now()
                    WHERE id = :target_profile_id AND kb_id = :kb_id
                    """
                ),
                parameters,
            )
            if result.rowcount != 1:
                raise ApiError("UPDATE_TARGET_NOT_FOUND", "待更新的 Profile 不存在", 409)
            return
        await connection.execute(
            text(
                """
                INSERT INTO document_problem_profile (
                    id, document_id, kb_id, problem_code, canonical_question,
                    aliases, keywords, product_name, model_names, task_type, enabled
                ) VALUES (
                    :new_id, :document_id, :kb_id, :problem_code, :canonical_question,
                    CAST(:aliases_json AS jsonb), CAST(:keywords_json AS jsonb),
                    :product_name, CAST(:models_json AS jsonb), :task_type, :enabled
                )
                """
            ),
            {**parameters, "new_id": uuid4()},
        )

    async def feedback(self, knowledge_id: UUID, limit: int) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT min(question) AS example, count(*) AS occurrences,
                               max(created_at) AS last_seen
                        FROM rag_trace
                        WHERE kb_id = :kb_id AND engine = 'SUPPORT'
                          AND response_type = 'NO_MATCH'
                          AND created_at >= now() - interval '30 days'
                        GROUP BY lower(regexp_replace(question, '[\\s[:punct:]]+', '', 'g'))
                        ORDER BY count(*) DESC, max(created_at) DESC
                        LIMIT :limit
                        """
                        ),
                        {"kb_id": knowledge_id, "limit": limit},
                    )
                )
                .mappings()
                .all()
            )
        return [
            {
                "question": str(row["example"]),
                "occurrences": int(row["occurrences"]),
                "last_seen": row["last_seen"],
                "recommendation": (
                    "建议人工归类并生成 Alias/Profile 草稿"
                    if int(row["occurrences"]) >= 2
                    else "继续观察"
                ),
            }
            for row in rows
        ]

    @staticmethod
    def _release_response(row: dict[str, Any]) -> dict[str, Any]:
        return {
            **row,
            "id": str(row["id"]),
            "kb_id": str(row["kb_id"]),
            "index_id": str(row["index_id"]),
            "validation_result": dict(row.get("validation_result") or {}),
            "draft_count": int(row.get("draft_count", 0)),
            "approved_count": int(row.get("approved_count", 0)),
        }

    @staticmethod
    def _draft_response(row: dict[str, Any]) -> dict[str, Any]:
        return {
            **row,
            "id": str(row["id"]),
            "release_id": str(row["release_id"]),
            "kb_id": str(row["kb_id"]),
            "document_id": str(row["document_id"]),
            "target_profile_id": (
                str(row["target_profile_id"]) if row.get("target_profile_id") else None
            ),
            "aliases": list(row.get("aliases") or []),
            "keywords": list(row.get("keywords") or []),
            "model_names": list(row.get("model_names") or []),
            "validation_result": dict(row.get("validation_result") or {}),
        }
