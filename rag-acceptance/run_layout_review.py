"""REAL-LLM no-regression screen for the static-first prompt layout (ADR-0042).

The prompts of LLM 1 (query interpreter) and LLM 2 (technical answer) used to start with per-request
data and carry the rules, schema and example at the end. They now start with the static instructions
so the provider's implicit prefix cache can match across requests (see run_prompt_cache_probe.py).
Reordering the keys of a JSON prompt can change model behaviour, so this tool compares the old order
with the shipped one on the frozen inputs of the image-rule review (3 evidence-input cases + 2 full
operations), answer stage and interpreter stage, ``--repeats`` samples per case and arm.

Arms (same content, only the key order differs):
  LEGACY        the previous order, rebuilt from the shipped prompt by moving the keys back.
  STATIC_FIRST  the shipped order.

The decision to reorder was taken by the owner; this is a safety screen, not a go/no-go test.
Protocol fixed BEFORE the first paid call (do not edit after seeing results):
  * Gate 1, answer validity: errors, structured retries or production-validator rejections for
    STATIC_FIRST exceed LEGACY's by at most 1 over the 15 samples.
  * Gate 2, controls: FFC (172.16.8.175 and "a port drop alone cannot confirm hardware damage") and
    PSU (power-off, 3359, photographing/marking, replacing, five-minute check) hold in 3/3 for
    STATIC_FIRST.
  * Gate 3, non-operation cases: pooled mean delivered images of STATIC_FIRST at most LEGACY's + 1.0.
  * Gate 4, operation cases (9 samples per arm): runs delivering ALL main-document images at least
    LEGACY's minus 1 run; borrowed images per answer at most LEGACY's + 1.
  * Gate 5, interpreter: for every case the most common (scope, relation, response_path) of
    STATIC_FIRST equals LEGACY's and at least 2 of its 3 samples agree with it.
If a gate fails the result is reported as it is and the owner decides; nothing is reverted here.
No model judge. resolved_question wording differences are printed to be read, not gated.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics as st
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from frozen_inputs import (
    FrozenCase,
    legacy_layout,
    load_cases,
    load_llm,
    static_first_layout,
)
from run_image_rule_review import core_ok, deliver
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.rag.document_card_repository import DocumentCardRepository
from app.rag.llm import create_llm_provider
from app.rag.repository import RagRepository
from app.rag.v2 import answer_generator
from app.rag.v2.query_interpreter import (
    INTERPRETER_SYSTEM_PROMPT,
    QueryInterpretation,
    build_interpreter_prompt,
)

ARMS = ("LEGACY", "STATIC_FIRST")


def build_prompts(case: FrozenCase, settings: Settings) -> dict[str, dict[str, str]]:
    answer = answer_generator.build_answer_prompt(
        history=case.history,
        interpretation=QueryInterpretation(
            resolved_question=case.question, scope="IN_SCOPE", response_path="KNOWLEDGE"
        ),
        context=case.context,
        product_name=case.product_name,
        scope_description=case.scope_description,
        max_rounds=settings.m3_rag_history_max_rounds,
    )
    interpreter = build_interpreter_prompt(
        question=case.question,
        history=case.history,
        knowledge_name=case.product_name,
        scope_description=case.scope_description,
        max_rounds=settings.m3_rag_history_max_rounds,
    )
    return {
        "LLM2": {
            "STATIC_FIRST": static_first_layout(answer, "LLM2"),
            "LEGACY": legacy_layout(answer, "LLM2"),
        },
        "LLM1": {
            "STATIC_FIRST": static_first_layout(interpreter, "LLM1"),
            "LEGACY": legacy_layout(interpreter, "LLM1"),
        },
    }


def summarize(path: Path) -> None:
    report = json.loads(path.read_text())
    rows = {c["case_id"]: c for c in report["cases"]}

    def samples(stage: str, case_id: str, arm: str) -> list[dict[str, Any]]:
        return [r[stage][arm] for r in rows[case_id]["runs"] if arm in r.get(stage, {})]

    print("== GATE 1 answer validity (errors, retries, validator rejections)")
    for arm in ARMS:
        bad = n = 0
        for case_id in rows:
            for s in samples("LLM2", case_id, arm):
                n += 1
                bad += (
                    "error" in s
                    or "rejected" in s.get("delivery", {})
                    or s.get("usage", {}).get("structured_attempts", 1) > 1
                )
        print(f"  {arm:13} problems {bad}/{n}")

    print("\n== GATE 2 controls")
    for arm in ARMS:
        parts = []
        for case_id in ("evidence_ffc", "evidence_psu"):
            vals = [
                core_ok(case_id, s["response"]["answer"])
                for s in samples("LLM2", case_id, arm)
                if "response" in s
            ]
            parts.append(f"{case_id}={sum(bool(v) for v in vals)}/{len(vals)}")
        print(f"  {arm:13} " + "  ".join(parts))

    print("\n== delivered images per case (each number is one run)")
    for case_id, case in rows.items():
        print(
            f"  {case_id} [{case['kind']}] main images: {len(case['main_image_keys'])}"
        )
        for arm in ARMS:
            ok = [
                s["delivery"]
                for s in samples("LLM2", case_id, arm)
                if "delivered" in s.get("delivery", {})
            ]
            counts = [d["delivered"] for d in ok]
            if counts:
                print(
                    f"    {arm:13} {counts} mean {st.mean(counts):.1f} sd {st.pstdev(counts):.2f}"
                )
    print(
        "\n== GATE 3 non-operation pooled mean; GATE 4 operation completeness and borrowing"
    )
    for arm in ARMS:
        non_op: list[int] = []
        complete = total = borrowed = 0
        for case_id, case in rows.items():
            for s in samples("LLM2", case_id, arm):
                d = s.get("delivery", {})
                if "delivered" not in d:
                    continue
                if case["kind"] == "NON_OPERATION":
                    non_op.append(d["delivered"])
                else:
                    total += 1
                    complete += set(case["main_image_keys"]) <= set(d["main_keys"])
                    borrowed += d["borrowed"]
        print(
            f"  {arm:13} non-op mean {st.mean(non_op):.2f} (n={len(non_op)})   "
            f"operation complete {complete}/{total}   borrowed/answer {borrowed / max(total, 1):.2f}"
        )

    print("\n== GATE 5 interpreter: (scope, relation, response_path) per case and arm")
    for case_id in rows:
        modal: dict[str, tuple[Any, int]] = {}
        for arm in ARMS:
            tuples = [
                (s["scope"], s["relation"], s["response_path"])
                for s in samples("LLM1", case_id, arm)
                if "scope" in s
            ]
            counts = Counter(tuples)
            modal[arm] = counts.most_common(1)[0] if counts else (None, 0)
            print(f"  {case_id:15} {arm:13} {dict(counts)}")
        same = modal["LEGACY"][0] == modal["STATIC_FIRST"][0]
        print(
            f"  {'':15} modal equal={same}  STATIC_FIRST agreement {modal['STATIC_FIRST'][1]}/3"
        )

    print("\n== resolved_question (read these)")
    for case_id in rows:
        print(f"  {case_id}")
        for arm in ARMS:
            for s in samples("LLM1", case_id, arm):
                print(f"    {arm:13} {s.get('resolved_question', s.get('error'))}")

    print(
        "\n== reported: completion tokens, latency (mean) and cached tokens (sum), answer stage"
    )
    for arm in ARMS:
        runs = [s for c in rows for s in samples("LLM2", c, arm) if "usage" in s]
        toks = [s["usage"].get("completion_tokens", 0) for s in runs]
        lat = [s["latency_ms"] for s in runs]
        cached = [
            (s["usage"].get("prompt_tokens_details") or {}).get("cached_tokens", 0)
            for s in runs
        ]
        print(
            f"  {arm:13} completion {st.mean(toks):.0f}  latency {st.mean(lat):.0f} ms  cached {sum(cached)}"
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summarize", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()
    if args.summarize:
        summarize(args.summarize)
        return
    if args.output is None:
        parser.error("--output is required")
    if args.output.exists():
        parser.error(
            "Output already exists; preserve previous runs with a new filename"
        )

    settings = Settings()
    engine = create_async_engine(settings.database_url.get_secret_value())
    repo = RagRepository(engine, settings)
    card_repo = DocumentCardRepository(engine, settings)
    try:
        llm = await load_llm(settings, engine, repo)
        cases, meta = await load_cases(settings, engine, repo, card_repo)
        generation = {
            "max_tokens": settings.m3_rag_answer_max_tokens,
            "timeout_seconds": settings.m3_rag_answer_timeout_seconds,
            "thinking_enabled": settings.m3_rag_answer_thinking_enabled,
            "thinking_budget": settings.m3_rag_answer_thinking_budget,
            "temperature": 0.1,
        }
        if generation != meta["generation_settings"] or generation["thinking_enabled"]:
            raise ValueError(
                "Current generation settings differ from the non-thinking baseline"
            )
        prompts = {c.case_id: build_prompts(c, settings) for c in cases}
        report: dict[str, Any] = {
            "started_at": datetime.now(UTC).isoformat(),
            "method": "static-first layout vs legacy order; no quality grading",
            "arms": list(ARMS),
            "repeats": args.repeats,
            "generation_settings": generation,
            "model": llm.label,
            "cases": [
                {
                    "case_id": c.case_id,
                    "kind": c.kind,
                    "question": c.question,
                    "main_image_keys": c.main_image_keys(),
                    "runs": [],
                }
                for c in cases
            ],
        }

        def save() -> None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        save()
        if args.prepare_only:
            for c in cases:
                for stage in ("LLM2", "LLM1"):
                    first = {
                        arm: next(iter(json.loads(prompts[c.case_id][stage][arm])))
                        for arm in ARMS
                    }
                    same = sorted(
                        json.loads(prompts[c.case_id][stage]["LEGACY"])
                    ) == sorted(json.loads(prompts[c.case_id][stage]["STATIC_FIRST"]))
                    print(f"{c.case_id:15} {stage} first keys {first} same keys {same}")
            print(
                f"Prepared {len(cases)} cases x {len(ARMS)} arms x 2 stages; no paid calls"
            )
            return

        semaphore = asyncio.Semaphore(args.concurrency)
        by_id = {c.case_id: c for c in cases}

        async def one(case: FrozenCase, repeat: int, run: dict[str, Any]) -> None:
            shift = (repeat + len(case.case_id)) % len(ARMS)
            order = [*ARMS[shift:], *ARMS[:shift]]
            async with semaphore:
                for arm in order:
                    for stage in ("LLM2", "LLM1"):
                        provider = create_llm_provider(settings, llm.snapshot)
                        started = time.perf_counter()
                        prompt = prompts[case.case_id][stage][arm]
                        try:
                            if stage == "LLM2":
                                generated = await answer_generator.generate_answer(
                                    provider,
                                    prompt=prompt,
                                    max_tokens=generation["max_tokens"],
                                    timeout_seconds=generation["timeout_seconds"],
                                    thinking_enabled=False,
                                    thinking_budget=generation["thinking_budget"],
                                )
                                response = generated.model_dump()
                                result: dict[str, Any] = {
                                    "response": response,
                                    "usage": dict(provider.usage),
                                    "delivery": deliver(by_id[case.case_id], response),
                                }
                            else:
                                parsed = await provider.complete_structured(
                                    prompt,
                                    system=INTERPRETER_SYSTEM_PROMPT,
                                    schema=QueryInterpretation,
                                    max_tokens=settings.m3_rag_query_interpreter_max_tokens,
                                    temperature=0.0,
                                    timeout_seconds=settings.m3_rag_query_interpreter_timeout_seconds,
                                )
                                result = {
                                    **parsed.model_dump(),
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
                        result["latency_ms"] = round(
                            (time.perf_counter() - started) * 1000
                        )
                        run.setdefault(stage, {})[arm] = result
                        save()
                        note = result.get("code") if "error" in result else "captured"
                        print(
                            f"{case.case_id} #{repeat} {stage} {arm}: {note}",
                            flush=True,
                        )

        tasks = []
        for i, case in enumerate(cases):
            for repeat in range(1, args.repeats + 1):
                run: dict[str, Any] = {"repeat": repeat}
                report["cases"][i]["runs"].append(run)
                tasks.append(one(case, repeat, run))
        await asyncio.gather(*tasks)
        report["completed_at"] = datetime.now(UTC).isoformat()
        save()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
