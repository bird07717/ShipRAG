"""Collect real Assistant answers for human review; no scoring or assertions.

Use the same questions and test knowledge base before/after a prompt change.
Reports contain full public answers and internal diagnostics, never credentials.
Existing Golden responses can be imported without their pass/fail judgments.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from run_golden import load_token, observe_trace, run_turn


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18001")
    parser.add_argument("--kb-id", required=True)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--import-report", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(
            "Output already exists; use a new filename to preserve prior results"
        )
    questions = [
        json.loads(line)
        for line in args.questions.read_text().splitlines()
        if line.strip()
    ]
    report: dict[str, Any] = {
        "label": args.label,
        "knowledge_id": args.kb_id,
        "results": [],
    }

    def save() -> None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if args.import_report:
        original = json.loads(args.import_report.read_text())
        if original["knowledge_id"] != args.kb_id:
            raise ValueError("Imported report uses a different knowledge base")
        rows = {row["id"]: row for row in original["results"]}
        for question in questions:
            turns = rows[question["id"]]["turns"]
            if [turn["question"] for turn in turns] != question["questions"]:
                raise ValueError("Imported questions differ from the review questions")
            report["results"].append(
                {
                    "id": question["id"],
                    "focus": question["focus"],
                    "turns": [
                        {
                            key: turn[key]
                            for key in ("question", "response", "latency_ms", "error")
                            if key in turn
                        }
                        for turn in turns
                    ],
                }
            )
        report["source_report"] = str(args.import_report)
        save()
        print(f"Imported complete answers without scores: {args.output}", flush=True)
        return

    run_id = uuid4().hex
    async with httpx.AsyncClient(
        headers={"Authorization": "Bearer " + load_token("assistant")}
    ) as client:
        runtime = await client.get(
            args.base_url + "/api/v1/system/model-runtime",
            headers={"Authorization": "Bearer " + load_token("admin")},
            timeout=30,
        )
        runtime.raise_for_status()
        report["runtime"] = runtime.json()
        report["base_url"] = args.base_url
        for question in questions:
            row: dict[str, Any] = {
                "id": question["id"],
                "focus": question["focus"],
                "turns": [],
            }
            report["results"].append(row)
            conversation_id = None
            for step, content in enumerate(question["questions"], start=1):
                request_id = f"context-review-{run_id}-{question['id']}-{step}"
                response, latency_ms, error = await run_turn(
                    client,
                    base_url=args.base_url,
                    kb_id=args.kb_id,
                    question=content,
                    conversation_id=conversation_id,
                    request_id=request_id,
                    api_mode="assistant",
                    external_user_id=f"context-review-{run_id}",
                )
                turn: dict[str, Any] = {
                    "question": content,
                    "response": response,
                    "latency_ms": latency_ms,
                    "error": error,
                }
                row["turns"].append(turn)
                if response:
                    conversation_id = response.get("conversation_id")
                    trace, trace_error = await observe_trace(
                        client,
                        base_url=args.base_url,
                        token=load_token("admin"),
                        request_id=request_id,
                    )
                    turn["trace_error"] = trace_error
                    if trace:
                        turn["diagnostics"] = {
                            key: trace.get(key)
                            for key in (
                                "trace_id",
                                "index_id",
                                "runtime_snapshot",
                                "model_usage",
                            )
                        }
                save()
                print(
                    f"{args.label} {question['id']}#{step}: "
                    + (error or "answer saved for human review"),
                    flush=True,
                )
                if error:
                    break


if __name__ == "__main__":
    asyncio.run(main())
