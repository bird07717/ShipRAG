"""RAG 链路黄金集运行器（黑盒，经 HTTP 调用真实链路）。

用法：
    .venv/bin/python rag-acceptance/run_golden.py [--base-url URL] [--kb-id ID]

凭据从环境变量 GOLDEN_TOKEN / SERVICE_TOKEN 读取，或回退到项目根目录 .env。
结果写入 rag-acceptance/reports/<run_id>.json，并在终端打印分类摘要。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import unicodedata
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
BOOK_TITLE_PATTERN = re.compile(r"《([^》]{2,120})》")


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(
        ch
        for ch in value
        if not ch.isspace() and not unicodedata.category(ch).startswith("P")
    )


def load_token() -> str:
    import os

    token = os.environ.get("GOLDEN_TOKEN") or os.environ.get("SERVICE_TOKEN")
    if token:
        return token
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("SERVICE_TOKEN="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("未找到服务凭据：设置 GOLDEN_TOKEN 或在项目根目录 .env 中配置 SERVICE_TOKEN")


def _fuzzy_title_match(mention_norm: str, title_norm: str) -> bool:
    """容忍 LLM 转写标题时的尾部后缀幻觉（如“-中文”写成“-英文”）。"""
    import difflib

    if not mention_norm or not title_norm:
        return False
    shorter, longer = (
        (mention_norm, title_norm) if len(mention_norm) <= len(title_norm) else (title_norm, mention_norm)
    )
    if len(shorter) >= 6 and shorter in longer:
        return True
    return difflib.SequenceMatcher(a=mention_norm, b=title_norm).ratio() >= 0.85


def extract_hits(response: dict[str, Any], catalog_norm: dict[str, str]) -> set[str]:
    """从响应的 references / sources / answer《》文本中提取命中的知识库文档标题。"""
    mentioned: set[str] = set()
    for ref in response.get("references") or []:
        if ref.get("title"):
            mentioned.add(str(ref["title"]))
    for src in response.get("sources") or []:
        if src.get("document"):
            mentioned.add(str(src["document"]))
    answer = str(response.get("answer") or "")
    for match in BOOK_TITLE_PATTERN.findall(answer):
        mentioned.add(match)

    hits: set[str] = set()
    for candidate in mentioned:
        candidate_norm = normalize(candidate)
        if not candidate_norm:
            continue
        for title, title_norm in catalog_norm.items():
            if candidate_norm in title_norm or title_norm in candidate_norm:
                hits.add(title)
            elif _fuzzy_title_match(candidate_norm, title_norm):
                hits.add(title)
    return hits


def evaluate(expected: dict[str, Any], hits: set[str]) -> tuple[bool, list[str]]:
    tolerance = expected.get("tolerance", "EXACT")
    keys = [normalize(k) for k in expected.get("document_keys") or []]
    forbidden = [normalize(k) for k in expected.get("forbidden_keys") or []]
    hit_norms = {normalize(h) for h in hits}
    problems: list[str] = []

    if tolerance == "NONE":
        if hits:
            problems.append(f"范围外问题命中了文档: {sorted(hits)}")
        return (not problems, problems)

    # document_keys is optional: an empty list means "no must-hit document"
    # (refusal / meta-utterance turns), leaving forbidden_keys as the only
    # document-level constraint.
    matched = any(any(k in h for k in keys) for h in hit_norms) if keys else True
    if not matched:
        problems.append(f"未命中期望文档 keys={expected.get('document_keys')} 实际={sorted(hits)}")
    bad = {h for h in hit_norms if any(f in h for f in forbidden)}
    if bad:
        problems.append(f"命中了禁止文档: {sorted(bad)}")
    return (not problems, problems)


def evaluate_answer(expected: dict[str, Any], answer: str) -> list[str]:
    """Answer-text assertions for turns whose quality is not document hits.

    answer_must_any: normalized answer must contain at least one entry.
    answer_forbidden_any: normalized answer must contain none of the entries.
    """
    answer_norm = normalize(answer)
    problems: list[str] = []
    must_any = [normalize(k) for k in expected.get("answer_must_any") or []]
    forbidden_any = [normalize(k) for k in expected.get("answer_forbidden_any") or []]
    if must_any and not any(k in answer_norm for k in must_any):
        problems.append(f"回答缺少必需内容 any={expected.get('answer_must_any')}")
    leaked = [k for k in forbidden_any if k in answer_norm]
    if leaked:
        problems.append(f"回答包含禁止内容: {leaked}")
    return problems


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(ratio * (len(ordered) - 1))))
    return ordered[index]


async def run_turn(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    kb_id: str,
    question: str,
    conversation_id: str | None,
    request_id: str,
) -> tuple[dict[str, Any] | None, int, str | None]:
    started = time.perf_counter()
    try:
        resp = await client.post(
            f"{base_url}/api/v1/chat",
            json={
                "knowledge_id": kb_id,
                "question": question,
                "request_id": request_id,
                **({"conversation_id": conversation_id} if conversation_id else {}),
            },
            timeout=180.0,
        )
        elapsed = int((time.perf_counter() - started) * 1000)
        if resp.status_code != 200:
            return None, elapsed, f"HTTP {resp.status_code}: {resp.text[:200]}"
        return resp.json(), elapsed, None
    except Exception as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return None, elapsed, f"{type(exc).__name__}: {exc}"


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--kb-id", default="5ae9881a-a568-436b-9330-2da5e9a2a9c9")
    parser.add_argument("--cases", default=str(ROOT / "rag-acceptance" / "cases.jsonl"))
    parser.add_argument("--out-dir", default=str(ROOT / "rag-acceptance" / "reports"))
    args = parser.parse_args()

    token = load_token()
    cases = [
        json.loads(line)
        for line in Path(args.cases).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(headers=headers) as client:
        ready = await client.get(f"{args.base_url}/health/live", timeout=10.0)
        if ready.status_code != 200:
            print(f"后端不可用: HTTP {ready.status_code}")
            return 2

        docs_resp = await client.get(
            f"{args.base_url}/api/v1/knowledge-bases/{args.kb_id}/documents",
            params={"page_size": 200},
            timeout=30.0,
        )
        docs = docs_resp.json()
        docs = docs if isinstance(docs, list) else docs.get("items", [])
        catalog = [str(d.get("display_name") or d.get("filename") or "") for d in docs]
        catalog_norm = {t: normalize(t) for t in catalog if t}
        print(f"知识库目录: {len(catalog_norm)} 份文档")

        run_id = time.strftime("%Y%m%d-%H%M%S")
        results: list[dict[str, Any]] = []
        turn_total = 0

        for case in cases:
            turns = case.get("turns") or [
                {"question": case["question"], "expected": case["expected"]}
            ]
            conversation_id: str | None = None
            case_result: dict[str, Any] = {
                "id": case["id"],
                "category": case["category"],
                "turns": [],
                "passed": True,
            }
            for step, turn in enumerate(turns, start=1):
                turn_total += 1
                request_id = f"golden-{run_id}-{case['id']}-{step}"
                payload, latency_ms, error = await run_turn(
                    client,
                    base_url=args.base_url,
                    kb_id=args.kb_id,
                    question=turn["question"],
                    conversation_id=conversation_id,
                    request_id=request_id,
                )
                if error is not None or payload is None:
                    case_result["turns"].append(
                        {"question": turn["question"], "error": error, "latency_ms": latency_ms}
                    )
                    case_result["passed"] = False
                    print(f"[FAIL] {case['id']}#{step} {turn['question'][:24]} -> {error}")
                    break

                conversation_id = str(payload.get("conversation_id") or conversation_id or "")
                hits = extract_hits(payload, catalog_norm)
                passed, problems = evaluate(turn["expected"], hits)
                problems = list(problems)
                problems += evaluate_answer(turn["expected"], str(payload.get("answer") or ""))
                max_latency = turn["expected"].get("max_latency_ms")
                if max_latency is not None and latency_ms > int(max_latency):
                    problems.append(f"延迟超限 {latency_ms}ms > {max_latency}ms")
                passed = not problems
                case_result["turns"].append(
                    {
                        "question": turn["question"],
                        "response_type": payload.get("response_type"),
                        "answer_mode": payload.get("answer_mode"),
                        "latency_ms": latency_ms,
                        "hits": sorted(hits),
                        "passed": passed,
                        "problems": problems,
                        "answer_snippet": str(payload.get("answer") or "")[:180],
                    }
                )
                if not passed:
                    case_result["passed"] = False
                status = "PASS" if passed else "FAIL"
                print(
                    f"[{status}] {case['id']}#{step} {turn['question'][:24]} "
                    f"-> {payload.get('response_type')} | {sorted(hits) or '-'} | {latency_ms}ms"
                )
                await asyncio.sleep(0.5)
            results.append(case_result)

    by_category: dict[str, dict[str, Any]] = defaultdict(lambda: {"cases": 0, "passed": 0})
    latencies: list[float] = []
    for case in results:
        bucket = by_category[case["category"]]
        bucket["cases"] += 1
        bucket["passed"] += 1 if case["passed"] else 0
        for turn in case["turns"]:
            if "latency_ms" in turn:
                latencies.append(float(turn["latency_ms"]))

    total = len(results)
    passed = sum(1 for c in results if c["passed"])
    report = {
        "run_id": run_id,
        "base_url": args.base_url,
        "knowledge_id": args.kb_id,
        "total_cases": total,
        "total_turns": turn_total,
        "passed_cases": passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "by_category": {
            cat: {
                "cases": stats["cases"],
                "passed": stats["passed"],
                "pass_rate": round(stats["passed"] / stats["cases"], 4)
                if stats["cases"]
                else 0.0,
            }
            for cat, stats in sorted(by_category.items())
        },
        "latency_ms": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "max": max(latencies) if latencies else 0,
        },
        "results": results,
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"baseline-{run_id}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n===== 黄金集基线报告 =====")
    print(f"用例: {total} | 通过: {passed} | 通过率: {report['pass_rate']:.1%}")
    for cat, stats in report["by_category"].items():
        print(f"  {cat:<14} {stats['passed']}/{stats['cases']}  ({stats['pass_rate']:.0%})")
    lat = report["latency_ms"]
    print(f"延迟: p50={lat['p50']:.0f}ms p95={lat['p95']:.0f}ms max={lat['max']:.0f}ms")
    print(f"报告: {out_path}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
