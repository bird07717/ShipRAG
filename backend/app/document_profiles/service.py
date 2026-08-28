# ruff: noqa: E501
from __future__ import annotations

import asyncio
import hashlib
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.api.schemas import DocumentProfilePatchRequest
from app.common.errors import ApiError
from app.core.config import Settings
from app.document_profiles.generator import DocumentProfileGenerator
from app.document_profiles.repository import DocumentProfileRepository
from app.rag.llm import LlmError, LlmProvider, create_llm_provider
from app.support.repository import SupportRepository
from app.tasks.maintenance import enqueue_maintenance_task
from app.tasks.repository import TaskRecordRepository


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _needs_generation(document: dict[str, Any], *, content_hash: str) -> bool:
    """Delta filter for full-knowledge runs: skip documents whose Profile was
    already generated from identical RAG content.

    - Manual pending edits are never clobbered by an automatic run.
    - Failed generations are retried (their hash was never recorded).
    - Error fallbacks (NEEDS_ATTENTION with an error code) are retried even
      though a hash exists: the hash only proves the content was read, not
      that a usable Profile came out of it.
    - A matching hash alone is not enough when nothing is left to keep:
      a discarded pending (no active, no pending) must regenerate.
    - Explicit single-document runs bypass this filter entirely.
    """
    if document.get("pending_source") == "MANUAL" and document.get("pending_state"):
        return False
    if document.get("pending_state") == "FAILED":
        return True
    stored = document.get("profile_content_hash")
    if stored is None or stored != content_hash:
        return True
    if document.get("pending_state"):
        return document.get("pending_error_code") is not None
    return not document.get("has_active_profile")


