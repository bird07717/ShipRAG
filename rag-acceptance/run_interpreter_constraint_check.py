"""REAL-LLM check: does the LLM 1 rewrite keep the user's explicit exclusions under both layouts?

Why: in the layout review (run_layout_review.py) the PDC case asks "...不要给软件升级流程。" and the
legacy layout kept that exclusion in ``resolved_question`` 3/3 while the static-first layout dropped it
3/3. LLM 2 never sees the raw question, only ``resolved_question``, so a dropped exclusion changes the
answer. That review did not gate on it, so this tool measures it on its own.

Questions: 8 short questions about the knowledge base's product, each with one explicit exclusion,
written BEFORE the first paid call (below). Each is sent ``--repeats`` times (default 3, temperature
0, so repeats are nearly identical: the effective sample size is the 8 questions) to LEGACY (previous
key order) and STATIC_FIRST (shipped order).

Protocol fixed before the first paid call:
  * A sample "keeps" the exclusion when ``resolved_question`` matches the question's ``keep`` regex
    (the excluded topic within 14 characters after a negation word).
  * Gate: STATIC_FIRST keeps the exclusion in at least as many samples as LEGACY minus 2 (of 24).
  * All resolved questions are printed and read; the regex is only a screen.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from frozen_inputs import legacy_layout, load_cases, load_llm, static_first_layout
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.rag.document_card_repository import DocumentCardRepository
from app.rag.llm import create_llm_provider
from app.rag.repository import RagRepository
from app.rag.v2.query_interpreter import (
    INTERPRETER_SYSTEM_PROMPT,
    QueryInterpretation,
    build_interpreter_prompt,
)

NEG = r"(?:不要|不需要|无需|不用|别|先别|不必|排除|不含|不包括|不涉及)"
QUESTIONS = (
    ("pdc_inspect", "PDC 接线盒怎么检查？不要给软件升级流程。", NEG + r".{0,14}升级"),
    (
        "ffc_ip",
        "FFC 升级时电脑的 IP 要设成多少？只回答地址，不要升级步骤。",
        NEG + r".{0,14}(?:升级|步骤)",
    ),
    (
        "psu_judge",
        "PSU 电源板报警了，先别让我更换，只说怎么初步判断。",
        NEG + r".{0,14}更换",
    ),
    (
        "lru_software",
        "更换 LRU 单元之后要做哪些软件操作？不要重复硬件拆装步骤。",
        NEG + r".{0,14}(?:拆装|硬件|重复)",
    ),
    (
        "mu_led",
        "MU 面板上的 LED 灯分别代表什么？不需要故障排查。",
        NEG + r".{0,14}(?:排查|故障)",
    ),
    (
        "pdc_oxid",
        "PDC 接线盒插头有氧化但通信正常，怎么判断是否可靠？不要给升级流程，也不要让我更换。",
        NEG + r".{0,14}(?:升级|更换)",
    ),
    (
        "ffc_port",
        "FFC 的交换机端口频繁掉线，怎么排查？别让我拆机。",
        NEG + r".{0,14}拆",
    ),
    (
        "pdc_prepare",
        "U 盘升级 PDC 前要准备什么？只讲准备，不要讲具体升级操作。",
        NEG + r".{0,14}(?:升级|操作)",
    ),
)
ARMS = ("LEGACY", "STATIC_FIRST")


def keeps(question_id: str, resolved: str) -> bool:
    pattern = next(p for q, _, p in QUESTIONS if q == question_id)
    return re.search(pattern, resolved) is not None


def summarize(path: Path) -> None:
    report = json.loads(path.read_text())
    totals = dict.fromkeys(ARMS, 0)
    counted = dict.fromkeys(ARMS, 0)
    for item in report["items"]:
        print(f"{item['id']}: {item['question']}")
        for arm in ARMS:
            for sample in item["samples"][arm]:
                resolved = sample.get("resolved_question", sample.get("error"))
                kept = "resolved_question" in sample and keeps(item["id"], resolved)
                counted[arm] += "resolved_question" in sample
                totals[arm] += kept
                print(f"  {arm:13} {'KEEP' if kept else 'DROP'}  {resolved}")
    print("\nkept the exclusion (regex screen):")
    for arm in ARMS:
        print(f"  {arm:13} {totals[arm]}/{counted[arm]}")
    passed = totals["STATIC_FIRST"] >= totals["LEGACY"] - 2
    print(f"gate (STATIC_FIRST >= LEGACY - 2): {passed}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summarize", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
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
    try:
        llm = await load_llm(settings, engine, repo)
        cases, _ = await load_cases(
            settings,
            engine,
            repo,
            DocumentCardRepository(engine, settings),
            only={"evidence_psu"},
        )
        profile = cases[0]
        items: list[dict[str, Any]] = []
        for question_id, question, _ in QUESTIONS:
            built = build_interpreter_prompt(
                question=question,
                history=[],
                knowledge_name=profile.product_name,
                scope_description=profile.scope_description,
                max_rounds=settings.m3_rag_history_max_rounds,
            )
            prompts = {
                "STATIC_FIRST": static_first_layout(built, "LLM1"),
                "LEGACY": legacy_layout(built, "LLM1"),
            }
            item: dict[str, Any] = {
                "id": question_id,
                "question": question,
                "samples": {arm: [] for arm in ARMS},
            }

            async def one(
                arm: str, item: dict[str, Any] = item, prompts: dict[str, str] = prompts
            ) -> None:
                provider = create_llm_provider(settings, llm.snapshot)
                try:
                    parsed = await provider.complete_structured(
                        prompts[arm],
                        system=INTERPRETER_SYSTEM_PROMPT,
                        schema=QueryInterpretation,
                        max_tokens=settings.m3_rag_query_interpreter_max_tokens,
                        temperature=0.0,
                        timeout_seconds=settings.m3_rag_query_interpreter_timeout_seconds,
                    )
                    item["samples"][arm].append(parsed.model_dump())
                except Exception as exc:
                    item["samples"][arm].append({"error": type(exc).__name__})
                finally:
                    await provider.aclose()

            await asyncio.gather(
                *[one(arm) for _ in range(args.repeats) for arm in ARMS]
            )
            items.append(item)
            print(f"{question_id}: done", flush=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {"model": llm.label, "items": items}, ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
