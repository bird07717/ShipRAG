"""Regrade two complete-response reports with the SAME current assertions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from run_golden import evaluate, evaluate_answer, extract_hits, percentile

from app.rag.evaluation import evaluate_content, response_text


def regrade(report: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any]:
    records = {case["id"]: case for case in report["results"]}
    results = []
    latencies = []
    for case in cases:
        actual = records.get(case["id"], {}).get("turns", [])
        problems = []
        body_chars = 0
        for i, turn in enumerate(case.get("turns") or [case]):
            if i >= len(actual):
                problems.append(f"{i + 1}:缺少执行结果")
                continue
            row = actual[i]
            if row.get("question") != turn["question"]:
                raise ValueError(f"{case['id']} 的问题已变化，不能直接比较")
            if row.get("error"):
                problems.append(f"{i + 1}:{row['error']}")
                continue
            if "response" not in row:
                raise ValueError(
                    "报告缺少完整 response，不能用 answer_snippet 代替正文评分"
                )
            response = row["response"]
            expected = turn["expected"]
            _, issues = evaluate(
                expected,
                extract_hits(
                    response,
                    {},
                    include_mentions=expected.get("tolerance") != "NONE",
                    include_suggestions=bool(expected.get("allow_suggested_documents")),
                ),
            )
            issues += evaluate_answer(expected, response_text(response))
            issues += evaluate_content(expected, response)
            latency = row["latency_ms"]
            if (
                expected.get("max_latency_ms") is not None
                and latency > expected["max_latency_ms"]
            ):
                issues.append("延迟超限")
            problems.extend(f"{i + 1}:{issue}" for issue in issues)
            latencies.append(latency)
            body_chars += len(response_text(response))
        results.append(
            {
                "id": case["id"],
                "category": case["category"],
                "passed": not problems,
                "problems": problems,
                "body_chars": body_chars,
            }
        )
    return {
        "cases": len(results),
        "passed": sum(r["passed"] for r in results),
        "latency_ms": {
            "p50": percentile(latencies, 0.5),
            "p95": percentile(latencies, 0.95),
        },
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reports = [json.loads(path.read_text()) for path in [args.before, args.after]]
    if reports[0].get("knowledge_id") != reports[1].get("knowledge_id"):
        raise SystemExit("知识库不同，不能直接比较")
    if any(r.get("api_mode") != "assistant" for r in reports):
        raise SystemExit("当前对照仅支持 Assistant 公开响应报告")
    cases = [
        json.loads(line) for line in args.cases.read_text().splitlines() if line.strip()
    ]
    before, after = [regrade(report, cases) for report in reports]
    result = {
        "before_report": str(args.before),
        "after_report": str(args.after),
        "assertions": str(args.cases),
        "before": before,
        "after": after,
        "improved": [
            a["id"]
            for b, a in zip(before["results"], after["results"])
            if a["passed"] and not b["passed"]
        ],
        "regressed": [
            a["id"]
            for b, a in zip(before["results"], after["results"])
            if b["passed"] and not a["passed"]
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in {"before", "after"}},
            ensure_ascii=False,
        )
    )
    print(
        f"before {before['passed']}/{before['cases']}; after {after['passed']}/{after['cases']}"
    )


if __name__ == "__main__":
    main()
