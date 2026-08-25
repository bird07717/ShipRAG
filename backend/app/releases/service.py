from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID

from app.common.errors import ApiError
from app.core.config import Settings
from app.rag.llm import LlmError, LlmProvider, create_llm_provider
from app.releases.models import GeneratedProfile, ProfileDraftPatch, ValidationReport
from app.releases.repository import ReleaseRepository
from app.support.problem_resolver import normalize_for_match
from app.support.repository import SupportRepository

_PROFILE_SYSTEM = """你是企业技术文档目录编辑。根据单篇文档生成一个可审核的问题 Profile 草稿。
只描述文档实际解决的一个核心问题，不得编造文档没有的产品、型号或步骤。
problem_code 使用简短稳定的英文大写下划线；Alias 是用户可能使用的完整问法；
keywords 只保留具有区分度的动作或故障词，不能只放宽泛产品名。输出严格 JSON。"""


class ReleaseService:
    def __init__(self, repository: ReleaseRepository, settings: Settings) -> None:
        self.repository = repository
        self.settings = settings

    async def generate_for_index(self, index_id: UUID) -> list[dict[str, Any]]:
        release = await self.repository.get_release_for_index(index_id)
        if release is None or release["status"] in {"PUBLISHED", "CANCELLED"}:
            return []
        release_id = UUID(str(release["id"]))
        knowledge_id = UUID(str(release["kb_id"]))
        documents = await self.repository.generation_documents(index_id)
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
            for document in documents:
                profile, source, error = await self._generate_profile(document, provider)
                if error:
                    provider_error = error
                operation, target = self._suggest_operation(
                    str(document["display_name"]), profile, live_profiles
                )
                if target is not None:
                    profile = self._merge_update(profile, target)
                generated.append(
                    await self.repository.upsert_generated_draft(
                        release_id=release_id,
                        knowledge_id=knowledge_id,
                        document_id=UUID(str(document["document_id"])),
                        operation=operation,
                        target_profile_id=(UUID(str(target["id"])) if target is not None else None),
                        profile=profile.model_dump(mode="json"),
                        generation_source=source,
                    )
                )
        finally:
            if provider is not None:
                await provider.aclose()
        await self.repository.set_generation_error(
            release_id,
            f"LLM 生成已降级为保守草稿：{provider_error}" if provider_error else None,
        )
        return generated

    async def regenerate_draft(self, draft_id: UUID) -> dict[str, Any]:
        # Resolve the containing index through the ordinary release response so
        # regeneration follows the same generation and replacement rules.
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
        drafts = await self.generate_for_index(UUID(str(row["index_id"])))
        selected = next(
            (item for item in drafts if item["document_id"] == str(row["document_id"])), None
        )
        if selected is None:
            raise ApiError("PROFILE_DRAFT_GENERATION_FAILED", "草稿重新生成失败", 500)
        return selected

    async def update_draft(self, draft_id: UUID, patch: ProfileDraftPatch) -> dict[str, Any]:
        changes = patch.changes()
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
            "enabled",
        }
        if profile_fields.intersection(changes) and "status" not in changes:
            changes["status"] = "DRAFT"
        operation = changes.get("operation")
        if operation == "UPDATE" and not changes.get("target_profile_id"):
            # Existing target may already be present; repository validation will
            # make the final determination.  This check only protects a wholly
            # empty UPDATE request.
            async with self.repository.engine.connect() as connection:
                from sqlalchemy import text

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
        return await self.repository.publish(release_id)

    async def _generate_profile(
        self, document: dict[str, Any], provider: LlmProvider | None
    ) -> tuple[GeneratedProfile, str, str | None]:
        title = str(document["display_name"] or document["filename"]).strip()
        if provider is not None:
            content = str(document.get("content") or "")[:20_000]
            prompt = f"文档标题：{title}\n\n文档正文：\n{content}"
            try:
                generated = await provider.complete_structured(
                    prompt,
                    system=_PROFILE_SYSTEM,
                    schema=GeneratedProfile,
                    max_tokens=1_024,
                    temperature=0,
                    timeout_seconds=self.settings.m3_provider_timeout_seconds,
                )
                return generated, "LLM", None
            except LlmError as exc:
                return self._fallback_profile(title, document), "FALLBACK", exc.code
        return self._fallback_profile(title, document), "FALLBACK", "MODEL_NOT_CONFIGURED"

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
        title: str, profile: GeneratedProfile, live_profiles: list[dict[str, Any]]
    ) -> tuple[str, dict[str, Any] | None]:
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
        return profile.model_copy(
            update={
                "problem_code": str(target["problem_code"]),
                "aliases": aliases[:10],
                "keywords": keywords[:12],
                "model_names": models[:10],
                "product_name": profile.product_name or target.get("product_name"),
                "task_type": profile.task_type or target.get("task_type"),
                "reasoning": (
                    (profile.reasoning + " " if profile.reasoning else "")
                    + f"与现有问题 {target['problem_code']} 高度相似，建议替换文档版本。"
                ),
            }
        )

    @staticmethod
    def _validate_snapshot(snapshot: dict[str, Any]) -> ValidationReport:
        errors: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        drafts = list(snapshot.get("drafts") or [])
        live_profiles = list(snapshot.get("live_profiles") or [])
        if snapshot.get("index_status") != "READY":
            errors.append({"code": "INDEX_NOT_READY", "message": "候选索引不是 READY 状态"})
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
                errors.append(
                    {
                        "code": "DRAFT_NOT_APPROVED",
                        "message": f"草稿 {draft['document_name']} 尚未审核通过",
                    }
                )
            if draft["operation"] == "NONE":
                continue
            readiness = draft.get("candidate_readiness") or {}
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
            errors=errors,
            warnings=warnings,
            checked_draft_count=len(drafts),
        )

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
