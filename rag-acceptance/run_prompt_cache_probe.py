"""REAL-LLM probe of DashScope's implicit prefix cache for the two answer-path prompts.

Question: if the static instruction block (rules, schema, example) came FIRST and the per-request
data LAST, would the implicit cache hit across DIFFERENT requests, and for how long?

Facts taken from the provider's documentation (Alibaba Cloud Model Studio, "Context Cache"):
implicit cache is automatic and cannot be disabled, needs at least 256 tokens of shared prefix,
bills a hit at 20% of the standard input price, matches by prefix and is best effort.

Today's layouts put the variable data first: LLM 1 starts with ``current_question`` and LLM 2 puts its
12 rules and schema (about 1.8k estimated tokens) AFTER a document of up to ten thousand tokens, so
different requests share almost no prefix. Measured hit rates in real traces are 9-11% of prompt tokens
for LLM 2 and 11-24% for LLM 1, and they come from the same request being sent again.

Streams (independent, they differ from the first byte): {LLM2, LLM1} x {CURRENT, STATIC_FIRST}.
STATIC_FIRST only reorders the keys of the same JSON prompt: the static keys first, the rest in their
original order. Steps at +0, +30, +60, +90 seconds and +5, +15, +30 minutes. Every call uses another
frozen case and a unique nonce appended to the question, so no two calls share a complete prompt and
earlier experiments cannot have warmed the cache.

Protocol fixed BEFORE the first paid call (do not edit after seeing results):
  * Mechanism works for a stage if STATIC_FIRST reports cached_tokens >= 1000 (LLM2) or >= 800 (LLM1)
    in at least 2 of the 3 calls at +30/+60/+90 s while CURRENT reports < 256 on all calls of that
    stage (the first call of a stream only warms the cache and is not counted).
  * Lifetime = the largest offset at which STATIC_FIRST still reports a hit of that size.
  * Reordering the prompt in production additionally requires that real traffic revisits the prefix
    within the lifetime (to be measured on real users) and that a quality comparison passes. This tool
    only measures the mechanism and the lifetime; it evaluates no answer quality.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from frozen_inputs import (
    FrozenCase,
    legacy_layout,
    load_cases,
    load_llm,
    static_first_layout,
    STATIC_KEYS,
)
from sqlalchemy.ext.asyncio import create_async_engine

from app.common.text import estimate_tokens
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

OFFSETS_SECONDS = (0, 30, 60, 90, 300, 900, 1800)
THRESHOLD = {"LLM2": 1000, "LLM1": 800}


def build(stage: str, case: FrozenCase, settings: Settings, nonce: str) -> str:
    question = f"{case.question}（探针编号 {nonce}）"
    if stage == "LLM2":
        return answer_generator.build_answer_prompt(
            history=case.history,
            interpretation=QueryInterpretation(
                resolved_question=question, scope="IN_SCOPE", response_path="KNOWLEDGE"
            ),
            context=case.context,
            product_name=case.product_name,
            scope_description=case.scope_description,
            max_rounds=settings.m3_rag_history_max_rounds,
        )
    return build_interpreter_prompt(
        question=question,
        history=case.history,
        knowledge_name=case.product_name,
        scope_description=case.scope_description,
        max_rounds=settings.m3_rag_history_max_rounds,
    )


def summarize(path: Path) -> None:
    report = json.loads(path.read_text())
    print(
        "cached_tokens / prompt_tokens at each step (the first step only warms the cache)"
    )
    header = "  ".join(f"+{o:>4}s" for o in report["offsets_seconds"])
    print(f"  {'stream':22} {header}")
    verdict: dict[str, dict[str, Any]] = {}
    for stream, steps in report["streams"].items():
        cells = []
        for step in steps:
            u = step.get("usage", {})
            cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
            cells.append(
                f"{cached:>5}/{u.get('prompt_tokens', 0):<5}"
                if "usage" in step
                else "  error  "
            )
        print(f"  {stream:22} " + "  ".join(cells))
        stage, layout = stream.split("/")
        verdict.setdefault(stage, {})[layout] = [
            (
                step["offset"],
                (step.get("usage", {}).get("prompt_tokens_details") or {}).get(
                    "cached_tokens", 0
                ),
            )
            for step in steps
        ]
    print("\nreading (pre-registered)")
    for stage, layouts in verdict.items():
        close = [c for o, c in layouts["STATIC_FIRST"] if o in (30, 60, 90)]
        hits = sum(c >= THRESHOLD[stage] for c in close)
        current_max = max((c for o, c in layouts["CURRENT"] if o > 0), default=0)
        works = hits >= 2 and current_max < 256
        lifetime = max(
            (o for o, c in layouts["STATIC_FIRST"] if o > 0 and c >= THRESHOLD[stage]),
            default=None,
        )
        print(
            f"  {stage}: STATIC_FIRST hits>={THRESHOLD[stage]} in {hits}/3 close calls; "
            f"CURRENT max after warm-up = {current_max}; mechanism works = {works}; "
            f"longest offset still hitting = {lifetime}"
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summarize", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
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
        cases, _meta = await load_cases(settings, engine, repo, card_repo)
        report: dict[str, Any] = {
            "started_at": datetime.now(UTC).isoformat(),
            "model": llm.label,
            "offsets_seconds": list(OFFSETS_SECONDS),
            "static_keys": {k: list(v) for k, v in STATIC_KEYS.items()},
            "streams": {
                f"{s}/{layout}": []
                for s in ("LLM2", "LLM1")
                for layout in ("CURRENT", "STATIC_FIRST")
            },
        }

        def prompt_for(stream: str, step: int) -> tuple[str, str]:
            stage, layout = stream.split("/")
            case = cases[step % len(cases)]
            prompt = build(stage, case, settings, uuid.uuid4().hex[:8])
            prompt = (
                static_first_layout if layout == "STATIC_FIRST" else legacy_layout
            )(prompt, stage)
            return case.case_id, prompt

        def save() -> None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        if args.prepare_only:
            for stream in report["streams"]:
                case_id, prompt = prompt_for(stream, 0)
                data = json.loads(prompt)
                first = next(iter(data))
                static_tokens = sum(
                    estimate_tokens(json.dumps(data[k], ensure_ascii=False))
                    for k in STATIC_KEYS[stream.split("/")[0]]
                )
                print(
                    f"{stream:22} case {case_id:16} first key {first:22} total est. {estimate_tokens(prompt):>6} tok, static est. {static_tokens}"
                )
            print(f"Prepared 4 streams x {len(OFFSETS_SECONDS)} steps; no paid calls")
            return

        async def call(stream: str, step: int, offset: int) -> None:
            stage, _ = stream.split("/")
            case_id, prompt = prompt_for(stream, step)
            provider = create_llm_provider(settings, llm.snapshot)
            started = time.perf_counter()
            record: dict[str, Any] = {"offset": offset, "case_id": case_id}
            try:
                if stage == "LLM2":
                    await answer_generator.generate_answer(
                        provider,
                        prompt=prompt,
                        max_tokens=settings.m3_rag_answer_max_tokens,
                        timeout_seconds=settings.m3_rag_answer_timeout_seconds,
                        thinking_enabled=False,
                        thinking_budget=settings.m3_rag_answer_thinking_budget,
                    )
                else:
                    await provider.complete_structured(
                        prompt,
                        system=INTERPRETER_SYSTEM_PROMPT,
                        schema=QueryInterpretation,
                        max_tokens=settings.m3_rag_query_interpreter_max_tokens,
                        temperature=0.0,
                        timeout_seconds=settings.m3_rag_query_interpreter_timeout_seconds,
                    )
                record["usage"] = dict(provider.usage)
            except Exception as exc:
                record["error"] = type(exc).__name__
                record["code"] = getattr(exc, "code", None)
                record["usage"] = dict(provider.usage) or None
                if not record["usage"]:
                    record.pop("usage")
            finally:
                await provider.aclose()
            record["latency_ms"] = round((time.perf_counter() - started) * 1000)
            report["streams"][stream].append(record)
            save()
            cached = (
                (record.get("usage") or {}).get("prompt_tokens_details") or {}
            ).get("cached_tokens")
            print(f"+{offset:>4}s {stream:20} cached={cached}", flush=True)

        t0 = time.monotonic()
        for step, offset in enumerate(OFFSETS_SECONDS):
            await asyncio.sleep(max(0.0, t0 + offset - time.monotonic()))
            await asyncio.gather(
                *[call(stream, step, offset) for stream in report["streams"]]
            )
        report["completed_at"] = datetime.now(UTC).isoformat()
        save()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
