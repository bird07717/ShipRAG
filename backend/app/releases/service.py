from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.common.errors import ApiError
from app.core.config import Settings
from app.rag.llm import LlmError, LlmProvider, create_llm_provider
from app.releases.models import (
    PROFILE_ENRICHMENT_VERSION,
    GeneratedProfile,
    ProfileDraftPatch,
    ProfileEvidence,
    ProfileQualityReview,
    ProfileScope,
    ValidationReport,
)
from app.releases.repository import ReleaseRepository
from app.support.problem_resolver import normalize_for_match
from app.support.repository import SupportRepository
from app.tasks.maintenance import enqueue_maintenance_task
from app.tasks.repository import TaskRecordRepository

_PROFILE_SCOPE_SYSTEM = """你是企业技术文档范围提取器。只依据单篇文档正文（标题仅作辅助），
识别该文档实际解决的一个核心对象和任务。列出正文可验证的症状、明确不适用范围，以及支持这些结论的短摘录。
不要把位置说明、图片说明、旁路设备、参考设备或偶然出现的型号当作问题主体。输出严格 JSON。"""

_PROFILE_SYSTEM = """你是企业技术文档目录编辑。根据给定的文档范围和正文生成一个可审核的
问题 Profile 草稿。
只描述范围内的一个核心问题；不得编造文档没有的产品、型号、版本、错误码或步骤。
problem_code 使用简短稳定的英文大写下划线；canonical_question 是稳定的标准问题。
aliases 只可填写核心问题的可靠用户改写，允许 0 到 8 条；没有可靠改写时返回空列表，
绝不为凑数量引入背景设备。keywords 只保留有区分度的动作、故障、部件或错误码。
routing_summary 说明文档解决的问题、适用对象和前提。
输出严格 JSON。"""

_PROFILE_REVIEW_SYSTEM = """你是企业技术文档 Profile 质量审核员。核对 Profile 的问题主体、
别名、关键词、症状和排除范围是否都有文档范围或正文证据。任何把背景设备、位置说明、
图片说明、无关型号变成问题主体的情况都标为 REVIEW_REQUIRED 并删除相应 Alias/Keyword。
无法证明的字段必须删除或标记 REVIEW_REQUIRED。输出修订后的 Profile、质量状态、简短原因和证据，
严格 JSON。"""

ProfileProgress = Callable[[int, int], Awaitable[None]]