class DocumentProfileService:
    """Generate, validate and publish document Profile attributes."""

    def __init__(self, repository: DocumentProfileRepository, settings: Settings) -> None:
        self.repository = repository
        self.settings = settings

    async def summary(self, knowledge_id: UUID) -> dict[str, Any]:
        result = await self.repository.list_for_knowledge(knowledge_id)
        async with self.repository.engine.connect() as connection:
            generating_rows = (
                (
                    await connection.execute(
                        text(
                            """
                    SELECT document_id FROM task_record
                    WHERE kb_id = :knowledge_id AND task_type = 'DOCUMENT_PROFILE_GENERATE'
                      AND status IN ('QUEUED', 'RUNNING')
                    """
                        ),
                        {"knowledge_id": knowledge_id},
                    )
                )
                .scalars()
                .all()
            )
        generating_ids = {str(value) for value in generating_rows if value is not None}
        full_regeneration = any(value is None for value in generating_rows)
        if generating_rows:
            for profile in result["profiles"]:
                if profile["profile_status"] == "NOT_GENERATED" and (
                    full_regeneration or profile["document_id"] in generating_ids
                ):
                    profile["profile_status"] = "GENERATING"
        result["generating_count"] = len(generating_rows)
        return result

    async def request_generation(
        self, knowledge_id: UUID, *, document_id: UUID | None = None
    ) -> dict[str, Any]:
        # Deferred import: app.tasks.recovery pulls in app.tasks.ingestion,
        # which imports this module — a top-level import is circular.
        from app.tasks.recovery import _RUNNING_STALE_SECONDS, _age_seconds

        # Zombie guard: a worker crash mid-run leaves the task row RUNNING
        # forever, and the replay branch below would keep returning it.  A
        # heartbeat-stale RUNNING row is failed here so generation can proceed
        # without waiting for a maintenance-worker restart to reconcile it.
        records = TaskRecordRepository(self.repository.engine)
        async with self.repository.engine.connect() as connection:
            active_probe = (
                (
                    await connection.execute(
                        text(
                            """
                    SELECT id, status, updated_at FROM task_record
                    WHERE kb_id = :knowledge_id AND task_type = 'DOCUMENT_PROFILE_GENERATE'
                      AND status IN ('QUEUED', 'RUNNING')
                    ORDER BY created_at DESC LIMIT 1
                    """
                        ),
                        {"knowledge_id": knowledge_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
        if active_probe is not None and active_probe["status"] == "RUNNING":
            age = _age_seconds(dict(active_probe), "updated_at")
            if age is not None and age > _RUNNING_STALE_SECONDS:
                await records.fail(
                    UUID(str(active_probe["id"])),
                    ApiError(
                        "TASK_HEARTBEAT_TIMEOUT",
                        "任务长时间无进度心跳，已判定为孤儿任务",
                        409,
                    ),
                )
        async with self.repository.engine.begin() as connection:
            exists = await connection.scalar(
                text("SELECT 1 FROM knowledge_base WHERE id = :id AND status <> 'DELETING'"),
                {"id": knowledge_id},
            )
            if exists is None:
                raise ApiError("KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在或正在删除", 404)
            active = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT id, status FROM task_record
                        WHERE kb_id = :knowledge_id AND task_type = 'DOCUMENT_PROFILE_GENERATE'
                          AND status IN ('QUEUED', 'RUNNING')
                        ORDER BY created_at DESC LIMIT 1
                        """
                        ),
                        {"knowledge_id": knowledge_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if active is not None:
                return {
                    "task_id": active["id"],
                    "task_type": "DOCUMENT_PROFILE_GENERATE",
                    "status": active["status"],
                    "replayed": True,
                }
            task_id = await TaskRecordRepository.create_in_connection(
                connection,
                task_type="DOCUMENT_PROFILE_GENERATE",
                kb_id=knowledge_id,
                document_id=document_id,
                metadata={
                    "knowledge_id": str(knowledge_id),
                    "document_id": str(document_id) if document_id else None,
                },
            )
        try:
            await asyncio.to_thread(enqueue_maintenance_task, self.settings, task_id)
        except Exception as exc:
            await TaskRecordRepository(self.repository.engine).fail(task_id, exc)
            raise ApiError("UPSTREAM_UNAVAILABLE", "异步任务队列不可用", 503) from exc
        return {
            "task_id": task_id,
            "task_type": "DOCUMENT_PROFILE_GENERATE",
            "status": "QUEUED",
            "replayed": False,
        }

    async def execute_generation_task(
        self, task: dict[str, Any], task_records: TaskRecordRepository
    ) -> dict[str, Any]:
        task_id = UUID(str(task["id"]))
        metadata = dict(task.get("metadata") or {})
        knowledge_id = UUID(str(metadata.get("knowledge_id") or task["kb_id"]))
        document_id = UUID(str(task["document_id"])) if task.get("document_id") else None
        documents = await self.repository.generation_documents(knowledge_id, document_id)
        # Incremental full-KB runs: unchanged documents keep their Profile.
        # Single-document requests are explicit and always regenerate.
        skipped_count = 0
        if document_id is None:
            selected: list[dict[str, Any]] = []
            for document in documents:
                digest = _content_hash(str(document["content"]))
                if _needs_generation(document, content_hash=digest):
                    selected.append(document)
            skipped_count = len(documents) - len(selected)
            documents = selected
        provider: LlmProvider | None = None
        provider_error: str | None = None
        generator = DocumentProfileGenerator(self.settings)
        # Documents are independent rows, so they are generated with bounded
        # concurrency inside the single maintenance job instead of serially.
        concurrency = max(1, min(self.settings.profile_generation_concurrency, len(documents) or 1))
        semaphore = asyncio.Semaphore(concurrency)
        completed = 0
        stop_requested = False
        cancelled = False
        failed_documents: list[dict[str, str]] = []

        async def _process(document: dict[str, Any]) -> str:
            nonlocal completed, provider_error, stop_requested
            async with semaphore:
                # Cooperative cancellation: the admin API flips task status to
                # CANCELLED and running workers observe it between documents.
                if stop_requested:
                    return "CANCELLED"
                if await task_records.status(task_id) != "RUNNING":
                    stop_requested = True
                    return "CANCELLED"
                profile, source, error, quality, notes = await generator.generate(
                    document, provider
                )
                if error:
                    provider_error = error
                state = "PENDING_ONLINE" if quality == "PASS" else "NEEDS_ATTENTION"
                message = "；".join(notes) if notes else None
                try:
                    await self.repository.upsert_pending(
                        knowledge_id=knowledge_id,
                        document_id=UUID(str(document["document_id"])),
                        payload={"intents": [{**profile.model_dump(mode="json"), "enabled": True}]},
                        source="AUTO",
                        confidence=0.8 if error is None else 0.35,
                        state=state,
                        error_code=error,
                        error_message=message,
                        # Record the hash only for real generations.  An error
                        # fallback (e.g. upstream LLM unavailable) must be
                        # retried by later runs, not skipped as "unchanged".
                        content_hash=(
                            _content_hash(str(document["content"])) if error is None else None
                        ),
                    )
                except ApiError as exc:
                    # A single stale/deleted document must not abort the whole
                    # knowledge-base run; record it and keep the others going.
                    failed_documents.append(
                        {
                            "document_id": str(document["document_id"]),
                            "error_code": str(getattr(exc, "code", type(exc).__name__)),
                            "error_message": str(getattr(exc, "message", "") or "")[:200],
                        }
                    )
                    return "ERROR"
                completed += 1
                await task_records.progress(
                    task_id,
                    stage="GENERATING",
                    progress=5 + int(completed * 90 / max(1, len(documents))),
                    metadata={
                        "generated_count": completed,
                        "document_count": len(documents),
                        "pending_state": state,
                    },
                )
                return "OK"

        try:
            try:
                snapshot = await SupportRepository(self.repository.engine, self.settings).get_llm()
                provider = create_llm_provider(self.settings, snapshot)
            except (ApiError, LlmError) as exc:
                provider_error = getattr(exc, "code", type(exc).__name__)
            results = await asyncio.gather(*(_process(document) for document in documents))
            cancelled = "CANCELLED" in results
        finally:
            if provider is not None:
                await provider.aclose()
        return {
            "knowledge_id": str(knowledge_id),
            "generated_count": completed,
            "document_count": len(documents),
            "skipped_count": skipped_count,
            "provider_error": provider_error,
            "cancelled": cancelled,
            "failed_documents": failed_documents,
        }

    async def edit(
        self, knowledge_id: UUID, document_id: UUID, patch: DocumentProfilePatchRequest
    ) -> dict[str, Any]:
        intent = patch.model_dump(mode="json")
        return await self.repository.upsert_pending(
            knowledge_id=knowledge_id,
            document_id=document_id,
            payload={"intents": [intent]},
            source="MANUAL",
            confidence=1.0,
            state="PENDING_ONLINE",
        )

    async def check(self, knowledge_id: UUID) -> dict[str, Any]:
        rows = await self.repository.pending_rows(knowledge_id)
        errors: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        aliases: dict[str, str] = {}
        publishable_rows = 0
        for row in rows:
            document_name = str(row["document_name"])
            if row["pending_state"] == "NEEDS_ATTENTION":
                warnings.append(
                    {
                        "code": "PROFILE_NEEDS_ATTENTION",
                        "message": f"《{document_name}》为保守或低置信属性，仍将走 RAG。",
                    }
                )
                continue
            if row["pending_state"] == "FAILED":
                warnings.append(
                    {
                        "code": "PROFILE_FAILED",
                        "message": f"《{document_name}》生成失败，仍将走 RAG。",
                    }
                )
                continue
            intents = self.repository.intents(dict(row["pending_payload"] or {}))
            if not intents:
                errors.append(
                    {
                        "code": "PROFILE_EMPTY",
                        "message": f"《{document_name}》缺少可上线的 Profile 属性。",
                    }
                )
                continue
            for intent in intents:
                if not str(intent.get("canonical_question") or "").strip():
                    errors.append(
                        {
                            "code": "PROFILE_REQUIRED_FIELD",
                            "message": f"《{document_name}》缺少标准问题。",
                        }
                    )
                for alias in intent.get("aliases", []):
                    key = self.repository.normalize(str(alias))
                    if not key:
                        continue
                    previous = aliases.setdefault(key, document_name)
                    if previous != document_name:
                        errors.append(
                            {
                                "code": "PROFILE_ALIAS_CONFLICT",
                                "message": f"Alias「{alias}」同时属于《{previous}》和《{document_name}》。",
                            }
                        )
            publishable_rows += 1
        return {
            "valid": not errors,
            "publishable": not errors and publishable_rows > 0,
            "pending_count": publishable_rows,
            "errors": errors,
            "warnings": warnings,
        }

    async def publish(self, knowledge_id: UUID) -> dict[str, Any]:
        report = await self.check(knowledge_id)
        if not report["valid"]:
            raise ApiError(
                "PROFILE_PUBLISH_CHECK_FAILED",
                "待上线 Profile 属性未通过检查",
                409,
                details={"check": report},
            )
        if not report["publishable"]:
            raise ApiError(
                "PROFILE_NOTHING_TO_PUBLISH",
                "没有可上线的 Profile 属性",
                409,
                details={"check": report},
            )
        result = await self.repository.publish(knowledge_id, report)
        return {**result, "check": report, "summary": await self.summary(knowledge_id)}
