"""Paired, repeated REAL LLM2 captures; no quality assertions or model judge.

Loads previously observed document selections from an Assistant capture, freezes
the real context once per question, then alternates baseline/candidate calls.
This bypasses LLM1/retrieval and is NOT an end-to-end or Golden Set run.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.rag.llm import create_llm_provider
from app.rag.models import ModelSnapshot
from app.rag.repository import RagRepository
from app.rag.v2 import answer_generator
from app.rag.v2.document_context import build_document_context
from app.rag.v2.document_retrieval import DocumentCandidate
from app.rag.v2.query_interpreter import QueryInterpretation
from context_prompt_candidate import build_candidate_prompt


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--case-id", action="append", default=[])
    args = parser.parse_args()
    if args.output.exists():
        parser.error(
            "Output already exists; use a new filename to preserve prior results"
        )
    if args.repeats < 1 or args.concurrency < 1:
        parser.error("repeats and concurrency must be positive")
    source = json.loads(args.input.read_text())
    settings = Settings()
    engine = create_async_engine(settings.database_url.get_secret_value())
    repo = RagRepository(engine, settings)
    report: dict[str, Any] = {
        "method": "Repeated paired LLM2 only, fixed real documents/history/task; no quality scoring",
        "source": str(args.input),
        "knowledge_id": source["knowledge_id"],
        "repeats": args.repeats,
        "baseline_code_sha256": sha(inspect.getsource(answer_generator)),
        "candidate_code_sha256": sha(inspect.getsource(build_candidate_prompt)),
        "results": [],
    }

    def save() -> None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    try:
        async with engine.connect() as connection:
            model = dict(
                (
                    await connection.execute(
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
                    await connection.execute(
                        text(
                            "SELECT name, description, COALESCE(rag_active_index_id, active_index_id) "
                            "AS active_index_id FROM knowledge_base WHERE id=:id"
                        ),
                        {"id": UUID(source["knowledge_id"])},
                    )
                )
                .mappings()
                .one()
            )
        snapshot = ModelSnapshot(
            provider=model["provider"],
            model_name=model["model_name"],
            parameters=model["parameters"] or {},
            base_url=model["base_url"],
            api_key=repo._decrypt_api_key(model),
        )
        # Factory check rejects missing/FAKE providers before any paid captures.
        provider_check = create_llm_provider(settings, snapshot)
        if type(provider_check).__name__ == "FakeLlmProvider":
            await provider_check.aclose()
            raise RuntimeError("Review requires a real LLM provider")
        await provider_check.aclose()
        report["model"] = {"provider": snapshot.provider, "name": snapshot.model_name}
        report["generation_settings"] = {
            "max_tokens": settings.m3_rag_answer_max_tokens,
            "timeout_seconds": settings.m3_rag_answer_timeout_seconds,
            "thinking_enabled": settings.m3_rag_answer_thinking_enabled,
            "thinking_budget": settings.m3_rag_answer_thinking_budget,
            "temperature": 0.1,
        }
        cases: list[dict[str, Any]] = []
        for item in source["results"]:
            # For correction, freeze a shared prior captured response for BOTH
            # variants instead of making separate candidate-produced histories.
            turn = item["turns"][-1]
            diagnostics = turn["diagnostics"]
            index_id = diagnostics["index_id"]
            if index_id != str(profile["active_index_id"]):
                raise RuntimeError("Active index changed since source capture")
            history: list[dict[str, str]] = []
            if len(item["turns"]) > 1:
                previous = item["turns"][0]
                history = [
                    {"role": "user", "content": previous["question"]},
                    {"role": "assistant", "content": previous["response"]["answer"]},
                ]
            cases.append(
                {
                    "id": item["id"],
                    "question": turn["question"],
                    "document_ids": diagnostics["runtime_snapshot"]["generation"][
                        "context"
                    ]["document_ids"],
                    "index_id": index_id,
                    "history": history,
                }
            )
        index_id = cases[0]["index_id"]
        cases.extend(
            [
                {
                    "id": "context_lru_exact_source",
                    "index_id": index_id,
                    "question": "按检查 VDR600 LRU 通讯线缆的文档，断电后需要等待多久才能继续操作？只回答等待时间。",
                    "document_ids": ["8eba030c-3d55-4d50-83af-331c98bd56e3"],
                    "history": [],
                },
                {
                    "id": "context_psu_full_operation",
                    "index_id": index_id,
                    "question": "请完整说明 VDR600 PSU 电源板的更换步骤，并附上各步骤需要的图片。",
                    "document_ids": ["aad2f45a-2fdc-482c-af93-7b5a7833ee58"],
                    "history": [],
                },
            ]
        )
        if args.case_id:
            known = {case["id"] for case in cases}
            if set(args.case_id) - known:
                raise ValueError("Unknown case ID")
            cases = [case for case in cases if case["id"] in args.case_id]
        semaphore = asyncio.Semaphore(args.concurrency)

        async def capture_pair(
            row: dict[str, Any], prompts: dict[str, str], repeat: int, order: list[str]
        ) -> None:
            pair: dict[str, Any] = {"repeat": repeat, "order": order, "variants": {}}
            row["pairs"].append(pair)
            # Keep the pair sequential, with order counterbalanced by case/run.
            async with semaphore:
                for name in order:
                    provider = create_llm_provider(settings, snapshot)
                    started = time.perf_counter()
                    try:
                        generated = await answer_generator.generate_answer(
                            provider,
                            prompt=prompts[name],
                            max_tokens=settings.m3_rag_answer_max_tokens,
                            timeout_seconds=settings.m3_rag_answer_timeout_seconds,
                            thinking_enabled=settings.m3_rag_answer_thinking_enabled,
                            thinking_budget=settings.m3_rag_answer_thinking_budget,
                        )
                        result = {
                            "response": generated.model_dump(),
                            "usage": dict(provider.usage),
                        }
                    except Exception as exc:
                        result = {
                            "error": type(exc).__name__,
                            "code": getattr(exc, "code", None),
                        }
                    finally:
                        await provider.aclose()
                    result["latency_ms"] = round((time.perf_counter() - started) * 1000)
                    pair["variants"][name] = result
                    save()
                    print(
                        f"{row['id']} #{repeat} {name}: {'ERROR' if 'error' in result else 'captured'}",
                        flush=True,
                    )

        jobs = []
        for number, case in enumerate(cases):
            context = await build_document_context(
                repo,
                knowledge_id=UUID(source["knowledge_id"]),
                index_id=UUID(case["index_id"]),
                candidates=[
                    DocumentCandidate(UUID(did), 0, (), None)
                    for did in case["document_ids"]
                ],
                token_budget=min(
                    settings.m3_rag_full_context_token_budget,
                    settings.m3_prompt_token_budget,
                ),
            )
            interpretation = QueryInterpretation(
                resolved_question=case["question"],
                scope="IN_SCOPE",
                response_path="KNOWLEDGE",
                relation="CORRECTION" if case["history"] else "NEW_TASK",
                active_task=case["question"] if case["history"] else None,
            )
            kwargs = {
                "history": case["history"],
                "interpretation": interpretation,
                "context": context,
                "product_name": profile["name"],
                "scope_description": profile["description"] or "",
                "max_rounds": settings.m3_rag_history_max_rounds,
            }
            prompts = {
                "baseline": answer_generator.build_answer_prompt(**kwargs),
                "candidate": build_candidate_prompt(**kwargs),
            }
            baseline_payload, candidate_payload = (
                json.loads(prompts[name]) for name in ("baseline", "candidate")
            )
            # Method invariant, NOT an assertion of answer quality.
            baseline_payload.pop("decision_policy")
            candidate_payload.pop("decision_policy")
            if baseline_payload != candidate_payload:
                raise RuntimeError("Candidate changed more than decision_policy")
            row = {
                **case,
                "document_ids": list(context.document_ids),
                "context_sha256": sha(context.prompt_context),
                "prompt_chars": {name: len(value) for name, value in prompts.items()},
                "prompt_sha256": {name: sha(value) for name, value in prompts.items()},
                "pairs": [],
            }
            report["results"].append(row)
            for repeat in range(1, args.repeats + 1):
                order = ["baseline", "candidate"]
                if (number + repeat) % 2:
                    order.reverse()
                jobs.append(capture_pair(row, prompts, repeat, order))
        save()
        await asyncio.gather(*jobs)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
