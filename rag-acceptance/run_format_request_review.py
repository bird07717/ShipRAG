"""REAL-LLM review: language / format-only follow-ups ("Answer in English", "简短一点", ...).

Why: production 2026-10-13 — right after a completed FFC round, "answer in english" was judged
NEW_TASK + OUT_OF_SCOPE and short-circuited to the fixed refusal; a local probe showed the same
request losing the inherited task in 1/5 (FFC) and 3/5 (PSU) samples. The change adds two LLM 1
rules and three route examples (``FORMAT_REQUEST_RULES`` / ``FORMAT_ROUTE_EXAMPLES``), a server
guard for the OUT_OF_SCOPE short-circuit, and a conversation rule + retry against answers that
only announce their content ("Here is the translation:").

Arms: BASELINE = the current prompt with the new entries removed (byte-identical to the previous
prompt; checked by ``--check-baseline``), NEW = the shipped prompt. Temperature as in production.

Protocol fixed BEFORE the first paid call
-----------------------------------------
Part ``llm1`` (LLM 1 only, raw model output, i.e. BEFORE the server guard):
  * FORMAT: 9 requests x 3 long histories x ``--repeats`` (3). A sample is CORRECT when
    scope != OUT_OF_SCOPE, relation is FOLLOW_UP/CORRECTION, ``resolved_question`` contains the
    inherited task keyword of the history, and keeps the request's format marker.
    PATH: re-answer requests expect KNOWLEDGE, rewrite-only requests expect CONVERSATION.
  * C1 new standalone tasks after a history: NEW_TASK, not OUT_OF_SCOPE, KNOWLEDGE, no inherited
    keyword. C2 real out-of-scope requests after a history: OUT_OF_SCOPE. C3 elliptical follow-ups:
    FOLLOW_UP/CORRECTION with the inherited keyword. C4 "Answer in English" without history: not
    OUT_OF_SCOPE. C5 explicit exclusions (questions of run_interpreter_constraint_check): kept.
  * Gates: G1 NEW FORMAT correct >= 90% and NEW FORMAT OUT_OF_SCOPE == 0.
           G2 NEW FORMAT path matches >= 80%.
           G3 each control: NEW >= BASELINE - 1 (C5: - 2), and C4 NEW all not OUT_OF_SCOPE.
Part ``conversation`` (conversation answerer, raw output before the retry): 3 rewrite inputs x
  ``--conv-repeats`` (8). Gate G4: NEW unfinished count <= BASELINE unfinished count, and every NEW
  answer to an English request is mostly ASCII letters.
Part ``answer`` (LLM 2, shipped prompt only, frozen PSU context): resolved question with the English
  requirement x 3. Reported, not gated: language, delivered images, validation.
All resolved questions / answers are printed and read; the regexes are only a screen.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from frozen_inputs import load_cases, load_llm
from run_image_rule_review import deliver
from run_interpreter_constraint_check import QUESTIONS as EXCLUSION_QUESTIONS
from run_interpreter_constraint_check import keeps
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.rag.document_card_repository import DocumentCardRepository
from app.rag.llm import create_llm_provider
from app.rag.repository import RagRepository
from app.rag.v2 import answer_generator
from app.rag.v2.conversation_answer import (
    CONVERSATION_RULES,
    CONVERSATION_SYSTEM_PROMPT,
    ConversationAnswer,
    build_conversation_prompt,
    is_unfinished_answer,
)
from app.rag.v2.query_interpreter import (
    FORMAT_REQUEST_RULES,
    FORMAT_ROUTE_EXAMPLES,
    INTERPRETER_SYSTEM_PROMPT,
    QueryInterpretation,
    build_interpreter_prompt,
)

ARMS = ("BASELINE", "NEW")


def _round(user: str, assistant: str) -> list[dict[str, str]]:
    return [
        {"role": "USER", "content": user},
        {"role": "ASSISTANT", "content": assistant},
    ]


# Long, production-shaped answers (markdown steps) written for this review; no real credentials.
HISTORIES: dict[str, tuple[str, list[dict[str, str]]]] = {
    "ffc2": (
        "FFC",
        _round(
            "如何完整升级 VDR600 的 FFC/FFC600 存储体？",
            "升级 VDR600 的 FFC/FFC600 存储体需遵循以下步骤：\n\n1. **网络配置**：用网线连接电脑与"
            " VDR 主机箱，将电脑 IP 设为与 FFC 同一网段。\n2. **准备升级包**：将升级文件放入电脑指定"
            "目录，确认版本号与现场设备匹配。\n3. **登录设备**：通过维护软件连接 FFC，确认当前版本。"
            "\n4. **执行升级**：选择升级包并开始升级，过程中不要断电或拔线。\n5. **重启验证**：升级"
            "完成后重启 FFC，在 OPU 上确认无报警且版本已更新。\n\n如升级失败，请记录报错信息后重试，"
            "仍失败时联系厂家技术支持。",
        )
        + _round(
            "vdr ffc error",
            'VDR600 出现 OPU 提示 "system error ffc" 表示浮式存储单元（FFC）离线。请按以下顺序排查：'
            "\n\n1. **检查供电**：确认 FFC 电源指示灯常亮，供电电缆连接牢固。\n2. **检查网络**：检查"
            " FFC 网线和接头是否松动或损坏，确认交换机对应端口指示灯正常。\n3. **重启 FFC**：断电"
            "等待约 30 秒后重新上电，观察报警是否恢复。\n4. **更换验证**：如仍离线，可用备用网线或"
            "更换端口验证；问题依旧时考虑更换 FFC。",
        ),
    ),
    "psu": (
        "PSU",
        _round(
            "如何更换 PSU 电源板？",
            "更换 VDR600 PSU 电源板的步骤如下：\n\n1. **断电**：关闭主机电源并断开外部供电，等待电容"
            "放电。\n2. **打开机箱**：拆下机箱盖板螺丝，取下盖板。\n3. **拆线**：依次拔下 PSU 板上的"
            "电源输入线、输出线和信号线，并做好标记。\n4. **取出旧板**：拆下固定螺丝，取出旧 PSU 板。"
            "\n5. **安装新板**：装入新板，按标记恢复全部连接并紧固螺丝。\n6. **通电检查**：恢复供电，"
            "确认电源指示灯正常、OPU 无电源报警。",
        ),
    ),
    "pdc": (
        "PDC",
        _round(
            "PDC 接线盒怎么检查？",
            "检查 VDR600 PDC 接线盒可按以下顺序进行：\n\n1. **外观检查**：确认接线盒外壳无破损、"
            "进水或腐蚀。\n2. **接线检查**：逐一检查端子排接线是否松动，插头是否氧化。\n3. **电压"
            "测量**：用万用表测量供电端子电压，确认在正常范围内。\n4. **通信检查**：在 OPU 上查看"
            " PDC 通信状态，确认无掉线报警。\n\n发现氧化可用触点清洁剂处理，接线松动时重新紧固。",
        ),
    ),
}

# (id, request, format marker regex, expected path)
FORMAT_REQUESTS = (
    ("en_plain", "answer in english", r"(?i)english|英文|英语", "KNOWLEDGE"),
    ("en_period", "Answer in English.", r"(?i)english|英文|英语", "KNOWLEDGE"),
    ("en_please", "Please answer in English", r"(?i)english|英文|英语", "KNOWLEDGE"),
    ("zh_english", "用英文回答", r"(?i)english|英文|英语", "KNOWLEDGE"),
    ("zh_points", "分点说明", r"分点|分条|要点|列表", "KNOWLEDGE"),
    ("zh_detail", "再详细一点", r"详细|细节|具体", "KNOWLEDGE"),
    (
        "zh_translate",
        "把上面的回答翻译成英文",
        r"(?i)english|英文|英语|翻译",
        "CONVERSATION",
    ),
    (
        "en_translate",
        "Translate it into English",
        r"(?i)english|英文|英语|翻译|translat",
        "CONVERSATION",
    ),
    ("zh_short", "简短一点", r"简短|简洁|精简|简要|简化|概括|压缩", "CONVERSATION"),
)
# (id, history, question, previous-task keyword that must NOT appear)
NEW_TASKS = (
    ("pdc_after_psu", "psu", "如何更换故障 PDC？", "PSU"),
    ("battery_after_ffc", "ffc2", "MU 主机纽扣电池怎么更换？", "FFC"),
    ("en_psu_after_ffc", "ffc2", "How do I replace the PSU board?", "FFC"),
)
OUT_OF_SCOPE = (
    ("poem_zh", "ffc2", "帮我写一首关于大海的诗"),
    ("weather", "psu", "今天天气怎么样"),
    ("poem_en", "ffc2", "Write a poem about the sea"),
)
ELLIPTICAL = (
    ("psu_wait", "psu", "断电后等多久"),
    ("ffc_port", "ffc2", "用哪个端口"),
)
CONVERSATION_INPUTS = (
    (
        "translate_ffc",
        "ffc2",
        "把上面的回答翻译成英文",
        "将上一轮关于 FFC 掉线排查的回答翻译成英文",
        True,
    ),
    (
        "translate_psu",
        "psu",
        "Translate it into English",
        "将上一轮关于更换 PSU 电源板的回答翻译成英文",
        True,
    ),
    ("short_pdc", "pdc", "简短一点", "简短概括上一轮关于 PDC 接线盒检查的回答", False),
)


def baseline_interpreter_prompt(prompt: str) -> str:
    data = json.loads(prompt)
    data["rules"] = [r for r in data["rules"] if r not in FORMAT_REQUEST_RULES]
    data["route_examples"] = [
        e for e in data["route_examples"] if e not in FORMAT_ROUTE_EXAMPLES
    ]
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


NEW_CONVERSATION_RULE = next(r for r in CONVERSATION_RULES if "以冒号结尾的引导语" in r)


def baseline_conversation_prompt(prompt: str) -> str:
    data = json.loads(prompt)
    data["rules"] = [r for r in data["rules"] if r != NEW_CONVERSATION_RULE]
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def mostly_ascii(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(c.isascii() for c in letters) / len(letters) >= 0.9


# ---------------------------------------------------------------- scoring


def score_llm1(item: dict[str, Any], sample: dict[str, Any]) -> dict[str, bool]:
    if "error" in sample:
        return {"ok": False, "path_ok": False, "oos": False}
    resolved = sample["resolved_question"]
    kind = item["kind"]
    if kind == "FORMAT":
        keyword = HISTORIES[item["history"]][0]
        ok = (
            sample["scope"] != "OUT_OF_SCOPE"
            and sample["relation"] in {"FOLLOW_UP", "CORRECTION"}
            and keyword.casefold() in resolved.casefold()
            and re.search(item["marker"], resolved) is not None
        )
        return {
            "ok": ok,
            "path_ok": sample["response_path"] == item["expected_path"],
            "oos": sample["scope"] == "OUT_OF_SCOPE",
        }
    if kind == "C1":
        ok = (
            sample["relation"] == "NEW_TASK"
            and sample["scope"] != "OUT_OF_SCOPE"
            and sample["response_path"] == "KNOWLEDGE"
            and item["forbidden"].casefold() not in resolved.casefold()
        )
    elif kind == "C2":
        ok = sample["scope"] == "OUT_OF_SCOPE"
    elif kind == "C3":
        keyword = HISTORIES[item["history"]][0]
        ok = sample["relation"] in {"FOLLOW_UP", "CORRECTION"} and keyword in resolved
    elif kind == "C4":
        ok = sample["scope"] != "OUT_OF_SCOPE"
    else:  # C5
        ok = keeps(item["id"], resolved)
    return {"ok": ok, "path_ok": True, "oos": sample["scope"] == "OUT_OF_SCOPE"}


def summarize(path: Path) -> None:
    report = json.loads(path.read_text())
    if "llm1" in report:
        totals: dict[str, dict[str, list[int]]] = {}
        print("== LLM 1 samples ==")
        for item in report["llm1"]:
            print(f"[{item['kind']}] {item['id']} | {item['question']}")
            for arm in ARMS:
                for sample in item["samples"][arm]:
                    s = score_llm1(item, sample)
                    bucket = totals.setdefault(
                        item["kind"], {a: [0, 0, 0, 0] for a in ARMS}
                    )[arm]
                    bucket[0] += s["ok"]
                    bucket[1] += s["path_ok"]
                    bucket[2] += s["oos"]
                    bucket[3] += 1
                    desc = (
                        sample.get("error")
                        or f"{sample['relation']}/{sample['scope']}/{sample['response_path']} "
                        f"{sample['resolved_question']}"
                    )
                    flag = "OK " if s["ok"] else "BAD"
                    path_flag = "" if s["path_ok"] else " [path]"
                    print(f"   {arm:8} {flag}{path_flag} {desc}")
        print("\n== LLM 1 totals (ok / path_ok / OUT_OF_SCOPE / n) ==")
        for kind, arms in totals.items():
            print(
                f"  {kind:6} "
                + "  ".join(
                    f"{a}: {v[0]}/{v[1]}/{v[2]}/{v[3]}" for a, v in arms.items()
                )
            )
        fmt = totals.get("FORMAT")
        if fmt:
            new = fmt["NEW"]
            g1 = new[0] >= 0.9 * new[3] and new[2] == 0
            g2 = new[1] >= 0.8 * new[3]
            print(f"G1 FORMAT correct >= 90% and no OUT_OF_SCOPE: {g1}")
            print(f"G2 FORMAT path >= 80%: {g2}")
        g3 = []
        for kind in ("C1", "C2", "C3", "C5"):
            if kind in totals:
                margin = 2 if kind == "C5" else 1
                g3.append(
                    totals[kind]["NEW"][0] >= totals[kind]["BASELINE"][0] - margin
                )
        if "C4" in totals:
            g3.append(totals["C4"]["NEW"][0] == totals["C4"]["NEW"][3])
        print(f"G3 controls not worse: {all(g3)} {g3}")
    if "conversation" in report:
        print("\n== Conversation answers (raw, before the retry) ==")
        unfinished = dict.fromkeys(ARMS, 0)
        english_bad = 0
        for item in report["conversation"]:
            print(f"{item['id']} | {item['question']}")
            for arm in ARMS:
                for sample in item["samples"][arm]:
                    answer = sample.get("answer", sample.get("error", ""))
                    bad = is_unfinished_answer(answer) or "error" in sample
                    unfinished[arm] += bad
                    if (
                        arm == "NEW"
                        and item["english"]
                        and not bad
                        and not mostly_ascii(answer)
                    ):
                        english_bad += 1
                    print(
                        f"   {arm:8} {'UNFINISHED' if bad else 'ok        '} {answer[:160]}"
                    )
        print(
            f"unfinished: {unfinished}; NEW English answers not mostly ASCII: {english_bad}"
        )
        print(f"G4: {unfinished['NEW'] <= unfinished['BASELINE'] and english_bad == 0}")
    if "answer" in report:
        print("\n== LLM 2 with the English requirement (reported, not gated) ==")
        for sample in report["answer"]:
            response = sample.get("response", {})
            print(
                f"   ascii={mostly_ascii(response.get('answer', ''))} "
                f"delivery={sample.get('delivery', sample.get('error'))} "
                f"{response.get('answer', '')[:200]!r}"
            )


# ---------------------------------------------------------------- runs


def llm1_items(repeats: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for history_id in HISTORIES:
        for request_id, request, marker, path in FORMAT_REQUESTS:
            items.append(
                {
                    "kind": "FORMAT",
                    "id": f"{request_id}@{history_id}",
                    "history": history_id,
                    "question": request,
                    "marker": marker,
                    "expected_path": path,
                }
            )
    for item_id, history_id, question, forbidden in NEW_TASKS:
        items.append(
            {
                "kind": "C1",
                "id": item_id,
                "history": history_id,
                "question": question,
                "forbidden": forbidden,
            }
        )
    for item_id, history_id, question in OUT_OF_SCOPE:
        items.append(
            {"kind": "C2", "id": item_id, "history": history_id, "question": question}
        )
    for item_id, history_id, question in ELLIPTICAL:
        items.append(
            {"kind": "C3", "id": item_id, "history": history_id, "question": question}
        )
    items.append(
        {
            "kind": "C4",
            "id": "en_no_history",
            "history": None,
            "question": "Answer in English",
        }
    )
    for item_id, question, _ in EXCLUSION_QUESTIONS:
        items.append(
            {"kind": "C5", "id": item_id, "history": None, "question": question}
        )
    for item in items:
        item["repeats"] = 2 if item["kind"] == "C5" else repeats
        item["samples"] = {arm: [] for arm in ARMS}
    return items


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summarize", type=Path)
    parser.add_argument("--parts", default="llm1,conversation,answer")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--conv-repeats", type=int, default=8)
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
    parts = {p.strip() for p in args.parts.split(",") if p.strip()}

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
        case = cases[0]
        report: dict[str, Any] = {"model": llm.label}

        async def structured(
            prompt: str, system: str, schema: Any, **kwargs: Any
        ) -> dict[str, Any]:
            async with semaphore:
                provider = create_llm_provider(settings, llm.snapshot)
                try:
                    parsed = await provider.complete_structured(
                        prompt, system=system, schema=schema, **kwargs
                    )
                    return parsed.model_dump()
                except Exception as exc:
                    return {"error": type(exc).__name__}
                finally:
                    await provider.aclose()

        if "llm1" in parts:
            items = llm1_items(args.repeats)
            jobs = []
            for item in items:
                history = HISTORIES[item["history"]][1] if item["history"] else []
                new_prompt = build_interpreter_prompt(
                    question=item["question"],
                    history=history,
                    knowledge_name=case.product_name,
                    scope_description=case.scope_description,
                    max_rounds=settings.m3_rag_history_max_rounds,
                )
                prompts = {
                    "NEW": new_prompt,
                    "BASELINE": baseline_interpreter_prompt(new_prompt),
                }

                async def one(
                    arm: str,
                    item: dict[str, Any] = item,
                    prompts: dict[str, str] = prompts,
                ) -> None:
                    item["samples"][arm].append(
                        await structured(
                            prompts[arm],
                            INTERPRETER_SYSTEM_PROMPT,
                            QueryInterpretation,
                            max_tokens=settings.m3_rag_query_interpreter_max_tokens,
                            temperature=0.0,
                            timeout_seconds=settings.m3_rag_query_interpreter_timeout_seconds,
                        )
                    )

                jobs += [one(arm) for _ in range(item["repeats"]) for arm in ARMS]
            await asyncio.gather(*jobs)
            report["llm1"] = items

        if "conversation" in parts:
            conv_items: list[dict[str, Any]] = []
            jobs = []
            for item_id, history_id, question, resolved, english in CONVERSATION_INPUTS:
                new_prompt = build_conversation_prompt(
                    question=question,
                    resolved_question=resolved,
                    history=HISTORIES[history_id][1],
                    product_name=case.product_name,
                    scope_description=case.scope_description,
                )
                prompts = {
                    "NEW": new_prompt,
                    "BASELINE": baseline_conversation_prompt(new_prompt),
                }
                item = {
                    "id": item_id,
                    "question": question,
                    "english": english,
                    "samples": {arm: [] for arm in ARMS},
                }
                conv_items.append(item)

                async def conv(
                    arm: str,
                    item: dict[str, Any] = item,
                    prompts: dict[str, str] = prompts,
                ) -> None:
                    item["samples"][arm].append(
                        await structured(
                            prompts[arm],
                            CONVERSATION_SYSTEM_PROMPT,
                            ConversationAnswer,
                            max_tokens=512,
                            temperature=0.1,
                            timeout_seconds=20.0,
                            thinking=False,
                        )
                    )

                jobs += [conv(arm) for _ in range(args.conv_repeats) for arm in ARMS]
            await asyncio.gather(*jobs)
            report["conversation"] = conv_items

        if "answer" in parts:
            prompt = answer_generator.build_answer_prompt(
                history=HISTORIES["psu"][1],
                interpretation=QueryInterpretation(
                    resolved_question="如何更换 PSU 电源板？请用英文回答。",
                    scope="IN_SCOPE",
                    relation="FOLLOW_UP",
                    active_task="更换 PSU 电源板",
                    response_path="KNOWLEDGE",
                ),
                context=case.context,
                product_name=case.product_name,
                scope_description=case.scope_description,
                max_rounds=settings.m3_rag_history_max_rounds,
            )

            async def answer_once() -> dict[str, Any]:
                async with semaphore:
                    provider = create_llm_provider(settings, llm.snapshot)
                    try:
                        generated = await answer_generator.generate_answer(
                            provider,
                            prompt=prompt,
                            max_tokens=settings.m3_rag_answer_max_tokens,
                            timeout_seconds=settings.m3_rag_answer_timeout_seconds,
                            thinking_enabled=False,
                            thinking_budget=settings.m3_rag_answer_thinking_budget,
                        )
                        response = generated.model_dump()
                        return {
                            "response": response,
                            "delivery": deliver(case, response),
                        }
                    except Exception as exc:
                        return {"error": type(exc).__name__}
                    finally:
                        await provider.aclose()

            report["answer"] = list(
                await asyncio.gather(*[answer_once() for _ in range(3)])
            )

        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        summarize(args.output)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
