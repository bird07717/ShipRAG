"""Concurrent fresh BASELINE/RELATIONS LLM2 capture, without quality grading.

Keep full documents/images and original policies. Only add manually source-grounded
object/condition/action/stage relations. These are NOT automatic extraction or
production cards; never insert question-specific expected answers into relations.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from run_evidence_input_review import FrozenDocuments, sha
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


def relation_guidance(
    annotations: dict[str, Any], blocks: dict[str, Any], context: Any
) -> dict[str, Any]:
    """Validate quotes/order against frozen full source, not answer semantics."""
    annotated = {d["document_id"]: d for d in annotations["documents"]}
    if len(annotated) != len(annotations["documents"]):
        raise ValueError("Duplicate document annotation")
    documents = []
    for key, did in zip(context.document_keys, context.document_ids, strict=True):
        if did not in annotated:
            continue
        elements = blocks[did]["elements"]
        by_id = {str(e["element_id"]): (i, e) for i, e in enumerate(elements)}
        for relation in annotated[did]["relations"]:
            positions = []
            if not relation["evidence"]:
                raise ValueError("Relation lacks source evidence")
            for evidence in relation["evidence"]:
                i, element = by_id[evidence["element_id"]]
                if (
                    element["element_type"] not in ("TEXT", "TABLE")
                    or evidence["quote"] != element["content"]
                    or evidence["quote"] not in context.prompt_context
                ):
                    raise ValueError("Annotation quote is not unchanged full source")
                positions.append(i)
            if positions != sorted(positions):
                raise ValueError("Annotation source order changed")
        documents.append(
            {"document_key": key, "relations": annotated[did]["relations"]}
        )
    return {"description": annotations["method"], "documents": documents}


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("rag-acceptance/reports/evidence-input-20261008/answers.json"),
    )
    parser.add_argument(
        "--annotations",
        type=Path,
        default=Path("rag-acceptance/condition-stage-annotations.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; preserve all previous results")
    if args.repeats < 1 or args.concurrency < 1:
        parser.error("repeats/concurrency must be positive")
    old_text = args.baseline.read_text()
    old = json.loads(old_text)
    annotations_text = args.annotations.read_text()
    annotations = json.loads(annotations_text)
    generator_hash = sha(inspect.getsource(answer_generator))
    if old.get("prepare_only") or generator_hash != old["answer_generator_sha256"]:
        raise ValueError("Require original completed source capture and business code")
    settings = Settings()
    generation = {
        "max_tokens": settings.m3_rag_answer_max_tokens,
        "timeout_seconds": settings.m3_rag_answer_timeout_seconds,
        "thinking_enabled": settings.m3_rag_answer_thinking_enabled,
        "thinking_budget": settings.m3_rag_answer_thinking_budget,
        "temperature": 0.1,
    }
    if generation != old["generation_settings"] or generation["thinking_enabled"]:
        raise ValueError("Require unchanged non-thinking generation configuration")
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "method": "fresh paired BASELINE/RELATIONS; full source; no quality scores",
        "source_capture": str(args.baseline),
        "source_capture_sha256": sha(old_text),
        "annotations_sha256": sha(annotations_text),
        "answer_generator_sha256": generator_hash,
        "prepare_only": args.prepare_only,
        "repeats": args.repeats,
        "generation_settings": generation,
        "knowledge_id": old["knowledge_id"],
        "index_id": old["index_id"],
        "results": [],
    }

    def save() -> None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    engine = create_async_engine(settings.database_url.get_secret_value())
    repo = RagRepository(engine, settings)
    try:
        kb_id, index_id = UUID(old["knowledge_id"]), UUID(old["index_id"])
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
            profile = dict(
                (
                    await db.execute(
                        text(
                            "SELECT name,description,COALESCE(rag_active_index_id,active_index_id) AS index_id "
                            "FROM knowledge_base WHERE id=:id"
                        ),
                        {"id": kb_id},
                    )
                )
                .mappings()
                .one()
            )
        label = {"provider": model["provider"], "name": model["model_name"]}
        if label != old["model"] or profile["index_id"] != index_id:
            raise ValueError("Model or frozen active index changed")
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
                raise ValueError("Require REAL original DashScope provider")
        finally:
            await check.aclose()
        report["model"] = label
        frozen = []
        blocks: dict[str, Any] = {}
        sources: dict[str, Any] = {}
        specs = [
            (r, "B" if r["id"] == "evidence_lru" else "A", None) for r in old["results"]
        ]
        by_id = {r["id"]: r for r in old["results"]}
        specs.extend(
            [
                (
                    by_id["evidence_lru"],
                    "B",
                    "按LRU通讯线缆检查文档，重新插拔并插回通讯和电源线缆后，要等待多久再检查DAU报警是否消除？只回答等待时间。",
                ),
                (
                    by_id["evidence_pdc"],
                    "A",
                    "PDC接线盒已检查确认腐蚀严重。按接线盒检查文档，这种情况下提到的临时处理办法是什么？只说明办法及适用条件，不展开接线步骤。",
                ),
            ]
        )
        for original, source_group, question_override in specs:
            old_context = original["contexts"][source_group]
            dids = old_context["document_ids"]
            for did in dids:
                if did not in blocks:
                    sources[did] = await repo.get_document_source(UUID(did))
                    if sources[did]["status"] != "STORED":
                        raise ValueError("Frozen source is no longer stored")
                    blocks[did] = await repo.get_document_blocks(
                        knowledge_id=kb_id, index_id=index_id, document_id=UUID(did)
                    )
            context = await build_document_context(
                FrozenDocuments(blocks, sources),
                knowledge_id=kb_id,
                index_id=index_id,
                candidates=[DocumentCandidate(UUID(d), 0, (), None) for d in dids],
                token_budget=min(
                    settings.m3_rag_full_context_token_budget,
                    settings.m3_prompt_token_budget,
                ),
            )
            if (
                context.context_mode != "FULL_DOCUMENT"
                or list(context.document_ids) != dids
            ):
                raise ValueError("Experiment must retain all documents in full")
            question = question_override or original["question"]
            prompt = answer_generator.build_answer_prompt(
                history=original["history"],
                interpretation=QueryInterpretation(
                    resolved_question=question,
                    scope="IN_SCOPE",
                    response_path="KNOWLEDGE",
                ),
                context=context,
                product_name=profile["name"],
                scope_description=profile["description"] or "",
                max_rounds=settings.m3_rag_history_max_rounds,
            )
            if sha(context.prompt_context) != old_context["context_sha256"]:
                raise ValueError("Full source changed since historical capture")
            if not question_override and sha(prompt) != old_context["prompt_sha256"]:
                raise ValueError("Original prompt changed since historical capture")
            guidance = relation_guidance(annotations, blocks, context)
            payload = json.loads(prompt)
            relational = json.dumps(
                {**payload, "source_relations": guidance},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            stripped = json.loads(relational)
            stripped.pop("source_relations")
            if stripped != payload:
                raise ValueError("Unexpected change outside source_relations")
            row = {
                "id": original["id"] + ("_positive" if question_override else ""),
                "source_group": source_group,
                "question": question,
                "history": original["history"],
                "context": old_context,
                "source_relations": guidance,
                "prompt_sha256": {
                    "BASELINE": sha(prompt),
                    "RELATIONS": sha(relational),
                },
                "runs": [],
            }
            report["results"].append(row)
            frozen.append((row, {"BASELINE": prompt, "RELATIONS": relational}))
        save()
        if args.prepare_only:
            print(f"Prepared {len(frozen)} paired full-source inputs; no paid calls")
            return
        semaphore = asyncio.Semaphore(args.concurrency)

        async def capture(
            row: dict[str, Any], prompts: dict[str, str], repeat: int, number: int
        ) -> None:
            order = (
                ["BASELINE", "RELATIONS"]
                if (repeat + number) % 2
                else ["RELATIONS", "BASELINE"]
            )
            run: dict[str, Any] = {"repeat": repeat, "order": order, "variants": {}}
            row["runs"].append(run)
            async with semaphore:
                for variant in order:
                    provider = create_llm_provider(settings, snapshot)
                    request_settings = []

                    async def record_request(request: Any) -> None:
                        body = json.loads(request.content)
                        request_settings.append(
                            {
                                k: body.get(k)
                                for k in (
                                    "model",
                                    "max_tokens",
                                    "temperature",
                                    "enable_thinking",
                                    "thinking_budget",
                                    "response_format",
                                )
                            }
                        )

                    provider.client.event_hooks["request"] = [record_request]
                    started = time.perf_counter()
                    try:
                        answer = await answer_generator.generate_answer(
                            provider,
                            prompt=prompts[variant],
                            max_tokens=generation["max_tokens"],
                            timeout_seconds=generation["timeout_seconds"],
                            thinking_enabled=False,
                            thinking_budget=generation["thinking_budget"],
                        )
                        result = {
                            "response": answer.model_dump(),
                            "usage": dict(provider.usage),
                        }
                    except Exception as exc:
                        result = {
                            "error": type(exc).__name__,
                            "code": getattr(exc, "code", None),
                            "usage": dict(provider.usage),
                        }
                    finally:
                        await provider.aclose()
                    result["latency_ms"] = round((time.perf_counter() - started) * 1000)
                    result["request_settings"] = request_settings
                    run["variants"][variant] = result
                    save()
                    print(
                        f"{row['id']} #{repeat} {variant}: {result.get('code') if 'error' in result else 'captured'}",
                        flush=True,
                    )

        await asyncio.gather(
            *[
                capture(row, prompts, repeat, number)
                for number, (row, prompts) in enumerate(frozen)
                for repeat in range(1, args.repeats + 1)
            ]
        )
        report["completed_at"] = datetime.now(UTC).isoformat()
        save()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
