"""Read-only local Trace replay; never imported by the online RAG/Support paths."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from app.core.config import Settings
from app.rag.doc_router import decide_doc_routing
from app.services.rag import RagService
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parent


def normalize(value):
    return re.sub(r"\s+", "", value).casefold()


def single_strong(candidates, status, floor, gap):
    """Use original provider scores; require an observed competing document."""
    if status != "PASSED" or not candidates:
        return None
    best = {}
    for item in candidates:
        score = item.get("rerank_score")
        if type(score) not in (float, int) or not math.isfinite(score) or not 0 <= score <= 1:
            return None
        key = item["document_id"]
        best[key] = max(best.get(key, 0), score)
    ranked = sorted(best.items(), key=lambda item: (-item[1], item[0]))
    if len(ranked) < 2:
        return None
    if ranked[0][1] >= floor and ranked[0][1] - ranked[1][1] >= gap:
        return ranked[0][0]
    return None


def baseline(row, catalog, settings):
    candidates = [
        SimpleNamespace(
            **{
                **item,
                "document_id": UUID(item["document_id"]),
                "document": catalog.get(item["document_id"], item["document_id"]),
            }
        )
        for item in row["rerank_result"].get("candidates", [])
    ]
    decision = decide_doc_routing(
        question=row["question"],
        candidates=candidates,
        focus_document_id=None,
        chat_context={},
        t_high=settings.m3_doc_agg_t_high,
        t_low=settings.m3_doc_agg_t_low,
        ratio=settings.m3_doc_agg_ratio,
        min_hits=settings.m3_doc_agg_min_hits,
        max_hits=settings.m3_doc_agg_max_hits,
        stay_score=settings.m3_doc_stay_score,
        switch_gap=settings.m3_doc_switch_gap,
        lock_best_floor=settings.m3_doc_lock_best_floor,
        single_clarify_best_floor=settings.m3_doc_single_clarify_best_floor,
        rerank_degraded=row["rerank_result"].get("status") == "DEGRADED",
    )
    if decision.action == "DELIVER":
        return str(decision.document_id), "AGGREGATION"
    if RagService._match_meta_ack(row["question"]) is not None:
        return None, "META_ACK"
    match = RagService._match_catalog_title(
        row["question"], [{"document_id": key, "title": value} for key, value in catalog.items()]
    )
    if match:
        return str(match["document_id"]), "TITLE_MATCH"
    return None, decision.action


def expected_documents(case, catalog):
    if not case:
        return set()
    expected = case["expected"]
    keys = [normalize(key) for key in expected.get("document_keys", [])]
    forbidden = [normalize(key) for key in expected.get("forbidden_keys", [])]
    return {
        key
        for key, value in catalog.items()
        if any(term in normalize(value) for term in keys)
        and not any(term in normalize(value) for term in forbidden)
    }


def judgement(document_id, case, catalog):
    if not case:
        return "unlabelled"
    expected = case["expected"]
    if expected.get("tolerance") == "NONE":
        return "incorrect"
    keys = [normalize(key) for key in expected.get("document_keys", [])]
    forbidden = [normalize(key) for key in expected.get("forbidden_keys", [])]
    title = normalize(catalog.get(document_id, ""))
    if any(key in title for key in forbidden):
        return "incorrect"
    allowed = expected_documents(case, catalog)
    if not keys:
        return "unlabelled"
    if document_id not in allowed:
        return "incorrect"
    # A broad key or ANY_OF cannot prove that a single document is sufficient.
    if expected.get("tolerance") == "ANY_OF" or len(allowed) != 1:
        return "compatible_ambiguous"
    return "correct_unique"


async def run():
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    if url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise SystemExit("This experiment requires a local database host")
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            rows = [
                dict(row)
                for row in (
                    await connection.execute(
                        text("""
                SELECT t.trace_id,t.request_id,t.question,t.created_at,t.index_id,t.kb_id,
                       t.response_type,t.retrieval_result,t.rerank_result,t.sources,
                       m.sequence_no
                FROM rag_trace t LEFT JOIN message m ON m.id=t.message_id
                WHERE t.engine='RAG' AND t.mode='CHAT' AND t.status='COMPLETED'
                  AND t.parent_trace_id IS NULL
                  AND t.rerank_result->>'provider'='siliconflow'
                ORDER BY t.created_at,t.trace_id
            """)
                    )
                ).mappings()
            ]
            catalogs = {}
            for index_id in {row["index_id"] for row in rows}:
                catalogs[str(index_id)] = dict(
                    (
                        await connection.execute(
                            text("""
                    SELECT d.id::text,COALESCE(NULLIF(d.display_name,''),d.filename)
                    FROM document_source d
                    WHERE d.kb_id=(SELECT kb_id FROM knowledge_index WHERE id=:index_id)
                      AND d.status='STORED'
                    ORDER BY d.created_at
                """),
                            {"index_id": index_id},
                        )
                    ).all()
                )
    finally:
        await engine.dispose()
    cases = [json.loads(line) for line in (ROOT / "cases.jsonl").read_text().splitlines() if line]
    first_cases = {case["id"]: {**case, **(case.get("turns") or [{}])[0]} for case in cases}
    records = []
    exclusions = Counter()
    for row in rows:
        golden = re.search(r"-(rag_[a-z]+_\d+)-1$", row["request_id"] or "")
        case = first_cases.get(golden.group(1)) if golden else None
        if row["sequence_no"] != 2 and case is None:
            exclusions["first_turn_not_proven"] += 1
            continue
        if case and normalize(case["question"]) != normalize(row["question"]):
            exclusions["golden_question_mismatch"] += 1
            continue
        routing = row["retrieval_result"].get("doc_routing", {})
        if (
            routing.get("resolved_from") in {"INPUT_GUARD", "META_ACK"}
            or RagService._match_meta_ack(row["question"]) is not None
        ):
            exclusions["input_or_meta_guard"] += 1
            continue
        if routing.get("phase_before") != "ALIGNING":
            exclusions["not_aligning"] += 1
            continue
        if not row["rerank_result"].get("candidates"):
            exclusions["missing_scores"] += 1
            continue
        catalog = catalogs[str(row["index_id"])]
        candidates = row["rerank_result"]["candidates"]
        if not catalog or any(item["document_id"] not in catalog for item in candidates):
            exclusions["catalog_or_document_missing"] += 1
            continue
        chosen, method = baseline(row, catalog, settings)
        best = {}
        hits = Counter()
        for item in candidates:
            key = item["document_id"]
            hits[key] += 1
            best[key] = max(best.get(key, 0), item.get("rerank_score") or 0)
        ranked = sorted(best, key=lambda key: (-best[key], key))
        expected_ids = expected_documents(case, catalog)
        top_chunk = max(candidates, key=lambda item: item.get("rerank_score") or 0)
        records.append(
            {
                "trace_id": str(row["trace_id"]),
                "date": str(row["created_at"]),
                "question": row["question"],
                "case_id": case["id"] if case else None,
                "category": case["category"] if case else "unlabelled",
                "index_id": str(row["index_id"]),
                "baseline_document": chosen,
                "rerank_model": row["rerank_result"].get("model"),
                "rerank_status": row["rerank_result"].get("status"),
                "candidate_count": len(candidates),
                "expected_document_count": len(expected_ids),
                "expected_in_rerank": bool(expected_ids.intersection(best))
                if expected_ids
                else None,
                "baseline_method": method,
                "recorded_response_type": row["response_type"],
                "top_title": catalog[ranked[0]],
                "top_best": best[ranked[0]],
                "top_hits": hits[ranked[0]],
                "top_evidence": {
                    key: top_chunk.get(key)
                    for key in (
                        "chunk_id",
                        "chunk_type",
                        "suspected_incomplete",
                        "incomplete_reasons",
                    )
                },
                "runner_title": catalog[ranked[1]] if len(ranked) > 1 else None,
                "runner_best": best[ranked[1]] if len(ranked) > 1 else None,
                "baseline_judgement": judgement(chosen, case, catalog) if chosen else None,
                "trials": {
                    f"{floor:.2f}/{gap:.2f}": {
                        "document_id": (
                            selected := single_strong(
                                candidates, row["rerank_result"].get("status"), floor, gap
                            )
                        ),
                        "judgement": judgement(selected, case, catalog) if selected else None,
                    }
                    for floor, gap in [
                        (0.90, 0.20),
                        (0.85, 0.18),
                        (0.80, 0.15),
                        (0.75, 0.15),
                        (0.70, 0.10),
                        (0.65, 0.10),
                        (0.60, 0.10),
                    ]
                }
                if chosen is None
                else {},
            }
        )

    def summarize(items):
        return {
            "count": len(items),
            "baseline_direct": sum(bool(item["baseline_document"]) for item in items),
            "rerank_statuses": dict(Counter(item["rerank_status"] for item in items)),
            "rerank_models": dict(Counter(item["rerank_model"] for item in items)),
            "candidate_counts": dict(Counter(item["candidate_count"] for item in items)),
            "baseline_methods": dict(Counter(item["baseline_method"] for item in items)),
            "baseline_judgements": dict(
                Counter(item["baseline_judgement"] for item in items if item["baseline_document"])
            ),
            "trials": {
                key: dict(
                    Counter(
                        item["trials"][key]["judgement"]
                        for item in items
                        if key in item["trials"] and item["trials"][key]["document_id"]
                    )
                )
                for key in [
                    "0.90/0.20",
                    "0.85/0.18",
                    "0.80/0.15",
                    "0.75/0.15",
                    "0.70/0.10",
                    "0.65/0.10",
                    "0.60/0.10",
                ]
            },
        }

    latest = {normalize(row["question"]): row for row in records}
    return {
        "scope": "Local historical score replay, not fresh model calls or full HTTP A/B",
        "raw_real_completed_rag_traces": len(rows),
        "exclusions": dict(exclusions),
        "date_range": [str(rows[0]["created_at"]), str(rows[-1]["created_at"])] if rows else [],
        "baseline_thresholds": {
            key: value for key, value in settings.model_dump().items() if key.startswith("m3_doc_")
        },
        "all": summarize(records),
        "latest_per_question": summarize(list(latest.values())),
        "records": records,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "records"},
            ensure_ascii=False,
            indent=2,
        )
    )