class ReleaseService:
    def __init__(self, repository: ReleaseRepository, settings: Settings) -> None:
        self.repository = repository
        self.settings = settings

    async def generate_for_index(self, index_id: UUID) -> list[dict[str, Any]]:
        """Generate drafts for newly rebuilt documents during an index build."""
        return await self._generate_for_index(index_id, include_reused=False)

    async def refresh_profiles_for_index(
        self, index_id: UUID, *, on_progress: ProfileProgress | None = None
    ) -> list[dict[str, Any]]:
        """Regenerate every candidate document's draft from its current content."""
        return await self._generate_for_index(
            index_id,
            include_reused=True,
            require_editable=True,
            on_progress=on_progress,
        )

    async def backfill_profiles_for_index(
        self, index_id: UUID, *, on_progress: ProfileProgress | None = None
    ) -> list[dict[str, Any]]:
        """Fill only absent or older-enrichment Profiles without overwriting drafts."""
        release = await self.repository.get_release_for_index(index_id)
        release = self._ensure_editable_release(release)
        release_id = UUID(str(release["id"]))
        knowledge_id = UUID(str(release["kb_id"]))
        documents = await self.repository.generation_documents(index_id, include_reused=True)
        live_profiles = await self.repository.list_live_profiles(knowledge_id)
        existing_drafts = await self.repository.existing_draft_document_ids(release_id)
        document_ids = self._backfill_document_ids(documents, live_profiles, existing_drafts)
        return await self._generate_for_index(
            index_id,
            include_reused=True,
            document_ids=document_ids,
            require_editable=True,
            on_progress=on_progress,
        )

    async def _generate_for_index(
        self,
        index_id: UUID,
        *,
        include_reused: bool,
        document_ids: set[UUID] | None = None,
        require_editable: bool = False,
        on_progress: ProfileProgress | None = None,
    ) -> list[dict[str, Any]]:
        release = await self.repository.get_release_for_index(index_id)
        if release is None or release["status"] in {"PUBLISHED", "CANCELLED"}:
            if require_editable:
                self._ensure_editable_release(release)
            return []
        if require_editable:
            self._ensure_editable_release(release)
        release_id = UUID(str(release["id"]))
        knowledge_id = UUID(str(release["kb_id"]))
        documents = await self.repository.generation_documents(
            index_id, include_reused=include_reused
        )
        if document_ids is not None:
            documents = [
                document
                for document in documents
                if UUID(str(document["document_id"])) in document_ids
            ]
        live_profiles = await self.repository.list_live_profiles(knowledge_id)
        provider: LlmProvider | None = None
        provider_error: str | None = None
        try:
            snapshot = await SupportRepository(self.repository.engine, self.settings).get_llm()
            provider = create_llm_provider(self.settings, snapshot)
        except (ApiError, LlmError) as exc:
            provider_error = getattr(exc, "code", type(exc).__name__)

        generated: list[dict[str, Any]] = []
        try:
            for position, document in enumerate(documents, start=1):
                (
                    profile,
                    source,
                    error,
                    quality_status,
                    quality_notes,
                    quality_evidence,
                ) = await self._generate_profile(document, provider)
                if error:
                    provider_error = error
                operation, target = self._suggest_operation(
                    UUID(str(document["document_id"])),
                    str(document["display_name"]),
                    profile,
                    live_profiles,
                )
                if target is not None:
                    profile = self._merge_update(profile, target)
                preflight_notes = self._preflight_quality_notes(profile, live_profiles, target)
                if preflight_notes:
                    quality_status = "REVIEW_REQUIRED"
                    quality_notes = list(dict.fromkeys([*quality_notes, *preflight_notes]))
                generated.append(
                    await self.repository.upsert_generated_draft(
                        release_id=release_id,
                        knowledge_id=knowledge_id,
                        document_id=UUID(str(document["document_id"])),
                        operation=operation,
                        target_profile_id=(UUID(str(target["id"])) if target is not None else None),
                        profile=profile.model_dump(mode="json"),
                        generation_source=source,
                        profile_enrichment_version=PROFILE_ENRICHMENT_VERSION,
                        quality_status=quality_status,
                        quality_notes=quality_notes,
                        quality_evidence=quality_evidence,
                    )
                )
                if on_progress is not None:
                    await on_progress(position, len(documents))
        finally:
            if provider is not None:
                await provider.aclose()
        await self.repository.set_generation_error(
            release_id,
            (
                "模型未返回可用的结构化 Profile，已生成保守草稿；"
                "请检查该模型的 JSON/结构化输出能力后重新生成。"
                if provider_error == "STRUCTURED_OUTPUT_INVALID"
                else f"LLM 生成已降级为保守草稿：{provider_error}"
                if provider_error
                else None
            ),
        )
        return generated

    async def request_profile_task(
        self, index_id: UUID, *, task_type: str, draft_id: UUID | None = None
    ) -> dict[str, Any]:
        release = self._ensure_editable_release(
            await self.repository.get_release_for_index(index_id)
        )
        metadata: dict[str, Any] = {
            "release_id": str(release["id"]),
            "knowledge_id": str(release["kb_id"]),
        }
        if draft_id is not None:
            metadata["draft_id"] = str(draft_id)
        async with self.repository.engine.begin() as connection:
            active_result = await connection.execute(
                text(
                    """
                    SELECT id, task_type, status FROM task_record
                    WHERE index_id = :index_id
                      AND task_type IN (
                          'PROFILE_GENERATE', 'PROFILE_BACKFILL',
                          'PROFILE_REFRESH', 'PROFILE_REGENERATE'
                      )
                      AND status IN ('QUEUED', 'RUNNING')
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"index_id": index_id},
            )
            active = active_result.mappings().one_or_none()
            if active is not None:
                return {
                    "task_id": active["id"],
                    "task_type": active["task_type"],
                    "status": active["status"],
                    "replayed": True,
                }
            task_id = await TaskRecordRepository.create_profile_task_in_connection(
                connection,
                task_type=task_type,
                kb_id=UUID(str(release["kb_id"])),
                index_id=index_id,
                metadata=metadata,
            )
            if task_id is None:
                # Lost the uq_task_record_active_profile_per_index race against
                # a concurrent submission for the same candidate.  Re-query: the
                # winning row is committed and visible to this new statement.
                winner_result = await connection.execute(
                    text(
                        """
                        SELECT id, task_type, status FROM task_record
                        WHERE index_id = :index_id
                          AND task_type IN (
                              'PROFILE_GENERATE', 'PROFILE_BACKFILL',
                              'PROFILE_REFRESH', 'PROFILE_REGENERATE'
                          )
                          AND status IN ('QUEUED', 'RUNNING')
                        ORDER BY created_at DESC
                        LIMIT 1
                        """
                    ),
                    {"index_id": index_id},
                )
                winner = winner_result.mappings().one_or_none()
                if winner is None:  # pragma: no cover - index guarantees a row
                    raise ApiError("INTERNAL_ERROR", "Profile 任务提交冲突无法解析", 500)
                return {
                    "task_id": winner["id"],
                    "task_type": winner["task_type"],
                    "status": winner["status"],
                    "replayed": True,
                }
        try:
            await asyncio.to_thread(enqueue_maintenance_task, self.settings, task_id)
        except Exception as exc:
            await TaskRecordRepository(self.repository.engine).fail(task_id, exc)
            raise ApiError("UPSTREAM_UNAVAILABLE", "异步任务队列不可用", 503) from exc
        return {"task_id": task_id, "task_type": task_type, "status": "QUEUED", "replayed": False}

    async def request_regenerate_profile_task(self, draft_id: UUID) -> dict[str, Any]:
        async with self.repository.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT d.release_id, d.document_id, r.index_id
                            FROM problem_profile_draft d
                            JOIN knowledge_release r ON r.id = d.release_id
                            WHERE d.id = :id
                            """
                        ),
                        {"id": draft_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ApiError("PROFILE_DRAFT_NOT_FOUND", "Profile 草稿不存在", 404)
        return await self.request_profile_task(
            UUID(str(row["index_id"])), task_type="PROFILE_REGENERATE", draft_id=draft_id
        )

    async def execute_profile_task(
        self, task: dict[str, Any], task_records: TaskRecordRepository
    ) -> dict[str, Any]:
        task_id = UUID(str(task["id"]))
        task_type = str(task["task_type"])
        index_id = UUID(str(task["index_id"]))

        async def report_progress(position: int, total: int) -> None:
            progress = 95 if total == 0 else 5 + int(position * 90 / total)
            await task_records.progress(
                task_id,
                stage="GENERATING",
                progress=progress,
                metadata={"generated_count": position, "document_count": total},
            )

        if task_type == "PROFILE_GENERATE":
            drafts = await self._generate_for_index(
                index_id, include_reused=False, on_progress=report_progress
            )
        elif task_type == "PROFILE_BACKFILL":
            drafts = await self.backfill_profiles_for_index(index_id, on_progress=report_progress)
        elif task_type == "PROFILE_REFRESH":
            drafts = await self.refresh_profiles_for_index(index_id, on_progress=report_progress)
        else:
            draft_id = UUID(str(dict(task.get("metadata") or {})["draft_id"]))
            async with self.repository.engine.connect() as connection:
                document_id = await connection.scalar(
                    text("SELECT document_id FROM problem_profile_draft WHERE id = :id"),
                    {"id": draft_id},
                )
            if document_id is None:
                raise ApiError("PROFILE_DRAFT_NOT_FOUND", "Profile 草稿不存在", 404)
            drafts = await self._generate_for_index(
                index_id,
                include_reused=True,
                document_ids={UUID(str(document_id))},
                require_editable=True,
                on_progress=report_progress,
            )
        return {"generated_count": len(drafts), "index_id": str(index_id)}

    async def regenerate_draft(self, draft_id: UUID) -> dict[str, Any]:
        # A single-document regeneration must include REUSE documents too: old
        # documents are normally excluded from the automatic build-time pass.
        async with self.repository.engine.connect() as connection:
            from sqlalchemy import text

            row = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT d.release_id, d.document_id, r.index_id
                            FROM problem_profile_draft d
                            JOIN knowledge_release r ON r.id = d.release_id
                            WHERE d.id = :id
                            """
                        ),
                        {"id": draft_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ApiError("PROFILE_DRAFT_NOT_FOUND", "Profile 草稿不存在", 404)
        drafts = await self._generate_for_index(
            UUID(str(row["index_id"])),
            include_reused=True,
            document_ids={UUID(str(row["document_id"]))},
            require_editable=True,
        )
        selected = next(
            (item for item in drafts if item["document_id"] == str(row["document_id"])), None
        )
        if selected is None:
            raise ApiError("PROFILE_DRAFT_GENERATION_FAILED", "草稿重新生成失败", 500)
        return selected

    @staticmethod
    def _ensure_editable_release(release: dict[str, Any] | None) -> dict[str, Any]:
        if release is None:
            raise ApiError("RELEASE_NOT_FOUND", "发布候选不存在", 404)
        if release["status"] in {"PUBLISHED", "CANCELLED"}:
            raise ApiError("RELEASE_NOT_EDITABLE", "发布候选已发布或已取消", 409)
        if release.get("index_status") not in {"READY", "ACTIVE", "DEPRECATED"}:
            raise ApiError("INDEX_NOT_READY", "候选索引尚未完成，暂不能维护 Profile", 409)
        return release

    @staticmethod
    def _backfill_document_ids(
        documents: list[dict[str, Any]],
        live_profiles: list[dict[str, Any]],
        existing_drafts: set[UUID],
    ) -> set[UUID]:
        live_by_document = {UUID(str(profile["document_id"])): profile for profile in live_profiles}
        eligible: set[UUID] = set()
        for document in documents:
            document_id = UUID(str(document["document_id"]))
            if document_id in existing_drafts:
                continue
            profile = live_by_document.get(document_id)
            if profile is None or int(profile.get("profile_enrichment_version") or 0) < (
                PROFILE_ENRICHMENT_VERSION
            ):
                eligible.add(document_id)
        return eligible

    async def update_draft(self, draft_id: UUID, patch: ProfileDraftPatch) -> dict[str, Any]:
        changes = patch.changes()
        if "routing_summary" in changes and changes["routing_summary"] is None:
            changes["routing_summary"] = ""
        profile_fields = {
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
        }
        if profile_fields.intersection(changes) and "status" not in changes:
            changes["status"] = "DRAFT"
        if profile_fields.intersection(changes):
            changes["quality_status"] = "MANUAL_REVIEWED"
        if changes.get("status") == "APPROVED":
            async with self.repository.engine.connect() as connection:
                quality_status = await connection.scalar(
                    text("SELECT quality_status FROM problem_profile_draft WHERE id = :id"),
                    {"id": draft_id},
                )
            if quality_status in {"REVIEW_REQUIRED", "FALLBACK"}:
                raise ApiError(
                    "PROFILE_QUALITY_REVIEW_REQUIRED",
                    "请先修订质量待复核草稿，再批准发布",
                    409,
                )
        operation = changes.get("operation")
        if operation in {"UPDATE", "REMOVE"} and not changes.get("target_profile_id"):
            # Existing target may already be present; repository validation will
            # make the final determination.  This check only protects a wholly
            # empty UPDATE/REMOVE request.
            async with self.repository.engine.connect() as connection:
                current = await connection.execute(
                    text("SELECT target_profile_id FROM problem_profile_draft WHERE id = :id"),
                    {"id": draft_id},
                )
                if current.scalar_one_or_none() is None:
                    raise ApiError("UPDATE_TARGET_REQUIRED", "请选择要替换的线上 Profile", 422)
        return await self.repository.update_draft(draft_id, changes)

    async def validate(self, release_id: UUID) -> dict[str, Any]:
        snapshot = await self.repository.validation_snapshot(release_id)
        report = self._validate_snapshot(snapshot)
        await self.repository.save_validation(release_id, report.model_dump(mode="json"))
        return report.model_dump(mode="json")

    async def publish(self, release_id: UUID) -> dict[str, Any]:
        report = await self.validate(release_id)
        if not report["valid"]:
            raise ApiError(
                "RELEASE_VALIDATION_FAILED",
                "发布候选未通过验证",
                409,
                details={"validation": report},
            )
        if not report["publishable"]:
            raise ApiError(
                "RELEASE_REVIEW_REQUIRED",
                "发布候选仍有待审核的 Profile 草稿",
                409,
                details={"validation": report},
            )
        return await self.repository.publish(release_id)

    async def rebase(self, release_id: UUID) -> dict[str, Any]:
        """Explicitly catch a lagging candidate up to the current RAG index.

        Human-reviewed drafts for unchanged documents are copied verbatim;
        changed documents keep a draft that requires regeneration or manual
        re-review; documents that left the index produce REMOVE suggestions.
        A backfill task then generates drafts only for documents that still
        have none — it never overwrites carried-over human edits.
        """
        result = await self.repository.rebase_release(release_id)
        target_index_id = UUID(str(result["target_index_id"]))
        backfill_submitted = False
        if result["create_suggestion_count"] > 0:
            try:
                submission = await self.request_profile_task(
                    target_index_id, task_type="PROFILE_BACKFILL"
                )
                backfill_submitted = not bool(submission.get("replayed"))
            except ApiError:
                # The rebase itself succeeded; draft generation for new
                # documents can be requested from the review workspace.
                backfill_submitted = False
        result["backfill_submitted"] = backfill_submitted
        return result

    async def rollback_support_release(
        self, knowledge_id: UUID, target_release_id: UUID
    ) -> dict[str, Any]:
        return await self.repository.rollback_support_release(knowledge_id, target_release_id)

    async def _generate_profile(
        self, document: dict[str, Any], provider: LlmProvider | None
    ) -> tuple[GeneratedProfile, str, str | None, str, list[str], list[dict[str, str | None]]]:
        title = str(document["display_name"] or document["filename"]).strip()
        fallback = self._fallback_profile(title, document)
        if provider is None:
            return (
                fallback,
                "FALLBACK",
                "MODEL_NOT_CONFIGURED",
                "FALLBACK",
                ["模型不可用，按文档标题生成保守草稿，需人工审核。"],
                [],
            )
        content = str(document.get("content") or "")[:20_000]
        document_prompt = f"文档标题：{title}\n\n文档正文：\n{content}"
        try:
            scope = await provider.complete_structured(
                document_prompt,
                system=_PROFILE_SCOPE_SYSTEM,
                schema=ProfileScope,
                max_tokens=800,
                temperature=0,
                timeout_seconds=self.settings.m3_provider_timeout_seconds,
            )
            generated = await provider.complete_structured(
                f"{document_prompt}\n\n已提取的文档范围：\n{scope.model_dump_json()}",
                system=_PROFILE_SYSTEM,
                schema=GeneratedProfile,
                max_tokens=1_024,
                temperature=0,
                timeout_seconds=self.settings.m3_provider_timeout_seconds,
            )
        except LlmError as exc:
            return (
                fallback,
                "FALLBACK",
                exc.code,
                "FALLBACK",
                [f"离线范围提取或生成失败：{exc.code}"],
                [],
            )
        try:
            review = await provider.complete_structured(
                (
                    f"{document_prompt}\n\n文档范围：\n{scope.model_dump_json()}"
                    f"\n\n待审核 Profile：\n{generated.model_dump_json()}"
                ),
                system=_PROFILE_REVIEW_SYSTEM,
                schema=ProfileQualityReview,
                max_tokens=1_024,
                temperature=0,
                timeout_seconds=self.settings.m3_provider_timeout_seconds,
            )
        except LlmError as exc:
            return (
                generated,
                "LLM",
                exc.code,
                "REVIEW_REQUIRED",
                [f"离线质量复核失败：{exc.code}"],
                self._evidence_payload(scope.evidence),
            )
        return (
            review.profile,
            "LLM",
            None,
            review.quality_status,
            review.quality_notes,
            self._evidence_payload(review.evidence or scope.evidence),
        )

    @staticmethod
    def _evidence_payload(evidence: list[ProfileEvidence]) -> list[dict[str, str | None]]:
        return [item.model_dump(mode="json") for item in evidence]

    @staticmethod
    def _fallback_profile(title: str, document: dict[str, Any]) -> GeneratedProfile:
        canonical = title.rstrip("？?。 ") + "？"
        ascii_words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,30}", title)
        code_words = [word.upper().replace("-", "_") for word in ascii_words]
        suffix = str(document["document_id"]).replace("-", "")[:8].upper()
        problem_code = "_".join(code_words[:5])[:90].strip("_") or f"DOC_{suffix}"
        task_type = ReleaseService._infer_task_type(title)
        action_terms = [
            term
            for term in ("升级", "更换", "检查", "配置", "设置", "报警", "故障", "修改", "自检")
            if term in title
        ]
        model_names = [word.upper() for word in ascii_words if any(char.isdigit() for char in word)]
        product = next(
            (word.upper() for word in ascii_words if word.upper() not in model_names), None
        )
        return GeneratedProfile(
            problem_code=problem_code,
            canonical_question=canonical,
            aliases=[],
            keywords=action_terms,
            product_name=product,
            model_names=model_names[:5],
            task_type=task_type,
            routing_summary=(
                f"《{title}》的保守路由摘要：模型不可用，需由管理员根据正文确认适用问题。"
            ),
            symptoms=[],
            exclusions=[],
            confidence=0.35,
            reasoning="模型不可用，按文档标题生成的保守草稿，需要管理员审核。",
        )

    @staticmethod
    def _infer_task_type(title: str) -> str | None:
        mapping = (
            ("升级", "UPGRADE"),
            ("更换", "REPLACEMENT"),
            ("检查", "CHECK"),
            ("自检", "CHECK"),
            ("配置", "CONFIGURATION"),
            ("设置", "CONFIGURATION"),
            ("修改", "CONFIGURATION"),
            ("报警", "ALARM"),
        )
        return next((task for token, task in mapping if token in title), None)

    @staticmethod
    def _suggest_operation(
        document_id: UUID,
        title: str,
        profile: GeneratedProfile,
        live_profiles: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any] | None]:
        # A document owns at most one live Profile.  This identity rule must
        # take precedence over fuzzy title/question similarity, otherwise a
        # rewritten question creates a duplicate CREATE draft.
        same_document = next(
            (
                candidate
                for candidate in live_profiles
                if str(candidate.get("document_id")) == str(document_id)
            ),
            None,
        )
        if same_document is not None:
            return "UPDATE", same_document
        probe = normalize_for_match(profile.canonical_question or title)
        best: dict[str, Any] | None = None
        best_score = 0.0
        for candidate in live_profiles:
            existing = normalize_for_match(str(candidate["canonical_question"]))
            score = SequenceMatcher(None, probe, existing).ratio()
            document_title_score = SequenceMatcher(
                None, normalize_for_match(title), existing
            ).ratio()
            score = max(score, document_title_score)
            if score > best_score:
                best, best_score = candidate, score
        if best is not None and best_score >= 0.72:
            return "UPDATE", best
        return "CREATE", None

    @staticmethod
    def _merge_update(profile: GeneratedProfile, target: dict[str, Any]) -> GeneratedProfile:
        aliases = list(dict.fromkeys([*list(target.get("aliases") or []), *profile.aliases]))
        keywords = list(dict.fromkeys([*list(target.get("keywords") or []), *profile.keywords]))
        models = list(dict.fromkeys([*list(target.get("model_names") or []), *profile.model_names]))
        symptoms = list(dict.fromkeys([*list(target.get("symptoms") or []), *profile.symptoms]))
        exclusions = list(
            dict.fromkeys([*list(target.get("exclusions") or []), *profile.exclusions])
        )
        return profile.model_copy(
            update={
                "problem_code": str(target["problem_code"]),
                "aliases": aliases[:10],
                "keywords": keywords[:12],
                "model_names": models[:10],
                "product_name": profile.product_name or target.get("product_name"),
                "task_type": profile.task_type or target.get("task_type"),
                "routing_summary": profile.routing_summary
                or str(target.get("routing_summary") or ""),
                "symptoms": symptoms[:12],
                "exclusions": exclusions[:8],
                "reasoning": (
                    (profile.reasoning + " " if profile.reasoning else "")
                    + f"与现有问题 {target['problem_code']} 高度相似，建议替换文档版本。"
                ),
            }
        )

    @staticmethod
    def _preflight_quality_notes(
        profile: GeneratedProfile,
        live_profiles: list[dict[str, Any]],
        target: dict[str, Any] | None,
    ) -> list[str]:
        """Expose probable publication collisions before administrator review.

        The final release validation remains authoritative.  This preflight is
        deliberately non-destructive: it adds diagnostics and preserves the
        editable candidate draft.
        """
        target_id = str(target["id"]) if target is not None else None
        profile_aliases = {normalize_for_match(alias) for alias in profile.aliases}
        profile_canonical = normalize_for_match(profile.canonical_question)
        notes: list[str] = []
        for candidate in live_profiles:
            if str(candidate["id"]) == target_id:
                continue
            if str(candidate.get("problem_code") or "").strip() == profile.problem_code.strip():
                notes.append("预检发现问题编码与线上 Profile 冲突。")
            candidate_canonical = normalize_for_match(str(candidate["canonical_question"]))
            if candidate_canonical and candidate_canonical == profile_canonical:
                notes.append("预检发现标准问题与线上 Profile 冲突。")
            candidate_aliases = {
                normalize_for_match(str(alias)) for alias in candidate.get("aliases") or []
            }
            if profile_aliases.intersection(candidate_aliases):
                notes.append("预检发现 Alias 与线上 Profile 冲突。")
        return list(dict.fromkeys(notes))

    @staticmethod
    def _validate_snapshot(snapshot: dict[str, Any]) -> ValidationReport:
        errors: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        review_required: dict[str, dict[str, str]] = {}
        drafts = list(snapshot.get("drafts") or [])
        live_profiles = list(snapshot.get("live_profiles") or [])
        if snapshot.get("index_status") not in {"READY", "ACTIVE", "DEPRECATED"}:
            errors.append({"code": "INDEX_NOT_READY", "message": "候选索引尚不可供 Support 使用"})
        if not drafts:
            warnings.append(
                {"code": "NO_PROFILE_CHANGES", "message": "本次发布没有需要审核的 Profile 草稿"}
            )

        codes: dict[str, str] = {}
        canonicals: dict[str, str] = {}
        aliases: dict[str, str] = {}
        for draft in drafts:
            draft_id = str(draft["id"])
            if draft["status"] != "APPROVED":
                review_required[draft_id] = ReleaseService._review_required_item(draft)
            if draft.get("quality_status") in {"REVIEW_REQUIRED", "FALLBACK"}:
                review_required[draft_id] = ReleaseService._review_required_item(draft)
                warnings.append(
                    {
                        "code": "PROFILE_QUALITY_REVIEW_REQUIRED",
                        "message": f"草稿 {draft['document_name']} 需要人工核对生成质量",
                    }
                )
            if draft["operation"] == "NONE":
                continue
            readiness = draft.get("candidate_readiness") or {}
            if draft["operation"] == "REMOVE":
                # REMOVE intentionally targets a document that left the
                # candidate index; presence checks do not apply.
                if not draft.get("target_profile_id"):
                    errors.append(
                        {
                            "code": "REMOVE_TARGET_REQUIRED",
                            "message": f"{draft['document_name']} 缺少移除目标",
                        }
                    )
            else:
                if not readiness.get("document_published"):
                    errors.append(
                        {
                            "code": "DOCUMENT_NOT_PUBLISHED",
                            "message": f"文档 {draft['document_name']} 不在候选索引中",
                        }
                    )
                if not readiness.get("images_complete", True):
                    errors.append(
                        {
                            "code": "IMAGE_ASSET_MISSING",
                            "message": f"文档 {draft['document_name']} 的图片资源不完整",
                        }
                    )
            code = str(draft["problem_code"]).strip()
            canonical = normalize_for_match(str(draft["canonical_question"]))
            if not code or not canonical:
                errors.append(
                    {"code": "PROFILE_REQUIRED_FIELD", "message": "问题编码和标准问题不能为空"}
                )
                continue
            ReleaseService._record_collision(errors, codes, code, draft_id, "PROBLEM_CODE_CONFLICT")
            ReleaseService._record_collision(
                errors, canonicals, canonical, draft_id, "CANONICAL_QUESTION_CONFLICT"
            )
            for alias in draft.get("aliases") or []:
                normalized_alias = normalize_for_match(str(alias))
                ReleaseService._record_collision(
                    errors, aliases, normalized_alias, draft_id, "ALIAS_CONFLICT"
                )
            if draft["operation"] == "UPDATE" and not draft.get("target_profile_id"):
                errors.append({"code": "UPDATE_TARGET_REQUIRED", "message": f"{code} 缺少更新目标"})
            distinguishing = list(draft.get("keywords") or [])
            if not distinguishing and draft.get("model_names"):
                warnings.append(
                    {
                        "code": "MODEL_ONLY_ROUTING",
                        "message": f"{code} 只有型号，没有区分任务意图的关键词",
                    }
                )
            for keyword in distinguishing:
                if len(normalize_for_match(str(keyword))) < 2:
                    warnings.append(
                        {
                            "code": "BROAD_KEYWORD",
                            "message": f"{code} 的关键词“{keyword}”区分度过低",
                        }
                    )

        target_ids = {
            str(draft["target_profile_id"])
            for draft in drafts
            if draft.get("target_profile_id") and draft["operation"] == "UPDATE"
        }
        for profile in live_profiles:
            if str(profile["id"]) in target_ids or not profile.get("enabled", True):
                continue
            code = str(profile["problem_code"])
            canonical = normalize_for_match(str(profile["canonical_question"]))
            ReleaseService._record_collision(errors, codes, code, "live", "PROBLEM_CODE_CONFLICT")
            ReleaseService._record_collision(
                errors, canonicals, canonical, "live", "CANONICAL_QUESTION_CONFLICT"
            )
            for alias in profile.get("aliases") or []:
                ReleaseService._record_collision(
                    errors,
                    aliases,
                    normalize_for_match(str(alias)),
                    "live",
                    "ALIAS_CONFLICT",
                )
        return ValidationReport(
            valid=not errors,
            publishable=not errors and not review_required,
            errors=errors,
            warnings=warnings,
            review_required_count=len(review_required),
            review_required=list(review_required.values()),
            checked_draft_count=len(drafts),
        )

    @staticmethod
    def _review_required_item(draft: dict[str, Any]) -> dict[str, str]:
        return {
            "id": str(draft["id"]),
            "document_name": str(draft["document_name"]),
            "status": str(draft["status"]),
            "quality_status": str(draft.get("quality_status") or "PASS"),
        }

    @staticmethod
    def _record_collision(
        errors: list[dict[str, str]],
        registry: dict[str, str],
        value: str,
        owner: str,
        code: str,
    ) -> None:
        if not value:
            return
        previous = registry.get(value)
        if previous is not None and previous != owner:
            errors.append({"code": code, "message": f"发布集合中存在冲突值：{value}"})
        else:
            registry[value] = owner
