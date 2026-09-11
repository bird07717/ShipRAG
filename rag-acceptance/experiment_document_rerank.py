"""Local read-only corpus experiments using the configured real Rerank provider.

Text-only controlled comparison, not the production multimodal/HTTP pipeline.
Output contains questions, document IDs and scores, never model credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from uuid import UUID

from app.core.config import Settings
from app.ingestion.embedding import SiliconFlowEmbeddingProvider
from app.ingestion.models import EmbeddingInput
from app.rag.models import ModelSnapshot, RerankDocument
from app.rag.repository import RagRepository
from app.rag.rerank import RerankError, SiliconFlowRerankProvider
from app.rag.retrieval import merge_expanded_candidates, reciprocal_rank_fusion
from app.rag.synonyms import bm25_expansion_terms
from app.services.rag import _input_guard_reason
from experiment_single_strong import (
    baseline,
    expected_documents,
    judgement,
    normalize,
    single_strong,
)
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parent


def pack_documents(candidates, chunks, catalog, *, max_chunks=3, max_chars=2400):
    """Group evidence in supplied relevance order; no labels or Support data."""
    groups = {}
    for item in candidates:
        group = groups.setdefault(item["document_id"], [])
        body = chunks[item["chunk_id"]]["search_text"].strip()
        if body and body not in group and len(group) < max_chunks:
            group.append(body)
    return [
        {
            "document_id": key,
            "text": f"文档标题：{catalog[key]}\n" + "\n\n".join(values)[:max_chars],
        }
        for key, values in groups.items()
    ]


def diversify(candidates, *, limit=5, per_document=1):
    selected = []
    counts = Counter()
    for item in candidates:
        key = item["document_id"]
        if counts[key] < per_document:
            selected.append(item)
            counts[key] += 1
        if len(selected) == limit:
            break
    return selected


def classify_stage(expected, stages):
    if not expected:
        return "NO_UNIQUE_TARGET"
    present = {
        key: any(item["document_id"] in expected for item in values)
        for key, values in stages.items()
    }
    if present["rerank"]:
        return "PRESENT"
    if present["fusion"]:
        return "REMOVED_FROM_RERANK_TOP5"
    if not present["vector"] and not present["bm25"]:
        return "ABSENT_FROM_RECORDED_RECALL"
    if not present["fusion"]:
        return "REMOVED_BEFORE_RERANK"
    if not present["rerank"]:
        return "REMOVED_FROM_RERANK_TOP5"
    return "PRESENT"


def strong_conflict(selected, candidates):
    """Shadow veto only: never substitute the other document automatically."""
    if not selected or not candidates:
        return False
    best = {}
    for item in candidates:
        score = item.get("rerank_score")
        if type(score) not in (float, int) or not 0 <= score <= 1:
            return False
        key = item["document_id"]
        best[key] = max(best.get(key, 0), score)
    strongest = max(best, key=best.get)
    return (
        strongest != selected
        and selected in best
        and best[strongest] >= 0.85
        and best[strongest] - best[selected] >= 0.10
    )


def ranking_conflict(selected, candidates):
    """Conservative aggregate-delivery veto, not a replacement selector."""
    if not selected or not candidates:
        return False
    best = {}
    for item in candidates:
        score = item.get("rerank_score")
        if type(score) not in (float, int) or not 0 <= score <= 1:
            return False
        key = item["document_id"]
        best[key] = max(best.get(key, 0), score)
    return selected in best and max(best.values()) > best[selected] + 1e-6


async def run(args):
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    if url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise SystemExit("Local database required")
    engine = create_async_engine(
        url, connect_args={"server_settings": {"default_transaction_read_only": "on"}}
    )
    repository = RagRepository(engine, settings)
    source = json.loads(args.source.read_text())
    latest = list({normalize(row["question"]): row for row in source["records"]}.values())
    if len({row["index_id"] for row in latest}) != 1:
        raise SystemExit("Use one index per experiment")
    cases = {
        case["id"]: {**case, **(case.get("turns") or [{}])[0]}
        for case in map(json.loads, (ROOT / "cases.jsonl").read_text().splitlines())
    }
    try:
        async with engine.connect() as connection:
            rows = [
                dict(row)
                for row in (
                    await connection.execute(
                        text(
                            "SELECT trace_id,question,index_id,kb_id,"
                            "retrieval_result,rerank_result "
                            "FROM rag_trace WHERE trace_id=ANY(:ids)"
                        ),
                        {"ids": [UUID(row["trace_id"]) for row in latest]},
                    )
                ).mappings()
            ]
            traces = {str(row["trace_id"]): row for row in rows}
            catalog = dict(
                (
                    await connection.execute(
                        text(
                            "SELECT id::text,display_name FROM document_source "
                            "WHERE status='STORED' AND kb_id=:kb_id ORDER BY created_at"
                        ),
                        {"kb_id": rows[0]["kb_id"]},
                    )
                ).all()
            )
            chunk_rows = [
                dict(row)
                for row in (
                    await connection.execute(
                        text(
                            "SELECT id::text,document_id::text,index_id::text,"
                            "content,search_text,sequence_no "
                            "FROM document_chunk WHERE index_id=:index_id ORDER BY sequence_no,id"
                        ),
                        {"index_id": rows[0]["index_id"]},
                    )
                ).mappings()
            ]
            chunks = {row["id"]: row for row in chunk_rows}
            models = {
                row["model_type"]: dict(row)
                for row in (
                    await connection.execute(
                        text(
                            "SELECT * FROM model_config WHERE enabled "
                            "AND model_type IN ('RERANK','EMBEDDING')"
                        )
                    )
                ).mappings()
            }

        def snapshot(kind):
            row = models[kind]
            return ModelSnapshot(
                provider=row["provider"],
                model_name=row["model_name"],
                parameters=row["parameters"],
                base_url=row["base_url"],
                api_key=repository._decrypt_api_key(row),
            )

        snap = snapshot("RERANK")
        if snap.provider != "siliconflow":
            raise SystemExit(
                "This experiment is calibrated only for the configured SiliconFlow provider"
            )
        provider = SiliconFlowRerankProvider(settings, snap)
        cache_path = args.output.with_suffix(".cache.json")
        cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
        calls = 0

        async def rank(question, documents):
            nonlocal calls
            digest = hashlib.sha256(
                json.dumps(
                    [snap.provider, snap.model_name, snap.base_url, question, documents],
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            if digest not in cache:
                started = time.perf_counter()
                async with asyncio.timeout(45):
                    outcome = await provider.rerank(
                        question,
                        [RerankDocument(item["text"]) for item in documents],
                        len(documents),
                    )
                if len(outcome.items) != len(documents):
                    raise RuntimeError("Incomplete score set")
                cache[digest] = {
                    "scores": [
                        {
                            **{
                                key: value
                                for key, value in documents[item.index].items()
                                if key != "text"
                            },
                            "rerank_score": item.score,
                        }
                        for item in outcome.items
                    ],
                    "elapsed_ms": round((time.perf_counter() - started) * 1000),
                }
                calls += 1
                cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n")
            return cache[digest]

        def assess(ranked, case):
            candidates = ranked["scores"]
            selected = single_strong(candidates, "PASSED", 0.85, 0.18)
            return {
                **ranked,
                "top_judgement": judgement(candidates[0]["document_id"], case, catalog),
                "selected": selected,
                "selected_judgement": judgement(selected, case, catalog) if selected else None,
            }

        if args.heldout:
            heldout = json.loads(args.heldout.read_text())
            golden_questions = {normalize(value["question"]) for value in cases.values()}
            assert all(normalize(value["question"]) not in golden_questions for value in heldout)
            cases.update({value["id"]: value for value in heldout})
            seed = rows[0]
            latest = [
                {
                    "trace_id": value["id"],
                    "case_id": value["id"],
                    "category": value["category"],
                    "baseline_method": "NOT_REPLAYED",
                }
                for value in heldout
            ]
            traces = {
                value["id"]: {
                    "question": value["question"],
                    "kb_id": seed["kb_id"],
                    "index_id": seed["index_id"],
                }
                for value in heldout
            }
        heldout_embedder = SiliconFlowEmbeddingProvider(settings, snapshot=snapshot("EMBEDDING"))
        results = []
        try:
            for number, item in enumerate(latest, 1):
                row = traces[item["trace_id"]]
                question = row["question"]
                case = cases.get(item["case_id"])
                if args.heldout:
                    if _input_guard_reason(question):
                        results.append(
                            {
                                "question": question,
                                "case_id": item["case_id"],
                                "category": item["category"],
                                "input_guard": True,
                            }
                        )
                        continue
                    vector = await heldout_embedder.embed([EmbeddingInput(question)])
                    common = {"knowledge_id": row["kb_id"], "index_id": row["index_id"]}
                    vc = await repository.vector_search(**common, embedding=vector[0], limit=5)
                    bc = await repository.bm25_search(**common, query=question, limit=5)
                    extras = []
                    for term in bm25_expansion_terms(question):
                        extras.extend(await repository.bm25_search(**common, query=term, limit=3))
                    fc = reciprocal_rank_fusion(vc, bc, rrf_k=60, limit=8)
                    seen = {value.chunk_id for value in fc}
                    fc.extend(
                        [
                            value
                            for value in merge_expanded_candidates([], extras)
                            if value.chunk_id not in seen
                        ][:3]
                    )
                    row["retrieval_result"] = {
                        "vector_candidates": [value.trace_dict() for value in vc],
                        "bm25_candidates": [value.trace_dict() for value in bc],
                        "fusion_candidates": [value.trace_dict() for value in fc],
                    }
                    row["rerank_result"] = {"candidates": []}
                expected = expected_documents(case, catalog)
                old = row["retrieval_result"]
                fusion = old["fusion_candidates"]
                if any(value["chunk_id"] not in chunks for value in fusion):
                    raise RuntimeError("Historical chunk missing; cannot make a paired comparison")
                chunk_inputs = [
                    {
                        "document_id": value["document_id"],
                        "chunk_id": value["chunk_id"],
                        "text": (
                            f"文档标题：{catalog[value['document_id']]}\n"
                            + chunks[value["chunk_id"]]["search_text"]
                        ),
                    }
                    for value in fusion
                ]
                chunk_rank = await rank(question, chunk_inputs)
                ranked = chunk_rank["scores"]
                five = ranked[:5]
                selected, method = baseline(
                    {
                        "question": question,
                        "rerank_result": {"status": "PASSED", "candidates": five},
                    },
                    catalog,
                    settings,
                )
                variants = {
                    "chunk_single": assess({**chunk_rank, "scores": five}, case),
                    "diverse5_single": assess({**chunk_rank, "scores": diversify(ranked)}, case),
                    "doc_top5": assess(
                        await rank(question, pack_documents(five, chunks, catalog)), case
                    ),
                    "doc_fusion": assess(
                        await rank(question, pack_documents(ranked, chunks, catalog)), case
                    ),
                }
                result = {
                    "question": question,
                    "case_id": item["case_id"],
                    "category": item["category"],
                    "trace_id": item["trace_id"],
                    "expected_ids": sorted(expected),
                    "historical_baseline": item["baseline_method"],
                    "fresh_text_baseline": method,
                    "fresh_baseline_document": selected,
                    "fresh_baseline_judgement": judgement(selected, case, catalog)
                    if selected
                    else None,
                    "strong_conflict_veto": method == "AGGREGATION"
                    and strong_conflict(selected, five),
                    "historical_stage": classify_stage(
                        expected,
                        {
                            "vector": old["vector_candidates"],
                            "bm25": old["bm25_candidates"],
                            "fusion": fusion,
                            "rerank": row["rerank_result"]["candidates"],
                        },
                    )
                    if not args.heldout
                    else None,
                    "fresh_candidate_stage": classify_stage(
                        expected,
                        {
                            "vector": old["vector_candidates"],
                            "bm25": old["bm25_candidates"],
                            "fusion": fusion,
                            "rerank": five,
                        },
                    ),
                    "variants": variants,
                }
                results.append(result)
                print(
                    json.dumps(
                        {
                            "progress": f"{number}/{len(latest)}",
                            "question": question,
                            "baseline": method,
                            "doc_selected": variants["doc_fusion"]["selected_judgement"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                args.output.write_text(
                    json.dumps(
                        {
                            "model": snap.model_name,
                            "records": results,
                            "catalog": catalog,
                            "new_rerank_calls": calls,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n"
                )

            diagnostics = []
            embedder = SiliconFlowEmbeddingProvider(settings, snapshot=snapshot("EMBEDDING"))
            try:
                missing = [
                    item
                    for item in results
                    if not args.heldout
                    and item["expected_ids"]
                    and item["historical_stage"] != "PRESENT"
                ]
                for item in missing:
                    row = traces[item["trace_id"]]
                    expected = set(item["expected_ids"])
                    embedding = (await embedder.embed([EmbeddingInput(item["question"])]))[0]
                    vector = await repository.vector_search(
                        knowledge_id=row["kb_id"],
                        index_id=row["index_id"],
                        embedding=embedding,
                        limit=100,
                    )
                    lexical = await repository.bm25_search(
                        knowledge_id=row["kb_id"],
                        index_id=row["index_id"],
                        query=item["question"],
                        limit=100,
                    )
                    corpus = [
                        {"document_id": value["document_id"], "chunk_id": value["id"]}
                        for value in chunk_rows
                        if value["index_id"] == str(row["index_id"])
                    ]
                    doc_rank = await rank(item["question"], pack_documents(corpus, chunks, catalog))
                    diagnostics.append(
                        {
                            "question": item["question"],
                            "vector_target_rank": next(
                                (
                                    n
                                    for n, v in enumerate(vector, 1)
                                    if str(v.document_id) in expected
                                ),
                                None,
                            ),
                            "bm25_target_rank": next(
                                (
                                    n
                                    for n, v in enumerate(lexical, 1)
                                    if str(v.document_id) in expected
                                ),
                                None,
                            ),
                            "catalog_rerank": assess(doc_rank, cases.get(item["case_id"])),
                        }
                    )
            finally:
                await embedder.aclose()
            output = {
                "scope": "fresh real-model text-only rerank; local read-only data; no HTTP or LLM",
                "model": snap.model_name,
                "records": results,
                "diagnostics": diagnostics,
                "catalog": catalog,
                "new_rerank_calls": calls,
                "cached_rerank_calls": len(cache),
                "heldout": bool(args.heldout),
            }
            args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
        finally:
            await heldout_embedder.aclose()
            await provider.aclose()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=ROOT / "reports/single-strong-local-20260911.json"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "reports/document-rerank-local-20260911.json"
    )
    parser.add_argument("--heldout", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(run(args))
    except RerankError as error:
        raise SystemExit(f"Rerank stopped: {error.code} {error.detail or ''}") from None
