"""REAL-LLM regression check of the language/format change: did other kinds of questions change?

50 small cases (written with their expectations BEFORE the first paid call), each sent
``--repeats`` times (default 2, so 200 calls) to:
  * BASELINE = the previous LLM 1 prompt (new rules/examples removed), raw model output, as before.
  * NEW = what ships: ``interpret_query`` with the current prompt AND the server guard.

Categories: A single-turn product questions (10), B conversational (6), C genuinely out of scope (6,
two of them are short translation requests that the guard may wrongly turn into follow-ups),
D elliptical follow-ups (10), E new tasks after a history (6), F real questions that contain a
format word but have their own object (8), G English product questions (4).

A sample is OK when scope/path/relation are in the expected sets, every ``must`` regex matches
``resolved_question``, no ``forbid`` regex matches and (if given) the format ``marker`` is kept.

Gates fixed beforehand:
  R1 NEW total OK >= BASELINE total OK - 3 (of 100 samples).
  R2 no category with NEW OK < BASELINE OK - 2.
  R3 the guard fires on no sample of categories A, B, D, E, F, G (it may fire only where the model
     said OUT_OF_SCOPE and the message is format-only); firings in C are reported as a known cost.
  R4 every case whose (scope, path, relation) differs between the arms is printed and read.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from frozen_inputs import load_cases, load_llm
from run_format_request_review import HISTORIES, baseline_interpreter_prompt
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.rag.document_card_repository import DocumentCardRepository
from app.rag.llm import create_llm_provider
from app.rag.repository import RagRepository
from app.rag.v2.query_interpreter import (
    INTERPRETER_SYSTEM_PROMPT,
    QueryInterpretation,
    build_interpreter_prompt,
    interpret_query,
)

ARMS = ("BASELINE", "NEW")
IN = {"IN_SCOPE", "UNCERTAIN"}
OOS = {"OUT_OF_SCOPE"}
FOLLOW = {"FOLLOW_UP", "CORRECTION"}
NEW_TASK = {"NEW_TASK"}
KNOW = {"KNOWLEDGE"}
CONV = {"CONVERSATION"}
ANY_PATH = {"KNOWLEDGE", "CONVERSATION"}
ENGLISH = r"(?i)english|英文|英语"


def case(
    cid, cat, history, question, scope, path, relation, must=(), forbid=(), marker=None
):
    return {
        "id": cid, "cat": cat, "history": history, "question": question,
        "scope": sorted(scope), "path": sorted(path), "relation": sorted(relation),
        "must": list(must), "forbid": list(forbid), "marker": marker,
    }  # fmt: skip


CASES = [
    # A single-turn product questions
    case(
        "A1", "A", None, "VDR600 的 FFC 离线报警怎么检查？", IN, KNOW, NEW_TASK, ["FFC"]
    ),
    case("A2", "A", None, "如何更换 PSU 电源板？", IN, KNOW, NEW_TASK, ["PSU"]),
    case(
        "A3",
        "A",
        None,
        "PDC 接线盒怎么检查？不要给软件升级流程。",
        IN,
        KNOW,
        NEW_TASK,
        ["PDC", r"不要.{0,6}升级"],
    ),
    case(
        "A4",
        "A",
        None,
        "FFC 升级时电脑的 IP 要设成多少？",
        IN,
        KNOW,
        NEW_TASK,
        ["FFC", "IP"],
    ),
    case(
        "A5", "A", None, "UTC 时间和系统时间有什么区别？", IN, KNOW, NEW_TASK, ["UTC"]
    ),
    case("A6", "A", None, "VDR600 是谁生产的？", IN, ANY_PATH, NEW_TASK),
    case("A7", "A", None, "MU 主机纽扣电池是什么型号？", IN, KNOW, NEW_TASK, ["电池"]),
    case("A8", "A", None, "如何检查 DAU 通道的电压？", IN, KNOW, NEW_TASK, ["DAU"]),
    case("A9", "A", None, "FCC 怎么升级？", IN, KNOW, NEW_TASK, ["FFC"]),
    case(
        "A10",
        "A",
        None,
        "更换单个存储体之后需要做什么？",
        IN,
        KNOW,
        NEW_TASK,
        ["存储体"],
    ),
    # B conversational
    case("B1", "B", None, "你好", IN, CONV, NEW_TASK),
    case("B2", "B", "psu", "谢谢", IN, CONV, NEW_TASK | FOLLOW),
    case("B3", "B", None, "你能帮我做什么", IN, CONV, NEW_TASK),
    case(
        "B4",
        "B",
        "ffc2",
        "好的，我同意联系海兰信进行专业检查",
        IN,
        CONV,
        NEW_TASK | FOLLOW,
    ),
    case("B5", "B", "ffc2", "我需要联系海兰信专业人员吗", IN, CONV, NEW_TASK | FOLLOW),
    case("B6", "B", "psu", "上面的内容我看不懂，请简单解释", IN, CONV, FOLLOW),
    # C genuinely out of scope (C4/C5 are short translation requests: guard risk)
    case("C1", "C", None, "今天天气怎么样", OOS, ANY_PATH, NEW_TASK),
    case("C2", "C", "ffc2", "帮我写一首关于大海的诗", OOS, ANY_PATH, NEW_TASK),
    case("C3", "C", "psu", "用 Python 写一个快速排序", OOS, ANY_PATH, NEW_TASK),
    case("C4", "C", "ffc2", "把 'good morning' 翻译成法语", OOS, ANY_PATH, NEW_TASK),
    case(
        "C5",
        "C",
        "psu",
        "Translate the sentence 'see you tomorrow' into English",
        OOS,
        ANY_PATH,
        NEW_TASK,
    ),
    case("C6", "C", None, "推荐一部好看的电影", OOS, ANY_PATH, NEW_TASK),
    # D elliptical follow-ups
    case("D1", "D", "psu", "断电后等多久", IN, KNOW, FOLLOW, ["PSU"]),
    case("D2", "D", "psu", "下一步呢", IN, KNOW, FOLLOW, ["PSU"]),
    case("D3", "D", "ffc2", "为什么", IN, KNOW, FOLLOW, ["FFC"]),
    case("D4", "D", "ffc2", "用哪个端口", IN, KNOW, FOLLOW, ["FFC"]),
    case("D5", "D", "pdc", "接线松动了怎么办", IN, KNOW, FOLLOW, ["PDC"]),
    case("D6", "D", "ffc2", "那 PDC 呢", IN, KNOW, FOLLOW, ["PDC"]),
    case("D7", "D", "psu", "需要什么工具", IN, KNOW, FOLLOW, ["PSU"]),
    case("D8", "D", "ffc2", "升级完成后怎么验证", IN, KNOW, FOLLOW, ["FFC"]),
    case("D9", "D", "pdc", "氧化了怎么处理", IN, KNOW, FOLLOW, ["PDC"]),
    case("D10", "D", "psu", "第 3 步具体怎么拆", IN, KNOW, FOLLOW, ["PSU"]),
    # E new tasks after a history
    case("E1", "E", "psu", "如何更换故障 PDC？", IN, KNOW, NEW_TASK, ["PDC"], ["PSU"]),
    case(
        "E2",
        "E",
        "ffc2",
        "MU 主机纽扣电池怎么更换？",
        IN,
        KNOW,
        NEW_TASK,
        ["电池"],
        ["FFC"],
    ),
    case(
        "E3",
        "E",
        "pdc",
        "FFC 的交换机端口频繁掉线怎么排查？",
        IN,
        KNOW,
        NEW_TASK,
        ["FFC"],
        ["PDC"],
    ),
    case(
        "E4",
        "E",
        "ffc2",
        "How do I replace the PSU board?",
        IN,
        KNOW,
        NEW_TASK,
        ["PSU"],
        ["FFC"],
    ),
    case(
        "E5",
        "E",
        "psu",
        "UTC 时间和系统时间有什么区别？",
        IN,
        KNOW,
        NEW_TASK,
        ["UTC"],
        ["PSU"],
    ),
    case(
        "E6",
        "E",
        "pdc",
        "如何检查 DAU 通道的电压？",
        IN,
        KNOW,
        NEW_TASK,
        ["DAU"],
        ["PDC"],
    ),
    # F real questions with a format word and their own object
    case(
        "F1",
        "F",
        "psu",
        "FFC 怎么升级？请用英文回答",
        IN,
        KNOW,
        NEW_TASK,
        ["FFC"],
        ["PSU"],
        ENGLISH,
    ),
    case(
        "F2",
        "F",
        "ffc2",
        "用表格列出更换 PSU 需要的工具",
        IN,
        KNOW,
        NEW_TASK,
        ["PSU"],
        ["FFC"],
        "表格",
    ),
    case(
        "F3",
        "F",
        None,
        "详细说明 FFC 升级步骤",
        IN,
        KNOW,
        NEW_TASK,
        ["FFC"],
        (),
        "详细",
    ),
    case(
        "F4",
        "F",
        "pdc",
        "简要说明 VDR600 是什么",
        IN,
        ANY_PATH,
        NEW_TASK,
        ["VDR600"],
        ["PDC"],
        r"简要|简短|简洁|概括",
    ),
    case(
        "F5",
        "F",
        None,
        "Explain in English how to replace the PDC",
        IN,
        KNOW,
        NEW_TASK,
        ["PDC"],
        (),
        ENGLISH,
    ),
    case(
        "F6",
        "F",
        "ffc2",
        "把 PSU 更换步骤分点列出来",
        IN,
        KNOW,
        NEW_TASK,
        ["PSU"],
        ["FFC"],
        r"分点|分条|列",
    ),
    case(
        "F7",
        "F",
        "psu",
        "再给我一个 FFC 升级的简短说明",
        IN,
        KNOW,
        NEW_TASK,
        ["FFC"],
        ["PSU"],
        r"简短|简要|简洁",
    ),
    case(
        "F8",
        "F",
        None,
        "Please answer in English: how to check DAU voltage?",
        IN,
        KNOW,
        NEW_TASK,
        ["DAU"],
        (),
        ENGLISH,
    ),
    # G English product questions
    case(
        "G1",
        "G",
        None,
        "How do I check the FFC offline alarm?",
        IN,
        KNOW,
        NEW_TASK,
        ["FFC"],
    ),
    case(
        "G2",
        "G",
        None,
        "What IP should the laptop use when upgrading the PDC?",
        IN,
        KNOW,
        NEW_TASK,
        ["PDC"],
    ),
    case(
        "G3",
        "G",
        None,
        "Why does the PSU board need to be replaced?",
        IN,
        KNOW,
        NEW_TASK,
        ["PSU"],
    ),
    case("G4", "G", None, "Who manufactures the VDR600?", IN, ANY_PATH, NEW_TASK),
]
assert len(CASES) == 50 and len({c["id"] for c in CASES}) == 50


def score(item: dict[str, Any], sample: dict[str, Any]) -> bool:
    if "error" in sample:
        return False
    resolved = sample["resolved_question"]
    return (
        sample["scope"] in item["scope"]
        and sample["response_path"] in item["path"]
        and sample["relation"] in item["relation"]
        and all(re.search(p, resolved, re.IGNORECASE) for p in item["must"])
        and not any(re.search(p, resolved, re.IGNORECASE) for p in item["forbid"])
        and (item["marker"] is None or re.search(item["marker"], resolved) is not None)
    )


def triple(sample: dict[str, Any]) -> tuple[str, str, str]:
    return (
        sample.get("scope", "?"),
        sample.get("response_path", "?"),
        sample.get("relation", "?"),
    )


def summarize(path: Path) -> None:
    report = json.loads(path.read_text())
    cats: dict[str, dict[str, list[int]]] = {}
    differing = []
    guard_fired = []
    for item in report["items"]:
        for arm in ARMS:
            for sample in item["samples"][arm]:
                bucket = cats.setdefault(item["cat"], {a: [0, 0] for a in ARMS})[arm]
                bucket[0] += score(item, sample)
                bucket[1] += 1
                if sample.get("scope_guard"):
                    guard_fired.append(
                        (item["id"], item["cat"], item["question"], sample)
                    )
        base = {triple(s) for s in item["samples"]["BASELINE"]}
        new = {triple(s) for s in item["samples"]["NEW"]}
        if base != new:
            differing.append(item)
    print("== per-category OK (BASELINE / NEW) ==")
    tb = tn = 0
    r2 = True
    for cat in sorted(cats):
        b, n = cats[cat]["BASELINE"], cats[cat]["NEW"]
        tb, tn = tb + b[0], tn + n[0]
        r2 &= n[0] >= b[0] - 2
        print(f"  {cat}: {b[0]}/{b[1]}  {n[0]}/{n[1]}")
    print(f"  total: {tb}/100  {tn}/100")
    print(f"R1 NEW total >= BASELINE - 3: {tn >= tb - 3}")
    print(f"R2 no category worse by more than 2: {r2}")
    bad_guard = [g for g in guard_fired if g[1] != "C"]
    print(
        f"R3 guard firings outside C: {len(bad_guard)} (inside C: {len(guard_fired) - len(bad_guard)})"
    )
    for cid, cat, question, sample in guard_fired:
        print(f"   guard [{cat}] {cid} {question} -> {sample['resolved_question']}")
    print("\n== NOT OK samples ==")
    for item in report["items"]:
        for arm in ARMS:
            for sample in item["samples"][arm]:
                if not score(item, sample):
                    desc = sample.get("error") or (
                        f"{sample['relation']}/{sample['scope']}/{sample['response_path']} "
                        f"{sample['resolved_question']}"
                    )
                    print(f"  [{item['id']}] {arm:8} {item['question']} => {desc}")
    print("\n== cases where (scope, path, relation) differ between the arms ==")
    for item in differing:
        print(f"[{item['id']}] {item['question']}")
        for arm in ARMS:
            for sample in item["samples"][arm]:
                print(
                    f"   {arm:8} {'/'.join(triple(sample))}  {sample.get('resolved_question', sample.get('error'))}"
                )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summarize", type=Path)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--only", help="comma separated case ids (default: all 50)")
    parser.add_argument("--concurrency", type=int, default=8)
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
    semaphore = asyncio.Semaphore(args.concurrency)
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
        only = {c.strip() for c in args.only.split(",")} if args.only else None
        items = [
            {**c, "samples": {arm: [] for arm in ARMS}}
            for c in CASES
            if only is None or c["id"] in only
        ]

        async def run(item: dict[str, Any], arm: str) -> None:
            history = HISTORIES[item["history"]][1] if item["history"] else []
            async with semaphore:
                provider = create_llm_provider(settings, llm.snapshot)
                try:
                    if arm == "NEW":
                        outcome = await interpret_query(
                            provider,
                            question=item["question"],
                            history=history,
                            knowledge_name=profile.product_name,
                            scope_description=profile.scope_description,
                            max_rounds=settings.m3_rag_history_max_rounds,
                            max_tokens=settings.m3_rag_query_interpreter_max_tokens,
                            timeout_seconds=settings.m3_rag_query_interpreter_timeout_seconds,
                        )
                        sample = outcome.interpretation.model_dump()
                        sample["scope_guard"] = outcome.scope_guard
                        sample["status"] = outcome.status
                    else:
                        prompt = baseline_interpreter_prompt(
                            build_interpreter_prompt(
                                question=item["question"],
                                history=history,
                                knowledge_name=profile.product_name,
                                scope_description=profile.scope_description,
                                max_rounds=settings.m3_rag_history_max_rounds,
                            )
                        )
                        parsed = await provider.complete_structured(
                            prompt,
                            system=INTERPRETER_SYSTEM_PROMPT,
                            schema=QueryInterpretation,
                            max_tokens=settings.m3_rag_query_interpreter_max_tokens,
                            temperature=0.0,
                            timeout_seconds=settings.m3_rag_query_interpreter_timeout_seconds,
                        )
                        sample = parsed.model_dump()
                    item["samples"][arm].append(sample)
                except Exception as exc:
                    item["samples"][arm].append({"error": type(exc).__name__})
                finally:
                    await provider.aclose()

        await asyncio.gather(
            *[
                run(item, arm)
                for item in items
                for _ in range(args.repeats)
                for arm in ARMS
            ]
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {"model": llm.label, "items": items}, ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
        summarize(args.output)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
