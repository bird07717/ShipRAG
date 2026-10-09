"""REAL LLM2 experiment on the undefined ``next_suggestions`` output field.

Why: in 169 historical LLM2 answers the field was non-empty for MU 20/20 and PDC 20/20
(the two scenarios that most often leave the user's stated limits) and 0 for FFC/LRU, while
``answer_generator.build_answer_prompt`` never says what the field should contain.

Single factor, three arms on identical frozen inputs (the evidence-input "A" contexts):
  BASELINE  the unchanged current prompt.
  DEFINED   one extra ``output_contract`` item defining ``next_suggestions``.
  EMPTY     one extra ``output_contract`` item requiring an empty list (diagnostic arm:
            does the main answer change when the field cannot carry adjacent flows?).

Protocol fixed BEFORE the first paid call (do not edit after seeing results):
  * Cases: MU and PDC are the targets; FFC and PSU (full operation) are regression controls.
    Every arm of every case is sampled ``--repeats`` times (default 8), all results kept.
  * Primary metric: number of responses whose ``next_suggestions`` contain at least one item
    that proposes an action beyond the user's request (read by a human, item by item).
    MU asked to observe and record without opening, replacing or upgrading; PDC asked to
    assess reliability and whether professional inspection is needed, not repair steps.
  * Secondary: the same judgement on the main ``answer`` (sentence level), and the share of
    responses with an empty suggestion list.
  * Controls must keep their core content: FFC states the computer address 172.16.8.175 and
    that port drops alone cannot confirm hardware damage; PSU keeps power-off, taking out and
    refitting the 3359 board, photographing/marking cables, replacing the PSU and the
    five-minute alarm check, with comparable image count.
  * DEFINED is only worth shipping if MU+PDC responses with an out-of-scope suggestion drop
    by at least half versus BASELINE, the controls do not regress, and the main answers show
    no new high-consequence behaviour (more than two extra out-of-scope main answers in
    MU+PDC combined counts as a regression).
``--summarize`` prints mechanical screening counts only; they are NOT the primary metric
because negations such as "无需更换" match the patterns. No model judge, no Golden.

Second experiment: stop asking LLM 2 to write ``next_suggestions``
------------------------------------------------------------------
The server already discards the field (ADR-0040), so asking for it only costs output tokens.
Arms (``--arms BASELINE,NO_FIELD``): NO_FIELD deletes the property from ``output_schema`` and the
key from ``output_example`` and changes nothing else; the schema class still accepts the field if
the model writes it anyway. Same four cases, ``--repeats`` 8, all results kept.

Protocol fixed BEFORE the first paid call of this experiment (do not edit after seeing results):
  * Gate 1, validity: structured-output errors or retries (usage.structured_attempts > 1) in
    NO_FIELD exceed BASELINE's by at most 1 over the 32 samples.
  * Gate 2, controls: FFC states 172.16.8.175 and that a port drop alone cannot confirm hardware
    damage in 8/8 samples; PSU keeps power-off, 3359 board, photographing/marking, replacing the
    PSU and the five-minute alarm check in 8/8, and its image-marker count stays within
    BASELINE's min..max.
  * Gate 3, target cases: main answers in MU+PDC that direct the user beyond the request (MU:
    opening the chassis or inspecting inside it; PDC: replacing, temporary docking or repair),
    judged by reading the matched sentences, exceed BASELINE's count by at most 2 of 16.
  * Reported, not gated: whether the model still writes the field, completion tokens, latency.
  Ship only if all three gates hold. Any unexpected failure mode (for example a different answer
  shape) is reported and blocks shipping until it is understood.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
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

CASES = ("evidence_mu", "evidence_pdc", "evidence_ffc", "evidence_psu")
DEFAULT_ARMS = ("BASELINE", "DEFINED", "EMPTY")
ALL_ARMS = (*DEFAULT_ARMS, "NO_FIELD")

DEFINED_RULE = (
    "next_suggestions 是用户可以直接点击追问的问题，最多 3 条，只能写成疑问句；"
    "必须围绕当前问题和已采用的文档继续澄清或延伸，不得是操作指令，"
    "不得引入当前问题没有涉及的其他操作或处理流程，也不得与用户明示的限制冲突；"
    "没有合适的问题时返回空列表。"
)
EMPTY_RULE = "next_suggestions 必须返回空列表。"
ARM_RULES = {"DEFINED": DEFINED_RULE, "EMPTY": EMPTY_RULE}
NO_FIELD_NOTE = "NO_FIELD removes next_suggestions from output_schema.properties and output_example only"

# Mechanical screening only; see the module docstring.
SCREEN = {
    "OPEN": re.compile(
        r"开盖|开箱|打开.{0,8}(机箱|盖板|箱门|箱体|外壳|盖)|拧开|螺丝|机箱内|内部"
    ),
    "MEASURE": re.compile(r"万用表|测量|波形|示波"),
    "CHANGE": re.compile(
        r"更换|替换|升级|备件|新接线盒|重新插拔|短接|临时对接|应急|吹干|断开"
    ),
}


def arm_prompt(base_prompt: str, arm: str) -> str:
    if arm == "BASELINE":
        return base_prompt
    payload = json.loads(base_prompt)
    if arm == "NO_FIELD":
        payload["output_schema"]["properties"].pop("next_suggestions")
        payload["output_example"].pop("next_suggestions")
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    payload["output_contract"] = [*payload["output_contract"], ARM_RULES[arm]]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def summarize(path: Path) -> None:
    report = json.loads(path.read_text())
    print(
        f"{'case':14} {'arm':9} {'n':>3} {'sugg>0':>7} "
        + " ".join(f"{k}-S/{k}-A" for k in SCREEN)
    )
    for row in report["results"]:
        for arm in ALL_ARMS:
            responses = [
                run["variants"][arm]["response"]
                for run in row["runs"]
                if "response" in run["variants"].get(arm, {})
            ]
            if not responses:
                continue
            counts = []
            for pattern in SCREEN.values():
                in_suggestions = sum(
                    any(pattern.search(item) for item in r["next_suggestions"])
                    for r in responses
                )
                in_answer = sum(bool(pattern.search(r["answer"])) for r in responses)
                counts.append(f"{in_suggestions:>3}/{in_answer:<3}")
            non_empty = sum(bool(r["next_suggestions"]) for r in responses)
            print(
                f"{row['id']:14} {arm:9} {len(responses):>3} {non_empty:>7} "
                + "   ".join(counts)
            )
        errors = sum(
            "error" in variant
            for run in row["runs"]
            for variant in run["variants"].values()
        )
        if errors:
            print(f"  {row['id']}: {errors} errored samples (kept in the report)")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("rag-acceptance/reports/evidence-input-20261008/answers.json"),
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summarize", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument(
        "--arms",
        default=",".join(DEFAULT_ARMS),
        help="comma separated subset of "
        + ",".join(ALL_ARMS)
        + "; BASELINE must come first",
    )
    parser.add_argument("--repeats", type=int, default=8)
    parser.add_argument("--concurrency", type=int, default=2)
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
    if args.repeats < 1 or args.concurrency < 1:
        parser.error("repeats and concurrency must be positive")
    arms = tuple(item.strip() for item in args.arms.split(",") if item.strip())
    if not arms or arms[0] != "BASELINE" or not set(arms) <= set(ALL_ARMS):
        parser.error("--arms must start with BASELINE and use known arm names")

    baseline_text = args.baseline.read_text()
    baseline = json.loads(baseline_text)
    if baseline.get("prepare_only") or baseline.get("repeats") != 3:
        raise ValueError("Expected the completed three-repeat evidence-input baseline")
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
            "Current generation settings differ from the non-thinking baseline"
        )

    engine = create_async_engine(settings.database_url.get_secret_value())
    repo = RagRepository(engine, settings)
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "method": f"single factor on next_suggestions; arms {','.join(arms)}; no quality grading",
        "baseline": str(args.baseline),
        "baseline_sha256": sha(baseline_text),
        "answer_generator_sha256": sha(Path(answer_generator.__file__).read_text()),
        "prepare_only": args.prepare_only,
        "repeats": args.repeats,
        "generation_settings": generation,
        "arm_rules": {**ARM_RULES, "NO_FIELD": NO_FIELD_NOTE},
        "arms": list(arms),
        "knowledge_id": baseline["knowledge_id"],
        "index_id": baseline["index_id"],
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
                            "SELECT name,description,COALESCE(rag_active_index_id,active_index_id) "
                            "AS index_id FROM knowledge_base WHERE id=:id"
                        ),
                        {"id": kb_id},
                    )
                )
                .mappings()
                .one()
            )
        model_label = {"provider": model["provider"], "name": model["model_name"]}
        if model_label != baseline["model"] or profile["index_id"] != index_id:
            raise ValueError("Model or active index changed since the baseline")
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
        finally:
            await check.aclose()
        report["model"] = model_label

        blocks: dict[str, Any] = {}
        sources: dict[str, Any] = {}
        frozen: list[tuple[dict[str, Any], dict[str, str]]] = []
        for original in baseline["results"]:
            if original["id"] not in CASES:
                continue
            old = original["contexts"]["A"]
            dids = old["document_ids"]
            for did in dids:
                if did not in blocks:
                    sources[did] = await repo.get_document_source(UUID(did))
                    if sources[did]["status"] != "STORED":
                        raise ValueError("Source document is no longer stored")
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
                    "Current full context/original prompt differs from the baseline"
                )
            prompts = {arm: arm_prompt(prompt, arm) for arm in arms}
            for arm in arms:
                if arm in ARM_RULES:
                    stripped = json.loads(prompts[arm])
                    stripped["output_contract"].pop()
                    if (
                        json.dumps(stripped, ensure_ascii=False, separators=(",", ":"))
                        != prompt
                    ):
                        raise ValueError(
                            f"{arm} arm changed more than the single contract item"
                        )
                elif arm == "NO_FIELD":
                    expected = json.loads(prompt)
                    expected["output_schema"]["properties"].pop("next_suggestions")
                    expected["output_example"].pop("next_suggestions")
                    if expected != json.loads(prompts[arm]):
                        raise ValueError("NO_FIELD arm changed more than the field")
            row = {
                "id": original["id"],
                "question": original["question"],
                "history": original["history"],
                "context": old,
                "prompt_sha256": {arm: sha(prompts[arm]) for arm in arms},
                "runs": [],
            }
            report["results"].append(row)
            frozen.append((row, prompts))
        save()
        if args.prepare_only:
            print(
                f"Prepared {len(frozen)} fixed inputs x {len(arms)} arms; no paid calls"
            )
            return

        semaphore = asyncio.Semaphore(args.concurrency)

        async def capture(
            row: dict[str, Any], prompts: dict[str, str], repeat: int, number: int
        ) -> None:
            shift = (repeat + number) % len(arms)
            order = [*arms[shift:], *arms[:shift]]
            run: dict[str, Any] = {"repeat": repeat, "order": order, "variants": {}}
            row["runs"].append(run)
            async with semaphore:
                for arm in order:
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
                                    "response_format",
                                )
                            }
                        )

                    provider.client.event_hooks["request"] = [record_request]
                    started = time.perf_counter()
                    try:
                        generated = await answer_generator.generate_answer(
                            provider,
                            prompt=prompts[arm],
                            max_tokens=generation["max_tokens"],
                            timeout_seconds=generation["timeout_seconds"],
                            thinking_enabled=False,
                            thinking_budget=generation["thinking_budget"],
                        )
                        result: dict[str, Any] = {
                            "response": generated.model_dump(),
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
                    run["variants"][arm] = result
                    save()
                    status = result.get("code") if "error" in result else "captured"
                    print(f"{row['id']} #{repeat} {arm}: {status}", flush=True)

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
