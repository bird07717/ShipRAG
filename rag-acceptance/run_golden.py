"""RAG 链路黄金集运行器（黑盒，经 HTTP 调用真实链路）。

用法：
    .venv/bin/python rag-acceptance/run_golden.py [--api-mode admin|assistant]
        [--base-url URL] [--kb-id ID]

管理模式凭据从 GOLDEN_TOKEN / SERVICE_TOKEN 读取；Assistant 模式凭据从
GOLDEN_ASSISTANT_TOKEN / ASSISTANT_SERVICE_TOKEN 读取，均可回退到项目根目录 .env。
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
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

from app.rag.evaluation import evaluate_content, response_text

ROOT = Path(__file__).resolve().parent.parent
BOOK_TITLE_PATTERN = re.compile(r"《([^》]{2,120})》")


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(
        ch
        for ch in value
        if not ch.isspace() and not unicodedata.category(ch).startswith("P")
    )


def _env_value(*names: str) -> str | None:
    import os

    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() in names and value.strip():
                normalized = value.strip()
                if (
                    len(normalized) >= 2
                    and normalized[0] == normalized[-1]
                    and normalized[0] in "\"'"
                ):
                    normalized = normalized[1:-1]
                return normalized
    return None


def load_token(api_mode: str) -> str:
    if api_mode == "assistant":
        token = _env_value("GOLDEN_ASSISTANT_TOKEN", "ASSISTANT_SERVICE_TOKEN")
        if token:
            return token
        raise SystemExit(
            "未找到 Assistant 凭据：设置 GOLDEN_ASSISTANT_TOKEN，或在项目根目录 "
            ".env 中配置 ASSISTANT_SERVICE_TOKEN"
        )
    token = _env_value("GOLDEN_TOKEN", "SERVICE_TOKEN")
    if token:
        return token
    raise SystemExit(
        "未找到管理服务凭据：设置 GOLDEN_TOKEN，或在项目根目录 .env 中配置 SERVICE_TOKEN"
    )


def _fuzzy_title_match(mention_norm: str, title_norm: str) -> bool:
    """容忍 LLM 转写标题时的尾部后缀幻觉（如“-中文”写成“-英文”）。"""
    import difflib

    if not mention_norm or not title_norm:
        return False
    shorter, longer = (
        (mention_norm, title_norm)
        if len(mention_norm) <= len(title_norm)
        else (title_norm, mention_norm)
    )
    if len(shorter) >= 6 and shorter in longer:
        return True
    return difflib.SequenceMatcher(a=mention_norm, b=title_norm).ratio() >= 0.85


def extract_hits(
    response: dict[str, Any],
    catalog_norm: dict[str, str],
    *,
    include_mentions: bool = True,
    include_suggestions: bool = False,
) -> set[str]:
    """从响应的 references / sources / answer《》文本中提取命中的知识库文档标题。"""
    mentioned: set[str] = set()
    for ref in response.get("references") or []:
        if ref.get("title"):
            mentioned.add(str(ref["title"]))
    for src in response.get("sources") or []:
        if src.get("document"):
            mentioned.add(str(src["document"]))
    answer = str(response.get("answer") or "")
    if include_mentions:
        mentioned.update(BOOK_TITLE_PATTERN.findall(answer))
    if include_suggestions:
        mentioned.update(str(item) for item in response.get("next_suggestions") or [])

    # The external Assistant contract intentionally has no document-catalog
    # endpoint. Its public references already carry document titles, so the
    # Assistant Golden run evaluates those titles directly. Admin mode keeps
    # catalog-assisted fuzzy matching for backward-compatible baselines.
    if not catalog_norm:
        return mentioned

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
        problems.append(
            f"未命中期望文档 keys={expected.get('document_keys')} 实际={sorted(hits)}"
        )
    bad = {h for h in hit_norms if any(f in h for f in forbidden)}
    if bad:
        problems.append(f"命中了禁止文档: {sorted(bad)}")
    if "allowed_document_keys" in expected:
        allowed = [normalize(k) for k in expected["allowed_document_keys"]]
        unexpected = {h for h in hit_norms if not any(k in h for k in allowed)}
        if unexpected:
            problems.append(f"引用超出允许范围: {sorted(unexpected)}")
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


def v2_expected(expected: dict[str, Any]) -> dict[str, Any]:
    """Translate legacy public-delivery assertions to V2 hidden evidence semantics."""

    adapted = dict(expected)
    response_types = list(adapted.get("response_types") or [])
    if any(item in {"DOC_DELIVERED", "DOCUMENT_DELIVERED"} for item in response_types):
        adapted["response_types"] = list(
            dict.fromkeys(
                "ANSWERED" if item in {"DOC_DELIVERED", "DOCUMENT_DELIVERED"} else item
                for item in response_types
            )
        )
    adapted.pop("require_reference", None)
    return adapted


def v2_trace_hits(
    trace: dict[str, Any], catalog_by_id: dict[str, str]
) -> tuple[bool, set[str], list[str]]:
    generation = (trace.get("runtime_snapshot") or {}).get("generation") or {}
    if generation.get("pipeline") != "V2":
        return False, set(), []
    answer = generation.get("answer") or {}
    ids = [str(item) for item in answer.get("used_document_ids") or []]
    missing = [item for item in ids if item not in catalog_by_id]
    problems = [f"V2 Trace 使用了目录外文档 ID: {item}" for item in missing]
    return (
        True,
        {catalog_by_id[item] for item in ids if item in catalog_by_id},
        problems,
    )


def v2_trace_expectation_problems(
    trace: dict[str, Any], expected: dict[str, Any]
) -> list[str]:
    generation = (trace.get("runtime_snapshot") or {}).get("generation") or {}
    problems: list[str] = []

    expected_path = expected.get("trace_response_path")
    if expected_path is not None and generation.get("response_path") != expected_path:
        problems.append(
            "V2 Trace 回答路径不符: "
            f"{generation.get('response_path')}，期望 {expected_path}"
        )

    expected_guard = expected.get("trace_input_guard")
    if expected_guard is not None and generation.get("input_guard") != expected_guard:
        problems.append(
            "V2 Trace 输入短路不符: "
            f"{generation.get('input_guard')}，期望 {expected_guard}"
        )

    expected_retrieval = expected.get("trace_retrieval_status")
    actual_retrieval = (generation.get("retrieval") or {}).get("status")
    if expected_retrieval is not None and actual_retrieval != expected_retrieval:
        problems.append(
            f"V2 Trace 检索状态不符: {actual_retrieval}，期望 {expected_retrieval}"
        )
    return problems


async def observe_trace(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    token: str,
    request_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    headers = {"Authorization": f"Bearer {token}"}
    summaries = await client.get(
        f"{base_url}/api/v1/traces",
        params={"request_id": request_id, "limit": 5},
        headers=headers,
        timeout=30.0,
    )
    if summaries.status_code != 200:
        return None, f"Trace 列表不可用: HTTP {summaries.status_code}"
    items = [item for item in summaries.json() if item.get("request_id") == request_id]
    if len(items) != 1:
        return None, f"Trace 数量异常: request_id={request_id} count={len(items)}"
    detail = await client.get(
        f"{base_url}/api/v1/traces/{items[0]['trace_id']}",
        headers=headers,
        timeout=30.0,
    )
    if detail.status_code != 200:
        return None, f"Trace 详情不可用: HTTP {detail.status_code}"
    return detail.json(), None


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(ratio * (len(ordered) - 1)))
    return ordered[index]


async def run_turn(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    kb_id: str,
    question: str,
    conversation_id: str | None,
    request_id: str,
    api_mode: str,
    external_user_id: str,
) -> tuple[dict[str, Any] | None, int, str | None]:
    started = time.perf_counter()
    try:
        if api_mode == "assistant":
            headers = {
                "Accept": "text/event-stream",
                "X-External-User-ID": external_user_id,
                "X-Request-ID": request_id,
                "Idempotency-Key": request_id,
            }
            async with client.stream(
                "POST",
                f"{base_url}/api/v1/assistant/chat/stream",
                headers=headers,
                json={
                    "knowledge_id": kb_id,
                    "question": question,
                    **({"conversation_id": conversation_id} if conversation_id else {}),
                },
                timeout=180.0,
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    elapsed = int((time.perf_counter() - started) * 1000)
                    return None, elapsed, f"HTTP {resp.status_code}: {body[:200]}"

                event_name: str | None = None
                data_lines: list[str] = []
                async for line in resp.aiter_lines():
                    if line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                        continue
                    if line or not data_lines:
                        continue
                    try:
                        payload = json.loads("\n".join(data_lines))
                    except json.JSONDecodeError:
                        payload = None
                    if event_name == "complete" and isinstance(payload, dict):
                        elapsed = int((time.perf_counter() - started) * 1000)
                        return payload, elapsed, None
                    if event_name == "error" and isinstance(payload, dict):
                        elapsed = int((time.perf_counter() - started) * 1000)
                        code = payload.get("code", "UNKNOWN")
                        message = payload.get("message", "Assistant stream failed")
                        return None, elapsed, f"SSE {code}: {message}"
                    event_name, data_lines = None, []
                elapsed = int((time.perf_counter() - started) * 1000)
                return None, elapsed, "SSE 流在没有 complete/error 终态时结束"

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
    parser.add_argument("--api-mode", choices=("admin", "assistant"), default="admin")
    parser.add_argument(
        "--external-user-id",
        help="Assistant 模式使用的稳定测试用户标识；默认按本次运行生成",
    )
    parser.add_argument("--cases", default=str(ROOT / "rag-acceptance" / "cases.jsonl"))
    parser.add_argument(
        "--case-id",
        action="append",
        dest="case_ids",
        help="只运行指定用例；可重复传入，默认运行用例文件中的全部内容",
    )
    parser.add_argument("--out-dir", default=str(ROOT / "rag-acceptance" / "reports"))
    args = parser.parse_args()

    token = load_token(args.api_mode)
    cases = [
        json.loads(line)
        for line in Path(args.cases).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.case_ids:
        selected_ids = set(args.case_ids)
        cases = [case for case in cases if case.get("id") in selected_ids]
        missing_ids = selected_ids - {str(case.get("id")) for case in cases}
        if missing_ids:
            raise SystemExit(
                "未找到指定 Golden 用例: " + ", ".join(sorted(missing_ids))
            )

    run_id = time.strftime("%Y%m%d-%H%M%S")
    external_user_id = args.external_user_id or f"golden-assistant-{run_id}"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(headers=headers) as client:
        ready = await client.get(f"{args.base_url}/health/live", timeout=10.0)
        if ready.status_code != 200:
            print(f"后端不可用: HTTP {ready.status_code}")
            return 2

        if args.api_mode == "assistant":
            kb_resp = await client.get(
                f"{args.base_url}/api/v1/assistant/knowledge-bases",
                headers={"X-External-User-ID": external_user_id},
                timeout=30.0,
            )
            if kb_resp.status_code != 200:
                print(
                    f"Assistant 知识库接口不可用: HTTP {kb_resp.status_code} {kb_resp.text[:200]}"
                )
                return 2
            available = {
                str(item.get("knowledge_id")): item
                for item in kb_resp.json().get("items", [])
                if isinstance(item, dict)
            }
            selected = available.get(str(args.kb_id))
            if selected is None:
                print(f"Assistant 不可见目标知识库: {args.kb_id}")
                return 2
            if not bool(selected.get("ready")):
                print(f"Assistant 目标知识库未就绪: {args.kb_id}")
                return 2
            observer_token = _env_value("GOLDEN_TOKEN", "SERVICE_TOKEN")
            if not observer_token:
                print(
                    "V2 Assistant Golden 需要 GOLDEN_TOKEN 或 SERVICE_TOKEN 读取内部 Trace"
                )
                return 2
            docs_resp = await client.get(
                f"{args.base_url}/api/v1/knowledge-bases/{args.kb_id}/documents",
                headers={"Authorization": f"Bearer {observer_token}"},
                timeout=30.0,
            )
            if docs_resp.status_code != 200:
                print(f"Golden 内部文档目录不可用: HTTP {docs_resp.status_code}")
                return 2
            docs_payload = docs_resp.json()
            docs = (
                docs_payload
                if isinstance(docs_payload, list)
                else docs_payload.get("items", [])
            )
            catalog_by_id = {
                str(item["id"]): str(
                    item.get("display_name") or item.get("filename") or ""
                )
                for item in docs
                if item.get("id")
            }
            catalog_norm: dict[str, str] = {
                title: normalize(title) for title in catalog_by_id.values() if title
            }
            print(f"Assistant 知识库: {selected.get('name')} ({args.kb_id})")
        else:
            observer_token = None
            catalog_by_id: dict[str, str] = {}
            docs_resp = await client.get(
                f"{args.base_url}/api/v1/knowledge-bases/{args.kb_id}/documents",
                params={"page_size": 200},
                timeout=30.0,
            )
            if docs_resp.status_code != 200:
                print(
                    f"知识库目录接口不可用: HTTP {docs_resp.status_code} {docs_resp.text[:200]}"
                )
                return 2
            docs = docs_resp.json()
            docs = docs if isinstance(docs, list) else docs.get("items", [])
            catalog = [
                str(d.get("display_name") or d.get("filename") or "") for d in docs
            ]
            catalog_norm = {t: normalize(t) for t in catalog if t}
            print(f"知识库目录: {len(catalog_norm)} 份文档")

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
                    api_mode=args.api_mode,
                    external_user_id=external_user_id,
                )
                if error is not None or payload is None:
                    case_result["turns"].append(
                        {
                            "question": turn["question"],
                            "error": error,
                            "latency_ms": latency_ms,
                        }
                    )
                    case_result["passed"] = False
                    print(
                        f"[FAIL] {case['id']}#{step} {turn['question'][:24]} -> {error}"
                    )
                    break

                conversation_id = str(
                    payload.get("conversation_id") or conversation_id or ""
                )
                expected = turn["expected"]
                public_hits = extract_hits(
                    payload,
                    catalog_norm,
                    include_mentions=expected.get("tolerance") != "NONE",
                    include_suggestions=bool(expected.get("allow_suggested_documents")),
                )
                hits = public_hits
                evidence_mode = "PUBLIC"
                observation_problems: list[str] = []
                if args.api_mode == "assistant" and observer_token:
                    trace, observation_error = await observe_trace(
                        client,
                        base_url=args.base_url,
                        token=observer_token,
                        request_id=request_id,
                    )
                    if observation_error:
                        observation_problems.append(observation_error)
                    elif trace is not None:
                        is_v2, hidden_hits, trace_problems = v2_trace_hits(
                            trace, catalog_by_id
                        )
                        if is_v2:
                            expected = v2_expected(expected)
                            hits = hidden_hits
                            evidence_mode = "V2_TRACE"
                            observation_problems.extend(trace_problems)
                            observation_problems.extend(
                                v2_trace_expectation_problems(trace, expected)
                            )
                passed, problems = evaluate(expected, hits)
                problems = list(problems)
                problems += observation_problems
                problems += evaluate_answer(expected, response_text(payload))
                problems += evaluate_content(expected, payload)
                max_latency = expected.get("max_latency_ms")
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
                        "evidence_mode": evidence_mode,
                        "passed": passed,
                        "problems": problems,
                        "answer_snippet": str(payload.get("answer") or "")[:180],
                        "response": payload,
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

    by_category: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"cases": 0, "passed": 0}
    )
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
        "api_mode": args.api_mode,
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
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("\n===== 黄金集基线报告 =====")
    print(f"接口模式: {args.api_mode}")
    print(f"用例: {total} | 通过: {passed} | 通过率: {report['pass_rate']:.1%}")
    for cat, stats in report["by_category"].items():
        print(
            f"  {cat:<14} {stats['passed']}/{stats['cases']}  ({stats['pass_rate']:.0%})"
        )
    lat = report["latency_ms"]
    print(f"延迟: p50={lat['p50']:.0f}ms p95={lat['p95']:.0f}ms max={lat['max']:.0f}ms")
    print(f"报告: {out_path}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
