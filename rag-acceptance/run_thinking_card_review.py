"""REAL LLM2 experiment reusing frozen historical answers, without quality scoring.

THINKING: identical original prompt with existing thinking mode enabled.
CARDS: thinking disabled; unchanged full context plus existing card metadata.
Use the five observed A inputs and the correct LRU B input, three repeats each.
No retrieval, card generation/edit, business configuration write, or model judge.
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
from app.rag.document_card_repository import DocumentCardRepository
from app.rag.document_cards import (
    DOCUMENT_CARD_SCHEMA_VERSION,
    DocumentCardPayload,
    content_fingerprint,
)
from app.rag.llm import FakeLlmProvider, create_llm_provider
from app.rag.models import ModelSnapshot
from app.rag.repository import RagRepository
from app.rag.v2 import answer_generator
from app.rag.v2.document_context import build_document_context
from app.rag.v2.document_retrieval import DocumentCandidate
from app.rag.v2.query_interpreter import QueryInterpretation

CARD_FIELDS = ("document_type", "summary", "tasks", "prerequisites", "exclusions")


def fingerprint_kind(card: dict[str, Any], document: dict[str, Any]) -> str:
    """Check current hash or the verified pre-badeaad index-bound algorithm."""
    if card["content_fingerprint"] == content_fingerprint(document):
        return "CURRENT"
    legacy = {
        "schema_version": DOCUMENT_CARD_SCHEMA_VERSION,
        "index_id": str(document["index_id"]),
        "source_hash": str(document.get("source_hash") or ""),
        "content": str(document.get("content") or ""),
        "sections": list(document.get("sections") or []),
        "image_count": int(document.get("image_count") or 0),
    }
    if card["content_fingerprint"] == sha(
        json.dumps(legacy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    ):
        return "LEGACY_INDEX_BOUND"
    raise ValueError("Existing card fingerprint matches neither source algorithm")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("rag-acceptance/reports/evidence-input-20261008/answers.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(
            "Output already exists; preserve previous runs with a new filename"
        )
    if args.repeats < 1 or args.concurrency < 1:
        parser.error("repeats and concurrency must be positive")
    baseline_text = args.baseline.read_text()
    baseline = json.loads(baseline_text)
    if baseline.get("prepare_only") or baseline.get("repeats") != 3:
        raise ValueError("Expected completed three-repeat original baseline")
    generator_hash = sha(inspect.getsource(answer_generator))
    if generator_hash != baseline["answer_generator_sha256"]:
        raise ValueError("Business answer generator changed since the baseline")
    settings = Settings()
    generation = {
        "max_tokens": settings.m3_rag_answer_max_tokens,
        "timeout_seconds": settings.m3_rag_answer_timeout_seconds,
        "thinking_enabled": settings.m3_rag_answer_thinking_enabled,
        "thinking_budget": settings.m3_rag_answer_thinking_budget,
        "temperature": 0.1,
    }
    if generation != baseline["generation_settings"] or generation["thinking_enabled"]:
        raise ValueError(
            "Current generation settings differ from non-thinking baseline"
        )
    engine = create_async_engine(settings.database_url.get_secret_value())
    repo = RagRepository(engine, settings)
    card_repo = DocumentCardRepository(engine, settings)
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "method": "historical baseline; THINKING or CARDS only; no quality grading",
        "baseline": str(args.baseline),
        "baseline_sha256": sha(baseline_text),
        "answer_generator_sha256": generator_hash,
        "prepare_only": args.prepare_only,
        "repeats": args.repeats,
        "generation_settings": generation,
        "card_fields": CARD_FIELDS,
        "knowledge_id": baseline["knowledge_id"],
        "index_id": baseline["index_id"],
        "card_snapshots": [],
        "results": [],
    }

    def save() -> None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    try:
        kb_id, index_id = UUID(baseline["knowledge_id"]), UUID(baseline["index_id"])
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
        model_label = {"provider": model["provider"], "name": model["model_name"]}
        if model_label != baseline["model"] or profile["index_id"] != index_id:
            raise ValueError("Model or active index changed since baseline")
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
                raise ValueError("Require the original REAL DashScope model")
            if not getattr(check, "_supports_thinking", False):
                raise ValueError("Configured provider cannot request thinking")
        finally:
            await check.aclose()
        report["model"] = model_label
        documents = {
            str(d["document_id"]): d
            for d in await card_repo.generation_documents(kb_id, index_id)
        }
        cards = {
            str(c["document_id"]): c
            for c in await card_repo.list_cards(kb_id, index_id)
        }
        blocks: dict[str, Any] = {}
        sources: dict[str, Any] = {}
        frozen: list[tuple[dict[str, Any], dict[str, str]]] = []
        selected_cards: set[str] = set()
        for original in baseline["results"]:
            groups = ["A", "B"] if original["id"] == "evidence_lru" else ["A"]
            for source_group in groups:
                old = original["contexts"][source_group]
                dids = old["document_ids"]
                for did in dids:
                    if did not in blocks:
                        sources[did] = await repo.get_document_source(UUID(did))
                        if sources[did]["status"] != "STORED":
                            raise ValueError("Source document is no longer stored")
                        blocks[did] = await repo.get_document_blocks(
                            knowledge_id=kb_id, index_id=index_id, document_id=UUID(did)
                        )
                    card = cards[did]
                    if (
                        card["generation_status"] != "READY"
                        or card["review_status"] != "CONFIRMED"
                        or card["schema_version"] != DOCUMENT_CARD_SCHEMA_VERSION
                    ):
                        raise ValueError("Card not confirmed/current for frozen source")
                    fingerprint_status = fingerprint_kind(card, documents[did])
                    DocumentCardPayload.model_validate(card["card_payload"])
                    if did not in selected_cards:
                        report["card_snapshots"].append(
                            {
                                "document_id": did,
                                "revision": card["revision"],
                                "review_status": card["review_status"],
                                "content_fingerprint": card["content_fingerprint"],
                                "fingerprint_status": fingerprint_status,
                                "card_payload": card["card_payload"],
                            }
                        )
                        selected_cards.add(did)
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
                prompt = answer_generator.build_answer_prompt(
                    history=original["history"],
                    interpretation=QueryInterpretation(
                        resolved_question=original["question"],
                        scope="IN_SCOPE",
                        response_path="KNOWLEDGE",
                    ),
                    context=context,
                    product_name=profile["name"],
                    scope_description=profile["description"] or "",
                    max_rounds=settings.m3_rag_history_max_rounds,
                )
                if (
                    sha(context.prompt_context) != old["context_sha256"]
                    or sha(prompt) != old["prompt_sha256"]
                ):
                    raise ValueError(
                        "Current full context/original prompt differs from baseline"
                    )
                guidance = [
                    {
                        "document_key": key,
                        **{
                            field: cards[did]["card_payload"][field]
                            for field in CARD_FIELDS
                        },
                    }
                    for key, did in zip(context.document_keys, dids, strict=True)
                ]
                card_payload = {**json.loads(prompt), "document_guidance": guidance}
                card_prompt = json.dumps(
                    card_payload, ensure_ascii=False, separators=(",", ":")
                )
                without_cards = json.loads(card_prompt)
                without_cards.pop("document_guidance")
                if without_cards != json.loads(prompt):
                    raise ValueError(
                        "Card variant unexpectedly changed original inputs"
                    )
                if any("error" in r["groups"][source_group] for r in original["runs"]):
                    raise ValueError(
                        "Selected historical baseline contains capture errors"
                    )
                row = {
                    "id": original["id"],
                    "source_group": source_group,
                    "question": original["question"],
                    "history": original["history"],
                    "context": old,
                    "document_guidance": guidance,
                    "prompt_sha256": {
                        "THINKING": sha(prompt),
                        "CARDS": sha(card_prompt),
                    },
                    "runs": [],
                }
                report["results"].append(row)
                frozen.append((row, {"THINKING": prompt, "CARDS": card_prompt}))
        save()
        if args.prepare_only:
            print(
                f"Prepared {len(frozen)} fixed inputs and {len(selected_cards)} existing cards; no paid calls"
            )
            return
        semaphore = asyncio.Semaphore(args.concurrency)

        async def capture(
            row: dict[str, Any], prompts: dict[str, str], repeat: int, number: int
        ) -> None:
            order = (
                ["THINKING", "CARDS"]
                if (repeat + number) % 2
                else ["CARDS", "THINKING"]
            )
            run: dict[str, Any] = {"repeat": repeat, "order": order, "variants": {}}
            row["runs"].append(run)
            async with semaphore:
                for variant in order:
                    provider = create_llm_provider(settings, snapshot)
                    request_settings: list[dict[str, Any]] = []

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
                        generated = await answer_generator.generate_answer(
                            provider,
                            prompt=prompts[variant],
                            max_tokens=generation["max_tokens"],
                            timeout_seconds=generation["timeout_seconds"],
                            thinking_enabled=variant == "THINKING",
                            thinking_budget=generation["thinking_budget"],
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
                        if getattr(exc, "detail", None) == "reasoning_budget_exhausted":
                            result["reason"] = "reasoning_budget_exhausted"
                        result["usage"] = dict(provider.usage)
                    finally:
                        await provider.aclose()
                    result["latency_ms"] = round((time.perf_counter() - started) * 1000)
                    result["request_settings"] = request_settings
                    run["variants"][variant] = result
                    save()
                    status = result.get("code") if "error" in result else "captured"
                    print(
                        f"{row['id']} {row['source_group']} #{repeat} {variant}: {status}",
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
