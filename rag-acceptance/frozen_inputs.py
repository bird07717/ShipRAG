"""Frozen LLM inputs shared by the rag-acceptance experiment tools.

Builds, from the local database, the answer-stage inputs the experiments replay:
the three cases of the evidence-input baseline (their context hashes are verified, so the
documents are exactly the ones used before) plus two extra full-operation cases built from
documents that have step images. Read-only: nothing is written to the database.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from run_evidence_input_review import FrozenDocuments, sha
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.rag.document_card_repository import DocumentCardRepository
from app.rag.llm import FakeLlmProvider, create_llm_provider
from app.rag.models import ModelSnapshot
from app.rag.repository import RagRepository
from app.rag.v2.document_context import DocumentContext, build_document_context
from app.rag.v2.document_retrieval import DocumentCandidate

BASELINE_PATH = Path("rag-acceptance/reports/evidence-input-20261008/answers.json")


# Key order of the two answer-path prompts before ADR-0042 (static instructions last). The
# experiments of 2026-10-08..10 ran with this order; ``legacy_layout`` rebuilds it from a prompt.
# Production now ships the static-first order for LLM 2 only; LLM 1 keeps this order because the
# static-first order dropped the user's explicit exclusions (see prompt-layout-review.md).
LEGACY_ORDER = {
    "LLM2": (
        "trusted_product_profile",
        "resolved_question",
        "conversation_relation",
        "active_task",
        "interpreted_scope",
        "recent_completed_history",
        "hidden_document_context",
        "available_document_keys",
        "available_image_keys",
        "output_schema",
        "output_example",
        "decision_policy",
        "output_contract",
    ),
    "LLM1": (
        "current_question",
        "recent_completed_rounds",
        "knowledge_base",
        "output_schema",
        "output_example",
        "route_examples",
        "rules",
    ),
}


def legacy_layout(prompt: str, stage: str) -> str:
    data = json.loads(prompt)
    order = LEGACY_ORDER[stage]
    assert set(order) == set(data), "prompt keys changed; update LEGACY_ORDER"
    return json.dumps(
        {k: data[k] for k in order}, ensure_ascii=False, separators=(",", ":")
    )


STATIC_KEYS = {
    "LLM2": ("output_schema", "output_example", "decision_policy", "output_contract"),
    "LLM1": ("output_schema", "output_example", "route_examples", "rules"),
}


def static_first_layout(prompt: str, stage: str) -> str:
    """Static instructions first, the remaining keys in their legacy order."""

    data = json.loads(prompt)
    keys = STATIC_KEYS[stage]
    order = [*keys, *(k for k in LEGACY_ORDER[stage] if k not in keys)]
    assert set(order) == set(data), "prompt keys changed; update LEGACY_ORDER"
    return json.dumps(
        {k: data[k] for k in order}, ensure_ascii=False, separators=(",", ":")
    )


@dataclass(frozen=True)
class OperationSpec:
    """A full-operation case built from named documents (the first one is the main document)."""

    case_id: str
    question: str
    main: str
    neighbours: tuple[str, ...]


EXTRA_OPERATIONS = (
    OperationSpec(
        "op_pdc_replace",
        "请完整说明更换故障 PDC 的步骤，并附上各步骤需要的图片。",
        "文档10—如何更换故障PDC",
        ("文档9—PDC 如何检查接线盒", "文档11—如何U盘升级PDC"),
    ),
    OperationSpec(
        "op_ffc_replace",
        "请完整说明更换 FFC 的步骤，并附上各步骤需要的图片。",
        "文档15-FFC 如何更换",
        ("文档16—如何升级FFC", "文档18—如何更换LRU单元"),
    ),
)
# case id in the baseline file -> (kind, display-name fragment of the main document or None)
BASELINE_CASES = {
    "evidence_psu": ("OPERATION", "文档21—如何更换PSU电源板"),
    "evidence_ffc": ("NON_OPERATION", None),
    "evidence_pdc": ("NON_OPERATION", None),
}


@dataclass(frozen=True)
class FrozenCase:
    case_id: str
    kind: str  # OPERATION | NON_OPERATION
    question: str
    history: list[dict[str, str]]
    context: DocumentContext
    main_document_id: str | None
    product_name: str
    scope_description: str

    def main_document_key(self) -> str | None:
        if self.main_document_id is None:
            return None
        return self.context.document_keys[
            self.context.document_ids.index(self.main_document_id)
        ]

    def main_image_keys(self) -> list[str]:
        key = self.main_document_key()
        return [b.key for b in self.context.image_bindings if b.document_key == key]


@dataclass(frozen=True)
class LlmHandle:
    snapshot: ModelSnapshot
    label: dict[str, str]


async def load_llm(
    settings: Settings, engine: AsyncEngine, repo: RagRepository
) -> LlmHandle:
    async with engine.connect() as db:
        model = dict(
            (
                await db.execute(
                    text(
                        "SELECT * FROM model_config WHERE model_type='LLM' AND enabled"
                    )
                )
            )
            .mappings()
            .one()
        )
    snapshot = ModelSnapshot(
        provider=model["provider"],
        model_name=model["model_name"],
        parameters=model["parameters"] or {},
        base_url=model["base_url"],
        api_key=repo._decrypt_api_key(model),
    )
    check = create_llm_provider(settings, snapshot)
    try:
        if isinstance(check, FakeLlmProvider) or snapshot.provider != "dashscope":
            raise ValueError("Require the real DashScope model")
    finally:
        await check.aclose()
    return LlmHandle(
        snapshot, {"provider": snapshot.provider, "name": snapshot.model_name}
    )


async def load_cases(
    settings: Settings,
    engine: AsyncEngine,
    repo: RagRepository,
    card_repo: DocumentCardRepository,
    baseline_path: Path = BASELINE_PATH,
    only: set[str] | None = None,
) -> tuple[list[FrozenCase], dict[str, Any]]:
    baseline = json.loads(baseline_path.read_text())
    kb_id, index_id = UUID(baseline["knowledge_id"]), UUID(baseline["index_id"])
    async with engine.connect() as db:
        profile = dict(
            (
                await db.execute(
                    text("SELECT name,description FROM knowledge_base WHERE id=:id"),
                    {"id": kb_id},
                )
            )
            .mappings()
            .one()
        )
    documents = {
        str(d["document_id"]): d
        for d in await card_repo.generation_documents(kb_id, index_id)
    }

    def find(fragment: str) -> str:
        hits = [
            did
            for did, d in documents.items()
            if fragment in str(d.get("display_name") or d.get("filename") or "")
        ]
        if len(hits) != 1:
            raise ValueError(
                f"expected exactly one document matching {fragment!r}, got {len(hits)}"
            )
        return hits[0]

    blocks: dict[str, Any] = {}
    sources: dict[str, Any] = {}

    async def context_for(doc_ids: list[str]) -> DocumentContext:
        for did in doc_ids:
            if did not in blocks:
                sources[did] = await repo.get_document_source(UUID(did))
                if sources[did]["status"] != "STORED":
                    raise ValueError("Source document is no longer stored")
                blocks[did] = await repo.get_document_blocks(
                    knowledge_id=kb_id, index_id=index_id, document_id=UUID(did)
                )
        return await build_document_context(
            FrozenDocuments(blocks, sources),
            knowledge_id=kb_id,
            index_id=index_id,
            candidates=[DocumentCandidate(UUID(d), 0, (), None) for d in doc_ids],
            token_budget=min(
                settings.m3_rag_full_context_token_budget,
                settings.m3_prompt_token_budget,
            ),
        )

    cases: list[FrozenCase] = []
    for original in baseline["results"]:
        if original["id"] not in BASELINE_CASES or (
            only and original["id"] not in only
        ):
            continue
        kind, main_fragment = BASELINE_CASES[original["id"]]
        old = original["contexts"]["A"]
        context = await context_for(old["document_ids"])
        if sha(context.prompt_context) != old["context_sha256"]:
            raise ValueError(
                f"{original['id']}: frozen context differs from the baseline"
            )
        cases.append(
            FrozenCase(
                original["id"],
                kind,
                original["question"],
                original["history"],
                context,
                find(main_fragment) if main_fragment else None,
                profile["name"],
                profile["description"] or "",
            )
        )
    for spec in EXTRA_OPERATIONS:
        if only and spec.case_id not in only:
            continue
        ids = [find(spec.main), *(find(name) for name in spec.neighbours)]
        cases.append(
            FrozenCase(
                spec.case_id,
                "OPERATION",
                spec.question,
                [],
                await context_for(ids),
                ids[0],
                profile["name"],
                profile["description"] or "",
            )
        )
    meta = {
        "knowledge_id": str(kb_id),
        "index_id": str(index_id),
        "baseline": str(baseline_path),
        "generation_settings": baseline["generation_settings"],
    }
    return cases, meta
