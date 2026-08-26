from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.common.errors import ApiError

logger = logging.getLogger(__name__)


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
        # A schema migration can invalidate asyncpg's prepared statement plan
        # in an already-running API worker.  Release detail is a read-only
        # operation, so dispose that stale pool and retry once instead of
        # making the first post-migration review request fail with a 500.
        for attempt in range(2):
            try:
                return await self._get_release_once(release_id)
            except SQLAlchemyError as error:
                stale_plan = (
                    type(getattr(error, "orig", None)).__name__ == "InvalidCachedStatementError"
                )
                if not stale_plan or attempt:
                    raise
                await self.engine.dispose()
        raise AssertionError("release detail retry loop unexpectedly completed")

    async def _get_release_once(self, release_id: UUID) -> dict[str, Any]:
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

    async def generation_documents(
        self,
        index_id: UUID,
        *,
        include_reused: bool = False,
        document_id: UUID | None = None,
    ) -> list[dict[str, Any]]:
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
                          AND (
                              :include_reused
                              OR COALESCE(idoc.metadata ->> 'build_mode', 'REBUILD') = 'REBUILD'
                          )
                          AND (
                              CAST(:document_id AS uuid) IS NULL
                              OR d.id = CAST(:document_id AS uuid)
                          )
                        GROUP BY d.id, d.kb_id, d.display_name, d.filename
                        ORDER BY d.created_at, d.id
                        """
                        ),
                        {
                            "index_id": index_id,
                            "include_reused": include_reused,
                            "document_id": document_id,
                        },
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
                               aliases, keywords, product_name, model_names, task_type,
                               routing_summary, symptoms, exclusions, profile_enrichment_version,
                               enabled
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

    async def existing_draft_document_ids(self, release_id: UUID) -> set[UUID]:
        """Return documents already represented in a candidate.

        Backfill must never replace an administrator's in-progress candidate
        change.  Full refresh deliberately uses ``upsert_generated_draft`` and
        therefore does replace those drafts.
        """
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT document_id
                            FROM problem_profile_draft
                            WHERE release_id = :release_id
                            """
                        ),
                        {"release_id": release_id},
                    )
                )
                .scalars()
                .all()
            )
        return {UUID(str(document_id)) for document_id in rows}

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
        profile_enrichment_version: int = 1,
        quality_status: str = "PASS",
        quality_notes: list[str] | None = None,
        quality_evidence: list[dict[str, Any]] | None = None,
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
            "enabled": bool(profile.get("enabled", True)),
            "routing_summary": profile.get("routing_summary", ""),
            "symptoms": json.dumps(profile.get("symptoms", []), ensure_ascii=False),
            "exclusions": json.dumps(profile.get("exclusions", []), ensure_ascii=False),
            "profile_enrichment_version": profile_enrichment_version,
            "confidence": float(profile.get("confidence", 0)),
            "reasoning": profile.get("reasoning"),
            "generation_source": generation_source,
            "quality_status": quality_status,
            "quality_notes": json.dumps(quality_notes or [], ensure_ascii=False),
            "quality_evidence": json.dumps(quality_evidence or [], ensure_ascii=False),
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
                                enabled,
                                routing_summary, symptoms, exclusions,
                                profile_enrichment_version,
                                generation_source, confidence, reasoning,
                                quality_status, quality_notes, quality_evidence
                            ) VALUES (
                                :id, :release_id, :kb_id, :document_id, :operation,
                                :target_profile_id, :problem_code, :canonical_question,
                                CAST(:aliases AS jsonb), CAST(:keywords AS jsonb),
                                :product_name, CAST(:model_names AS jsonb), :task_type,
                                :enabled,
                                :routing_summary, CAST(:symptoms AS jsonb),
                                CAST(:exclusions AS jsonb),
                                :profile_enrichment_version,
                                :generation_source, :confidence, :reasoning,
                                :quality_status, CAST(:quality_notes AS jsonb),
                                CAST(:quality_evidence AS jsonb)
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
                                routing_summary = EXCLUDED.routing_summary,
                                symptoms = EXCLUDED.symptoms,
                                exclusions = EXCLUDED.exclusions,
                                profile_enrichment_version = EXCLUDED.profile_enrichment_version,
                                generation_source = EXCLUDED.generation_source,
                                confidence = EXCLUDED.confidence,
                                reasoning = EXCLUDED.reasoning,
                                quality_status = EXCLUDED.quality_status,
                                quality_notes = EXCLUDED.quality_notes,
                                quality_evidence = EXCLUDED.quality_evidence,
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
            "routing_summary",
            "symptoms",
            "exclusions",
            "enabled",
            "status",
            "quality_status",
        }
        selected = {key: value for key, value in changes.items() if key in allowed}
        if not selected:
            raise ApiError("VALIDATION_ERROR", "没有可更新的草稿字段", 422)
        assignments: list[str] = []
        parameters: dict[str, Any] = {"id": draft_id}
        for key, value in selected.items():
            if key in {"aliases", "keywords", "model_names", "symptoms", "exclusions"}:
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

    async def create_draft_from_live(self, *, release_id: UUID, profile_id: UUID) -> dict[str, Any]:
        """Copy one live Profile into the current candidate as an UPDATE draft.

        This is the safe editing boundary: published routing data remains
        immutable until the candidate is reviewed and published atomically.
        An existing draft for the same document is returned unchanged so a
        repeated click cannot erase in-progress edits.
        """
        async with self.engine.begin() as connection:
            release = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT id, kb_id, index_id, status
                            FROM knowledge_release
                            WHERE id = :release_id
                            FOR UPDATE
                            """
                        ),
                        {"release_id": release_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if release is None:
                raise ApiError("RELEASE_NOT_FOUND", "发布候选不存在", 404)
            if release["status"] in {"PUBLISHED", "CANCELLED"}:
                raise ApiError("RELEASE_NOT_EDITABLE", "发布候选已发布或已取消", 409)

            profile = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT *
                            FROM document_problem_profile
                            WHERE id = :profile_id AND kb_id = :kb_id
                            FOR UPDATE
                            """
                        ),
                        {"profile_id": profile_id, "kb_id": release["kb_id"]},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if profile is None:
                raise ApiError("PROBLEM_PROFILE_NOT_FOUND", "线上 Profile 不存在", 404)

            present = await connection.execute(
                text(
                    """
                    SELECT 1 FROM index_document
                    WHERE index_id = :index_id AND document_id = :document_id
                    LIMIT 1
                    """
                ),
                {"index_id": release["index_id"], "document_id": profile["document_id"]},
            )
            if present.scalar_one_or_none() is None:
                raise ApiError("DOCUMENT_NOT_IN_CANDIDATE", "文档不在当前候选索引中", 409)

            existing = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT * FROM problem_profile_draft
                            WHERE release_id = :release_id AND document_id = :document_id
                            FOR UPDATE
                            """
                        ),
                        {
                            "release_id": release_id,
                            "document_id": profile["document_id"],
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                return self._draft_response(dict(existing))

            created = (
                (
                    await connection.execute(
                        text(
                            """
                            INSERT INTO problem_profile_draft (
                                id, release_id, kb_id, document_id, operation,
                                target_profile_id, problem_code, canonical_question,
                                aliases, keywords, product_name, model_names, task_type,
                                enabled,
                                routing_summary, symptoms, exclusions,
                                profile_enrichment_version,
                                generation_source, confidence, reasoning
                            ) VALUES (
                                :id, :release_id, :kb_id, :document_id, 'UPDATE',
                                :target_profile_id, :problem_code, :canonical_question,
                                CAST(:aliases AS jsonb), CAST(:keywords AS jsonb),
                                :product_name, CAST(:model_names AS jsonb), :task_type,
                                :enabled,
                                :routing_summary, CAST(:symptoms AS jsonb),
                                CAST(:exclusions AS jsonb), :profile_enrichment_version,
                                'MANUAL', 1.0, :reasoning
                            )
                            RETURNING *
                            """
                        ),
                        {
                            "id": uuid4(),
                            "release_id": release_id,
                            "kb_id": release["kb_id"],
                            "document_id": profile["document_id"],
                            "target_profile_id": profile["id"],
                            "problem_code": profile["problem_code"],
                            "canonical_question": profile["canonical_question"],
                            "aliases": json.dumps(
                                list(profile["aliases"] or []), ensure_ascii=False
                            ),
                            "keywords": json.dumps(
                                list(profile["keywords"] or []), ensure_ascii=False
                            ),
                            "product_name": profile["product_name"],
                            "model_names": json.dumps(
                                list(profile["model_names"] or []), ensure_ascii=False
                            ),
                            "task_type": profile["task_type"],
                            "enabled": profile["enabled"],
                            "routing_summary": str(profile.get("routing_summary") or ""),
                            "symptoms": json.dumps(
                                list(profile.get("symptoms") or []), ensure_ascii=False
                            ),
                            "exclusions": json.dumps(
                                list(profile.get("exclusions") or []), ensure_ascii=False
                            ),
                            "profile_enrichment_version": int(
                                profile.get("profile_enrichment_version") or 0
                            ),
                            "reasoning": "从线上 Profile 创建的人工编辑草稿。",
                        },
                    )
                )
                .mappings()
                .one()
            )
            await connection.execute(
                text(
                    """
                    UPDATE knowledge_release
                    SET status = 'DRAFT', validation_result = '{}'::jsonb, updated_at = now()
                    WHERE id = :release_id
                    """
                ),
                {"release_id": release_id},
            )
        return self._draft_response(dict(created))

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
        status = (
            "READY_TO_PUBLISH"
            if report["publishable"]
            else "VALIDATION_FAILED"
            if not report["valid"]
            else "DRAFT"
        )
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
                                SELECT r.*, i.status AS index_status,
                                       i.version AS index_version, kb.active_index_id
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
                newer_release = await connection.execute(
                    text(
                        """
                        SELECT 1
                        FROM knowledge_release newer
                        JOIN knowledge_index newer_index ON newer_index.id = newer.index_id
                        WHERE newer.kb_id = :kb_id
                          AND newer.id <> :release_id
                          AND newer.status <> 'CANCELLED'
                          AND newer_index.version > :index_version
                        LIMIT 1
                        """
                    ),
                    {
                        "kb_id": release["kb_id"],
                        "release_id": release_id,
                        "index_version": release["index_version"],
                    },
                )
                if newer_release.scalar_one_or_none() is not None:
                    raise ApiError(
                        "RELEASE_SUPERSEDED",
                        "该候选已被更新版本取代，请审核最新发布候选",
                        409,
                    )
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
                    raise ApiError(
                        "RELEASE_REVIEW_REQUIRED", "所有 Profile 草稿都必须先审核通过", 409
                    )
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
            "symptoms_json": json.dumps(list(draft.get("symptoms") or []), ensure_ascii=False),
            "exclusions_json": json.dumps(list(draft.get("exclusions") or []), ensure_ascii=False),
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
                        task_type = :task_type, routing_summary = :routing_summary,
                        symptoms = CAST(:symptoms_json AS jsonb),
                        exclusions = CAST(:exclusions_json AS jsonb),
                        profile_enrichment_version = :profile_enrichment_version,
                        enabled = :enabled, updated_at = now()
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
                    aliases, keywords, product_name, model_names, task_type, enabled,
                    routing_summary, symptoms, exclusions, profile_enrichment_version
                ) VALUES (
                    :new_id, :document_id, :kb_id, :problem_code, :canonical_question,
                    CAST(:aliases_json AS jsonb), CAST(:keywords_json AS jsonb),
                    :product_name, CAST(:models_json AS jsonb), :task_type, :enabled,
                    :routing_summary, CAST(:symptoms_json AS jsonb),
                    CAST(:exclusions_json AS jsonb),
                    :profile_enrichment_version
                )
                """
            ),
            {**parameters, "new_id": uuid4()},
        )

    async def document_profile_context(self, document_id: UUID) -> dict[str, Any] | None:
        """Deterministic single-document Profile context for the Documents view.

        Returns None when the document does not exist.  The response pairs the
        live published Profile (if any) with the newest unpublished candidate
        draft; multiple open candidates are a data anomaly, so the newest wins
        and a warning is logged instead of picking silently.  LLM reasoning and
        storage locations stay out of the payload — this is a read-only view.
        """
        async with self.engine.connect() as connection:
            document_result = await connection.execute(
                text("SELECT 1 FROM document_source WHERE id = :id"),
                {"id": document_id},
            )
            if document_result.scalar_one_or_none() is None:
                return None
            published = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT id, problem_code, canonical_question, aliases,
                                   keywords, product_name, model_names, task_type,
                                   routing_summary, symptoms, exclusions, enabled,
                                   profile_enrichment_version, updated_at
                            FROM document_problem_profile
                            WHERE document_id = :document_id
                            ORDER BY updated_at DESC
                            LIMIT 1
                            """
                        ),
                        {"document_id": document_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            draft_rows = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT d.id, d.release_id, d.operation, d.target_profile_id,
                                   d.problem_code, d.canonical_question, d.aliases,
                                   d.keywords, d.product_name, d.model_names, d.task_type,
                                   d.enabled, d.routing_summary, d.status, d.quality_status,
                                   d.quality_notes, d.generation_source, d.updated_at,
                                   r.index_id, i.version AS index_version,
                                   r.status AS release_status
                            FROM problem_profile_draft d
                            JOIN knowledge_release r ON r.id = d.release_id
                            JOIN knowledge_index i ON i.id = r.index_id
                            WHERE d.document_id = :document_id
                              AND r.status NOT IN ('PUBLISHED', 'CANCELLED')
                            ORDER BY r.created_at DESC, d.created_at DESC
                            """
                        ),
                        {"document_id": document_id},
                    )
                )
                .mappings()
                .all()
            )
        if len(draft_rows) > 1:
            logger.warning(
                "Document %s has %d open candidate Profile drafts; returning the newest",
                document_id,
                len(draft_rows),
            )
        return {
            "document_id": str(document_id),
            "published_profile": (
                self._profile_context_summary(dict(published)) if published else None
            ),
            "candidate_draft": (
                self._draft_context_summary(dict(draft_rows[0])) if draft_rows else None
            ),
        }

    @staticmethod
    def _profile_context_summary(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "problem_code": row["problem_code"],
            "canonical_question": row["canonical_question"],
            "aliases": list(row.get("aliases") or []),
            "keywords": list(row.get("keywords") or []),
            "product_name": row.get("product_name"),
            "model_names": list(row.get("model_names") or []),
            "task_type": row.get("task_type"),
            "routing_summary": str(row.get("routing_summary") or ""),
            "symptoms": list(row.get("symptoms") or []),
            "exclusions": list(row.get("exclusions") or []),
            "enabled": bool(row.get("enabled", True)),
            "profile_enrichment_version": int(row.get("profile_enrichment_version") or 0),
            "updated_at": row.get("updated_at"),
        }

    @staticmethod
    def _draft_context_summary(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "release_id": str(row["release_id"]),
            "index_id": str(row["index_id"]),
            "index_version": int(row["index_version"]),
            "release_status": row["release_status"],
            "status": row["status"],
            "operation": row["operation"],
            "target_profile_id": (
                str(row["target_profile_id"]) if row.get("target_profile_id") else None
            ),
            "problem_code": row["problem_code"],
            "canonical_question": row["canonical_question"],
            "aliases": list(row.get("aliases") or []),
            "keywords": list(row.get("keywords") or []),
            "product_name": row.get("product_name"),
            "model_names": list(row.get("model_names") or []),
            "task_type": row.get("task_type"),
            "routing_summary": str(row.get("routing_summary") or ""),
            "enabled": bool(row.get("enabled", True)),
            "quality_status": row.get("quality_status") or "PASS",
            "quality_notes": list(row.get("quality_notes") or []),
            "generation_source": row.get("generation_source"),
            "updated_at": row.get("updated_at"),
        }

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
            "symptoms": list(row.get("symptoms") or []),
            "exclusions": list(row.get("exclusions") or []),
            "quality_notes": list(row.get("quality_notes") or []),
            "quality_evidence": list(row.get("quality_evidence") or []),
            "validation_result": dict(row.get("validation_result") or {}),
        }
