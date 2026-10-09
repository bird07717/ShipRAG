"""REAL LLM2 A/B/C evidence-input experiment; no quality scoring or judge.

A: observed full candidate documents; B: manually verified single full document;
C: ordered, verbatim source elements selected in the manifest (possibly empty).
Prompt policies, task, history, model and generation settings remain unchanged.
The manually curated C group is diagnostic, NOT a production evidence selector.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.rag.llm import FakeLlmProvider, create_llm_provider
from app.rag.models import ModelSnapshot
from app.rag.repository import RagRepository
from app.rag.v2 import answer_generator
from app.rag.v2.document_context import build_document_context
from app.rag.v2.document_retrieval import DocumentCandidate
from app.rag.v2.query_interpreter import QueryInterpretation


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class FrozenDocuments:
    """Read-only, in-memory documents; selected elements retain source order."""

    def __init__(
        self,
        blocks: dict[str, Any],
        sources: dict[str, Any],
        selected: set[str] | None = None,
    ) -> None:
        self.blocks = blocks
        self.sources = sources
        self.selected = selected

    async def get_document_blocks(
        self, *, document_id: UUID, **kwargs: Any
    ) -> dict[str, Any]:
        document = self.blocks[str(document_id)]
        if self.selected is None:
            return document
        return {
            **document,
            "elements": [
                element
                for element in document["elements"]
                if str(element["element_id"]) in self.selected
            ],
        }

    async def get_document_source(self, document_id: UUID) -> dict[str, Any]:
        return self.sources[str(document_id)]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases", type=Path, default=Path("rag-acceptance/evidence-input-cases.jsonl")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; use a new filename to preserve all runs")
    if args.repeats < 1 or args.concurrency < 1:
        parser.error("repeats and concurrency must be positive")
    specs = [
        json.loads(line) for line in args.cases.read_text().splitlines() if line.strip()
    ]
    settings = Settings()
    engine = create_async_engine(settings.database_url.get_secret_value())
    repo = RagRepository(engine, settings)
    report: dict[str, Any] = {
        "method": "LLM2 only, evidence A/B/C; original policies; no quality assertions/scores",
        "prepare_only": args.prepare_only,
        "manifest_sha256": sha(args.cases.read_text()),
        "answer_generator_sha256": sha(inspect.getsource(answer_generator)),
        "repeats": args.repeats,
        "results": [],
    }

    def save() -> None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    try:
        captures = {
            spec["source_capture"]: json.loads(Path(spec["source_capture"]).read_text())
            for spec in specs
        }
        kb_ids = {capture["knowledge_id"] for capture in captures.values()}
        if len(kb_ids) != 1:
            raise ValueError("Source captures must share the same knowledge base")
        kb_id = next(iter(kb_ids))
        async with engine.connect() as connection:
            model = dict(
                (
                    await connection.execute(
                        text(
                            "SELECT * FROM model_config WHERE model_type='LLM' AND enabled"
                        )
                    )
                )
                .mappings()
                .one()
            )
            profile = dict(
                (
                    await connection.execute(
                        text(
                            "SELECT name, description, COALESCE(rag_active_index_id,active_index_id) AS index_id "
                            "FROM knowledge_base WHERE id=:id"
                        ),
                        {"id": UUID(kb_id)},
                    )
                )
                .mappings()
                .one()
            )
        index_id = str(profile["index_id"])
        snapshot = ModelSnapshot(
            provider=model["provider"],
            model_name=model["model_name"],
            parameters=model["parameters"] or {},
            base_url=model["base_url"],
            api_key=repo._decrypt_api_key(model),
        )
        check = create_llm_provider(settings, snapshot)
        fake = isinstance(check, FakeLlmProvider)
        await check.aclose()
        if fake:
            raise RuntimeError("This experiment requires REAL LLM")
        report.update(
            {
                "knowledge_id": kb_id,
                "index_id": index_id,
                "model": {"provider": snapshot.provider, "name": snapshot.model_name},
                "generation_settings": {
                    "max_tokens": settings.m3_rag_answer_max_tokens,
                    "timeout_seconds": settings.m3_rag_answer_timeout_seconds,
                    "thinking_enabled": settings.m3_rag_answer_thinking_enabled,
                    "thinking_budget": settings.m3_rag_answer_thinking_budget,
                    "temperature": 0.1,
                },
            }
        )
        blocks: dict[str, Any] = {}
        sources: dict[str, Any] = {}
        frozen_cases: list[tuple[dict[str, Any], dict[str, str]]] = []
        # Freeze and validate ALL inputs before issuing any paid model request.
        for spec in specs:
            capture = captures[spec["source_capture"]]
            case = next(
                item for item in capture["results"] if item["id"] == spec["source_case"]
            )
            turn = case["turns"][0]
            if turn.get("error") or not turn.get("diagnostics"):
                raise ValueError(
                    "Source capture is missing a successful observed trace"
                )
            diag = turn["diagnostics"]
            if diag["index_id"] != index_id:
                raise ValueError("Index changed since capture")
            actual_ids = diag["runtime_snapshot"]["generation"]["context"][
                "document_ids"
            ]
            single = spec["single_document_id"]
            for did in dict.fromkeys([*actual_ids, single]):
                if did not in blocks:
                    sources[did] = await repo.get_document_source(UUID(did))
                    if sources[did]["status"] != "STORED":
                        raise ValueError("Source is no longer stored")
                    blocks[did] = await repo.get_document_blocks(
                        knowledge_id=UUID(kb_id),
                        index_id=UUID(index_id),
                        document_id=UUID(did),
                    )
            selected_ids = spec["selected_element_ids"]
            selected = None if selected_ids == "ALL" else set(selected_ids)
            known_ids = {
                str(element["element_id"]) for element in blocks[single]["elements"]
            }
            if selected is not None and selected - known_ids:
                raise ValueError("Selected element IDs not present in frozen document")
            repos = {
                "A": FrozenDocuments(blocks, sources),
                "B": FrozenDocuments(blocks, sources),
                "C": FrozenDocuments(blocks, sources, selected),
            }
            ids = {
                "A": actual_ids,
                "B": [single],
                "C": [single] if selected is None or selected else [],
            }
            contexts = {}
            for group in ("A", "B", "C"):
                contexts[group] = await build_document_context(
                    repos[group],
                    knowledge_id=UUID(kb_id),
                    index_id=UUID(index_id),
                    candidates=[
                        DocumentCandidate(UUID(did), 0, (), None) for did in ids[group]
                    ],
                    token_budget=min(
                        settings.m3_rag_full_context_token_budget,
                        settings.m3_prompt_token_budget,
                    ),
                )
            observed_hash = diag["runtime_snapshot"]["generation"]["context"][
                "content_sha256"
            ]
            if sha(contexts["A"].prompt_context) != observed_hash:
                raise ValueError("A no longer matches the actually observed context")
            interpretation = QueryInterpretation(
                resolved_question=turn["question"],
                scope="IN_SCOPE",
                response_path="KNOWLEDGE",
            )
            prompts = {
                group: answer_generator.build_answer_prompt(
                    history=[],
                    interpretation=interpretation,
                    context=context,
                    product_name=profile["name"],
                    scope_description=profile["description"] or "",
                    max_rounds=settings.m3_rag_history_max_rounds,
                )
                for group, context in contexts.items()
            }
            # Method invariant only, never an answer correctness assertion.
            non_evidence = []
            for prompt in prompts.values():
                payload = json.loads(prompt)
                for key in (
                    "hidden_document_context",
                    "available_document_keys",
                    "available_image_keys",
                ):
                    payload.pop(key)
                non_evidence.append(payload)
            if non_evidence[0] != non_evidence[1] or non_evidence[1] != non_evidence[2]:
                raise ValueError("Non-evidence inputs changed between groups")
            row = {
                **spec,
                "question": turn["question"],
                "history": [],
                "single_is_in_A": single in actual_ids,
                "contexts": {
                    group: {
                        "document_ids": list(context.document_ids),
                        "context_sha256": sha(context.prompt_context),
                        "prompt_sha256": sha(prompts[group]),
                        "estimated_tokens": context.estimated_tokens,
                        "image_count": len(context.image_bindings),
                        "mode": context.context_mode,
                    }
                    for group, context in contexts.items()
                },
                "runs": [],
            }
            report["results"].append(row)
            frozen_cases.append((row, prompts))
        save()
        if args.prepare_only:
            print(
                f"Prepared {len(frozen_cases)} scenarios; no paid LLM calls", flush=True
            )
            return
        semaphore = asyncio.Semaphore(args.concurrency)

        async def capture_triad(
            row: dict[str, Any], prompts: dict[str, str], repeat: int, number: int
        ) -> None:
            shift = (repeat - 1 + number) % 3
            order = list("ABC")[shift:] + list("ABC")[:shift]
            run: dict[str, Any] = {"repeat": repeat, "order": order, "groups": {}}
            row["runs"].append(run)
            async with semaphore:
                for group in order:
                    provider = create_llm_provider(settings, snapshot)
                    started = time.perf_counter()
                    try:
                        generated = await answer_generator.generate_answer(
                            provider,
                            prompt=prompts[group],
                            max_tokens=settings.m3_rag_answer_max_tokens,
                            timeout_seconds=settings.m3_rag_answer_timeout_seconds,
                            thinking_enabled=settings.m3_rag_answer_thinking_enabled,
                            thinking_budget=settings.m3_rag_answer_thinking_budget,
                        )
                        result = {
                            "response": generated.model_dump(),
                            "usage": dict(provider.usage),
                        }
                    except Exception as exc:
                        result = {
                            "error": type(exc).__name__,
                            "code": getattr(exc, "code", None),
                        }
                    finally:
                        await provider.aclose()
                    result["latency_ms"] = round((time.perf_counter() - started) * 1000)
                    run["groups"][group] = result
                    save()
                    print(
                        f"{row['id']} #{repeat} {group}: {'ERROR' if 'error' in result else 'captured'}",
                        flush=True,
                    )

        await asyncio.gather(
            *[
                capture_triad(row, prompts, repeat, number)
                for number, (row, prompts) in enumerate(frozen_cases)
                for repeat in range(1, args.repeats + 1)
            ]
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
