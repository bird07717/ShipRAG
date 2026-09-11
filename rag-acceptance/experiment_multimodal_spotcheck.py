"""Read-only native multimodal rerank spot checks; never creates chat state."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from app.core.config import Settings
from app.ingestion.embedding import SiliconFlowEmbeddingProvider
from app.ingestion.models import EmbeddingInput
from app.rag.models import ModelSnapshot
from app.rag.repository import RagRepository
from app.rag.rerank import SiliconFlowRerankProvider
from app.rag.retrieval import apply_rerank, merge_expanded_candidates, reciprocal_rank_fusion
from app.rag.synonyms import bm25_expansion_terms
from app.services.rag import RagService
from experiment_document_rerank import ranking_conflict, strong_conflict
from experiment_single_strong import baseline
from minio import Minio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


async def run():
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    assert url.host in {"localhost", "127.0.0.1", "::1"}
    engine = create_async_engine(
        url, connect_args={"server_settings": {"default_transaction_read_only": "on"}}
    )
    repository = RagRepository(engine, settings)
    try:
        async with engine.connect() as connection:
            kb = (
                await connection.execute(
                    text(
                        "SELECT id,active_index_id FROM knowledge_base "
                        "WHERE active_index_id IS NOT NULL"
                    )
                )
            ).one()
            catalog = dict(
                (
                    await connection.execute(
                        text(
                            "SELECT id::text,display_name FROM document_source "
                            "WHERE kb_id=:id AND status='STORED' ORDER BY created_at"
                        ),
                        {"id": kb.id},
                    )
                ).all()
            )
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

        embedding = SiliconFlowEmbeddingProvider(settings, snapshot=snapshot("EMBEDDING"))
        rerank = SiliconFlowRerankProvider(settings, snapshot("RERANK"))
        minio = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key.get_secret_value(),
            secret_key=settings.minio_secret_key.get_secret_value(),
            secure=settings.minio_secure,
        )
        service = RagService(repository, settings, minio)
        results = []
        try:
            for question in [
                "PDC的接线盒需要检查，具体步骤是什么？",
                "LRU怎么升级版本",
                "MU报警，需要怎么处理？",
            ]:
                started = time.perf_counter()
                common = {"knowledge_id": kb.id, "index_id": kb.active_index_id}
                vector = (await embedding.embed([EmbeddingInput(question)]))[0]
                vc = await repository.vector_search(**common, embedding=vector, limit=5)
                bc = await repository.bm25_search(**common, query=question, limit=5)
                extras = []
                for term in bm25_expansion_terms(question):
                    extras.extend(await repository.bm25_search(**common, query=term, limit=3))
                fc = reciprocal_rank_fusion(vc, bc, rrf_k=60, limit=8)
                seen = {item.chunk_id for item in fc}
                fc.extend(
                    [
                        item
                        for item in merge_expanded_candidates([], extras)
                        if item.chunk_id not in seen
                    ][:3]
                )
                documents = await service._build_rerank_documents(kb.active_index_id, fc)
                outcome = await rerank.rerank(question, documents, 5)
                candidates = [item.trace_dict() for item in apply_rerank(fc, outcome.items)]
                selected, method = baseline(
                    {
                        "question": question,
                        "rerank_result": {"status": "PASSED", "candidates": candidates},
                    },
                    catalog,
                    settings,
                )
                result = {
                    "question": question,
                    "method": method,
                    "selected_title": catalog.get(selected),
                    "selected_id": selected,
                    "candidates": candidates,
                    "images_used": sum(item.image_bytes is not None for item in documents),
                    "strong_conflict_veto": method == "AGGREGATION"
                    and strong_conflict(selected, candidates),
                    "ranking_conflict_veto": method == "AGGREGATION"
                    and ranking_conflict(selected, candidates),
                    "elapsed_ms": round((time.perf_counter() - started) * 1000),
                }
                results.append(result)
                print(
                    json.dumps(
                        {key: value for key, value in result.items() if key != "candidates"},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        finally:
            await embedding.aclose()
            await rerank.aclose()
        output = Path(__file__).parent / "reports/multimodal-spotcheck-20260911.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "records": results,
                    "catalog": catalog,
                    "scope": "native retrieval and multimodal rerank, no chat state or LLM",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
