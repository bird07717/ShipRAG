"""REAL LLM2 experiment on how many images an answer carries and how stable that number is.

Why: on identical input, same prompt and temperature 0.1, a full PSU replacement answer carried
5 to 11 ``[IMG:]`` markers (8 runs). The image decision is the model's: it reads captions inline in
the document text and applies rule 12, which is a judgement ("all images of the written steps" for
a full operation, "only those that directly illustrate the answer" otherwise). The model also sees
a flat ``available_image_keys`` list that does not say which document an image belongs to.

Single-factor arms on identical frozen inputs (answer stage only, no retrieval, no LLM 1):
  BASELINE  the unchanged current prompt.
  RULE      rule 12 replaced by an explicit two-branch rule (full operation: every image of the main
            document's steps, none missed, supplementary documents only when their steps are used;
            parameter/position/status/yes-no/assessment answers: only images that directly answer,
            otherwise none).
  GROUPED   data only: ``available_image_keys`` replaced by ``available_images_by_document``
            (document key -> its image keys); rule text unchanged.
  BOTH      RULE text and GROUPED data together.

Cases: operation = PSU replacement (from the evidence-input baseline) and PDC / FFC replacement
(built from documents with step images; the first document is the main one); non-operation =
FFC address/diagnosis and PDC oxidation assessment (images are not needed).
Every arm of every case is sampled ``--repeats`` times, all results kept.

Measured on what the user would receive: the answer is run through the production
``validate_generated_answer`` and the delivered image blocks are counted and attributed to documents.

Protocol fixed BEFORE any result was read (do not edit after seeing results):
  Harness amendment (before any gate was judged): the first pass of this tool skipped
  production's ``normalize_evidence_bindings`` step and wrongly counted 10 of 60 answers as
  validator rejections (a cited image whose document was not listed). ``deliver()`` now applies it,
  and ``--redeliver`` recomputes delivery for stored responses without any model call.
  Sample size: the user cut the budget from 8 to 3 repeats per case and arm = 60 analysed calls
  (5 cases x 4 arms x 3). Eight repeats of the three baseline cases had already been paid for when
  that decision was made and NO result had been looked at; of those only repeats 1-3 are analysed
  (``--max-repeats 3``, fixed in advance, not chosen from outcomes) and the two operation cases that
  were not reached run with ``--cases op_pdc_replace,op_ffc_replace --repeats 3``. Three repeats give
  little power: sd values rest on n=3 per case, so a pass or fail of the stability gate is weak evidence.
  * Gate 1, validity: structured-output errors/retries (usage.structured_attempts > 1) or answers
    rejected by the production validator, for an arm, exceed BASELINE's by at most 1 over its 15
    samples.
  * Gate 2, controls: FFC (non-operation) states 172.16.8.175 and that a port drop alone cannot
    confirm hardware damage in 3/3; PSU keeps power-off, 3359 board, photographing/marking, replacing
    the PSU and the five-minute alarm check in 3/3.
  * Gate 3, no extra images where none are needed: the pooled mean number of delivered images on the
    two non-operation cases is at most BASELINE's mean + 1.0.
  * Gate 4, operation cases (PSU, PDC replacement, FFC replacement; 9 runs per arm):
      (a) completeness: the share of runs that deliver ALL images of the main document is at least
          BASELINE's;
      (b) stability: the mean over the three cases of the standard deviation of the delivered image
          count is at most 70% of BASELINE's;
      (c) no inflation: delivered images from documents other than the main one, per operation
          answer, at most BASELINE's + 1.
  An arm is adopted only if gates 1-4 hold; with several qualifying arms the lowest stability figure
  wins. If none qualifies nothing is adopted and the result is reported as it is.
Reported, not gated: per-image inclusion of the main document, completion tokens, latency.
No model judge. Image relevance to a step is NOT judged here; completeness is the rule's own
definition (every image of the main document).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics as st
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from frozen_inputs import FrozenCase, legacy_layout, load_cases, load_llm
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.rag.document_card_repository import DocumentCardRepository
from app.rag.llm import create_llm_provider
from app.rag.repository import RagRepository
from app.rag.v2 import answer_generator
from app.rag.v2.answer_generator import GeneratedAnswer
from app.rag.v2.query_interpreter import QueryInterpretation
from app.rag.v2.validation import (
    normalize_evidence_bindings,
    validate_generated_answer,
)

ARMS = ("BASELINE", "RULE", "GROUPED", "BOTH")
RULE_12 = (
    "12. 先判断本次回答是否在给出完成任务的连续操作步骤（更换、拆装、升级、配置等）。"
    "是：把主任务文档中与所写步骤对应的图片按步骤顺序全部放入，一张不漏；补充文档的图片只在"
    "采用其步骤时放入。否（参数、位置、状态、是否、评估、判断类回答）：只放能直接说明答案的图片，"
    "没有就不放。句末标点写在图片标记之前。"
)


def arm_prompt(base_prompt: str, arm: str, case: FrozenCase) -> str:
    # The experiment ran with the pre-ADR-0042 key order; keep it so BASELINE stays reproducible.
    payload = json.loads(legacy_layout(base_prompt, "LLM2"))
    if arm in ("RULE", "BOTH"):
        assert payload["decision_policy"][11].startswith("12. ")
        payload["decision_policy"][11] = RULE_12
    if arm in ("GROUPED", "BOTH"):
        grouped: dict[str, list[str]] = {key: [] for key in case.context.document_keys}
        for binding in case.context.image_bindings:
            grouped[binding.document_key].append(binding.key)
        payload.pop("available_image_keys")
        payload["available_images_by_document"] = grouped
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def core_ok(case_id: str, answer: str) -> bool | None:
    if case_id == "evidence_ffc":
        return "172.16.8.175" in answer and (
            ("不能" in answer and ("确" in answer or "判定" in answer))
            or "无法" in answer
        )
    if case_id == "evidence_psu":
        return (
            all(k in answer for k in ("断电", "3359", "更换"))
            and ("拍照" in answer or "标记" in answer)
            and ("5" in answer or "五" in answer)
        )
    return None


def deliver(case: FrozenCase, response: dict[str, Any]) -> dict[str, Any]:
    """What the user would receive: production validation, then image blocks per document."""

    # Production binds every cited image to its owning document before validating
    # (pipeline.py); without this step a cited image whose document the model forgot to list
    # was counted as a rejection here, which production never sees.
    generated = normalize_evidence_bindings(
        GeneratedAnswer.model_validate(response), case.context
    )
    try:
        result = validate_generated_answer(
            generated,
            context=case.context,
            include_images=True,
            allowed_text=case.question + "\n" + case.context.prompt_context,
        )
    except Exception as exc:  # the validator rejects invalid evidence or leaks
        return {"rejected": getattr(exc, "code", type(exc).__name__)}
    by_asset = {b.asset_id: b for b in case.context.image_bindings}
    keys = [
        by_asset[block.image_asset_id].key
        for block in result.content
        if block.type == "image" and block.image_asset_id in by_asset
    ]
    main = set(case.main_image_keys())
    return {
        "delivered": len(keys),
        "keys": keys,
        "main_keys": sorted(k for k in keys if k in main),
        "borrowed": sum(k not in main for k in keys),
    }


def load_report(paths: list[Path], max_repeats: int | None) -> dict[str, Any]:
    """Merge result files and keep only complete runs of repeats 1..max_repeats.

    A case that has no complete run in a file (the stopped first run only reached it with a few
    unfinished calls) is left out of that file.
    """

    reports = [json.loads(p.read_text()) for p in paths]
    merged = {**reports[0], "cases": []}
    seen: set[str] = set()
    for report in reports:
        if report["arms"] != merged["arms"]:
            raise ValueError("Result files use different arms")
        for case in report["cases"]:
            case["runs"] = [
                r
                for r in case["runs"]
                if (max_repeats is None or r["repeat"] <= max_repeats)
                and set(r["arms"]) == set(report["arms"])
            ]
            if not case["runs"]:
                continue
            if case["case_id"] in seen:
                raise ValueError(f"Case {case['case_id']} appears in two files")
            seen.add(case["case_id"])
            merged["cases"].append(case)
    return merged


async def redeliver(report: dict[str, Any]) -> None:
    """Recompute ``delivery`` of every stored response with the current ``deliver()``."""

    settings = Settings()
    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        cases, _ = await load_cases(
            settings,
            engine,
            RagRepository(engine, settings),
            DocumentCardRepository(engine, settings),
            only={c["case_id"] for c in report["cases"]},
        )
    finally:
        await engine.dispose()
    by_id = {c.case_id: c for c in cases}
    for case in report["cases"]:
        for run in case["runs"]:
            for result in run["arms"].values():
                if "response" in result:
                    result["delivery"] = deliver(
                        by_id[case["case_id"]], result["response"]
                    )


def summarize(report: dict[str, Any]) -> None:
    arms = report["arms"]
    print(
        "analysed runs per case and arm: "
        + ", ".join(f"{c['case_id']}={len(c['runs'])}" for c in report["cases"])
    )
    rows = {c["case_id"]: c for c in report["cases"]}

    def samples(case_id: str, arm: str) -> list[dict[str, Any]]:
        return [r["arms"][arm] for r in rows[case_id]["runs"] if arm in r["arms"]]

    print(
        "== GATE 1 validity: errors, retries (structured_attempts>1) or validator rejections per arm"
    )
    for arm in arms:
        bad = ok = 0
        for case_id in rows:
            for s in samples(case_id, arm):
                if "error" in s or s.get("delivery", {}).get("rejected"):
                    bad += 1
                elif s.get("usage", {}).get("structured_attempts", 1) > 1:
                    bad += 1
                else:
                    ok += 1
        print(f"  {arm:9} clean={ok} problems={bad}")

    print(
        "\n== GATE 2 controls (FFC address and no-diagnosis; PSU core steps), per arm"
    )
    for arm in arms:
        parts = []
        for case_id in ("evidence_ffc", "evidence_psu"):
            vals = [
                core_ok(case_id, s["response"]["answer"])
                for s in samples(case_id, arm)
                if "response" in s
            ]
            parts.append(f"{case_id}={sum(bool(v) for v in vals)}/{len(vals)}")
        print(f"  {arm:9} " + "  ".join(parts))

    print("\n== delivered image counts per case and arm (each number is one run)")
    for case_id, case in rows.items():
        main_total = len(case["main_image_keys"])
        print(f"  {case_id} [{case['kind']}] main-document images: {main_total}")
        for arm in arms:
            counts = [
                s["delivery"]["delivered"]
                for s in samples(case_id, arm)
                if "delivered" in s.get("delivery", {})
            ]
            sd = f"{st.pstdev(counts):.2f}" if counts else "-"
            print(
                f"    {arm:9} {counts}  mean {st.mean(counts):.1f} sd {sd}"
                if counts
                else f"    {arm}: none"
            )

    print("\n== GATE 3 non-operation cases: pooled mean delivered images")
    for arm in arms:
        counts = [
            s["delivery"]["delivered"]
            for case_id, c in rows.items()
            if c["kind"] == "NON_OPERATION"
            for s in samples(case_id, arm)
            if "delivered" in s.get("delivery", {})
        ]
        print(f"  {arm:9} {st.mean(counts):.2f} over {len(counts)} runs")

    print("\n== GATE 4 operation cases")
    stats: dict[str, dict[str, float]] = {}
    for arm in arms:
        complete = total = borrowed = 0
        sds = []
        for case_id, c in rows.items():
            if c["kind"] != "OPERATION":
                continue
            need = set(c["main_image_keys"])
            counts = []
            for s in samples(case_id, arm):
                d = s.get("delivery", {})
                if "delivered" not in d:
                    continue
                total += 1
                complete += need <= set(d["main_keys"])
                borrowed += d["borrowed"]
                counts.append(d["delivered"])
            if len(counts) > 1:
                sds.append(st.pstdev(counts))
        stats[arm] = {
            "complete": complete / total if total else 0.0,
            "sd": st.mean(sds) if sds else 0.0,
            "borrowed": borrowed / total if total else 0.0,
        }
        a = stats[arm]
        print(
            f"  {arm:9} complete {complete}/{total} = {a['complete']:.0%}   mean sd {a['sd']:.2f}   "
            f"borrowed per answer {a['borrowed']:.2f}"
        )
    base = stats.get("BASELINE")
    if base:
        print(
            "\n  vs BASELINE (gate 4: complete >= baseline, sd <= 70% of baseline, borrowed <= baseline+1):"
        )
        for arm in arms:
            if arm == "BASELINE":
                continue
            a = stats[arm]
            checks = (
                a["complete"] >= base["complete"],
                a["sd"] <= 0.7 * base["sd"],
                a["borrowed"] <= base["borrowed"] + 1,
            )
            print(
                f"    {arm:9} complete={checks[0]} stability={checks[1]} no-inflation={checks[2]}"
            )

    print("\n== reported: per-image inclusion of the main document (operation cases)")
    for case_id, c in rows.items():
        if c["kind"] != "OPERATION":
            continue
        print(f"  {case_id}")
        for arm in arms:
            runs = [
                s for s in samples(case_id, arm) if "delivered" in s.get("delivery", {})
            ]
            incl = {
                k: sum(k in s["delivery"]["main_keys"] for s in runs)
                for k in c["main_image_keys"]
            }
            print(
                f"    {arm:9} "
                + " ".join(f"{k}:{v}/{len(runs)}" for k, v in incl.items())
            )
    print("\n== reported: completion tokens (mean) per arm")
    for arm in arms:
        toks = [
            s["usage"]["completion_tokens"]
            for case_id in rows
            for s in samples(case_id, arm)
            if "usage" in s and "completion_tokens" in s["usage"]
        ]
        print(f"  {arm:9} {st.mean(toks):.0f}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summarize", type=Path, nargs="+")
    parser.add_argument("--max-repeats", type=int)
    parser.add_argument(
        "--redeliver",
        action="store_true",
        help="with --summarize: recompute delivery from stored responses (no model call)",
    )
    parser.add_argument("--cases", help="comma separated case ids to run (default all)")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()
    if args.summarize:
        report = load_report(args.summarize, args.max_repeats)
        if args.redeliver:
            await redeliver(report)
        summarize(report)
        return
    if args.output is None:
        parser.error("--output is required")
    if args.output.exists():
        parser.error(
            "Output already exists; preserve previous runs with a new filename"
        )
    arms = tuple(a.strip() for a in args.arms.split(",") if a.strip())
    if not arms or arms[0] != "BASELINE" or not set(arms) <= set(ARMS):
        parser.error("--arms must start with BASELINE and use known arm names")

    settings = Settings()
    engine = create_async_engine(settings.database_url.get_secret_value())
    repo = RagRepository(engine, settings)
    card_repo = DocumentCardRepository(engine, settings)
    try:
        llm = await load_llm(settings, engine, repo)
        only = (
            {c.strip() for c in args.cases.split(",") if c.strip()}
            if args.cases
            else None
        )
        cases, meta = await load_cases(settings, engine, repo, card_repo, only=only)
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
        report: dict[str, Any] = {
            "started_at": datetime.now(UTC).isoformat(),
            "method": f"image rule/structure single factor; arms {','.join(arms)}; no quality grading",
            "rule_12": RULE_12,
            "arms": list(arms),
            "repeats": args.repeats,
            "generation_settings": generation,
            "model": llm.label,
            **{k: v for k, v in meta.items() if k != "generation_settings"},
            "cases": [],
        }
        prompts: dict[str, dict[str, str]] = {}
        for case in cases:
            base_prompt = answer_generator.build_answer_prompt(
                history=case.history,
                interpretation=QueryInterpretation(
                    resolved_question=case.question,
                    scope="IN_SCOPE",
                    response_path="KNOWLEDGE",
                ),
                context=case.context,
                product_name=case.product_name,
                scope_description=case.scope_description,
                max_rounds=settings.m3_rag_history_max_rounds,
            )
            prompts[case.case_id] = {
                arm: arm_prompt(base_prompt, arm, case) for arm in arms
            }
            report["cases"].append(
                {
                    "case_id": case.case_id,
                    "kind": case.kind,
                    "question": case.question,
                    "documents": list(case.context.document_ids),
                    "main_document_id": case.main_document_id,
                    "main_image_keys": case.main_image_keys(),
                    "image_count": len(case.context.image_bindings),
                    "prompt_sha256": {
                        arm: __import__("hashlib").sha256(p.encode()).hexdigest()
                        for arm, p in prompts[case.case_id].items()
                    },
                    "runs": [],
                }
            )

        def save() -> None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        save()
        if args.prepare_only:
            for c in report["cases"]:
                print(
                    f"{c['case_id']:16} {c['kind']:14} images {c['image_count']:>2} "
                    f"main {len(c['main_image_keys'])} {c['main_image_keys']}"
                )
            print(f"Prepared {len(cases)} cases x {len(arms)} arms; no paid calls")
            return

        semaphore = asyncio.Semaphore(args.concurrency)
        by_id = {c.case_id: c for c in cases}

        async def capture(case_index: int, repeat: int) -> None:
            case = cases[case_index]
            shift = (repeat + case_index) % len(arms)
            order = [*arms[shift:], *arms[:shift]]
            run: dict[str, Any] = {"repeat": repeat, "order": order, "arms": {}}
            report["cases"][case_index]["runs"].append(run)
            async with semaphore:
                for arm in order:
                    provider = create_llm_provider(settings, llm.snapshot)
                    started = time.perf_counter()
                    try:
                        generated = await answer_generator.generate_answer(
                            provider,
                            prompt=prompts[case.case_id][arm],
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
                    except Exception as exc:
                        result = {
                            "error": type(exc).__name__,
                            "code": getattr(exc, "code", None),
                            "usage": dict(provider.usage),
                        }
                    finally:
                        await provider.aclose()
                    result["latency_ms"] = round((time.perf_counter() - started) * 1000)
                    run["arms"][arm] = result
                    save()
                    note = result.get("code") if "error" in result else "captured"
                    print(f"{case.case_id} #{repeat} {arm}: {note}", flush=True)

        await asyncio.gather(
            *[
                capture(i, r)
                for i in range(len(cases))
                for r in range(1, args.repeats + 1)
            ]
        )
        report["completed_at"] = datetime.now(UTC).isoformat()
        save()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
