from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from io import BytesIO
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.core.resources import AppResources
from app.ingestion.embedding import create_embedding_provider
from app.ingestion.models import EmbeddingInput
from app.m0.fixtures import make_text_image
from app.main import create_app


def _parse_sse(raw: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for frame in raw.replace("\r\n", "\n").split("\n\n"):
        if not frame.strip():
            continue
        event = "message"
        data_lines: list[str] = []
        for line in frame.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:
            events.append((event, json.loads("\n".join(data_lines))))
    return events


async def _seed_fixture(
    resources: AppResources,
    settings: Settings,
    knowledge_id: UUID,
    index_id: UUID,
    question: str,
) -> tuple[UUID, str]:
    document_id = uuid4()
    index_document_id = uuid4()
    element_id = uuid4()
    image_element_id = uuid4()
    image_asset_id = uuid4()
    chunk_id = uuid4()
    image_bytes = make_text_image()
    image_object_key = f"m5-smoke/{image_asset_id}.png"
    await asyncio.to_thread(
        resources.minio.put_object,
        settings.minio_image_bucket,
        image_object_key,
        BytesIO(image_bytes),
        len(image_bytes),
        content_type="image/png",
    )
    provider = create_embedding_provider(settings)
    try:
        vector = (await provider.embed([EmbeddingInput(question)]))[0]
    finally:
        await provider.aclose()
    vector_literal = "[" + ",".join(format(value, ".9g") for value in vector) + "]"

    async with resources.database.begin() as connection:
        embedding_model = await connection.execute(
            text(
                """
                SELECT id, model_name FROM model_config
                WHERE model_type = 'EMBEDDING' AND enabled
                """
            )
        )
        model = embedding_model.mappings().one()
        await connection.execute(
            text(
                """
                INSERT INTO knowledge_base (id, name, description)
                VALUES (:id, :name, 'M3 automated smoke fixture')
                """
            ),
            {"id": knowledge_id, "name": f"m3-smoke-{knowledge_id}"},
        )
        await connection.execute(
            text(
                """
                INSERT INTO knowledge_index (
                    id, kb_id, version, status, embedding_model_id, embedding_model_name,
                    embedding_dimension, document_count, element_count, chunk_count,
                    build_reason, finished_at, activated_at
                ) VALUES (
                    :id, :kb_id, 1, 'ACTIVE', :model_id, :model_name,
                    :dimension, 1, 2, 1, 'INITIAL', now(), now()
                )
                """
            ),
            {
                "id": index_id,
                "kb_id": knowledge_id,
                "model_id": model["id"],
                "model_name": model["model_name"],
                "dimension": settings.embedding_dimension,
            },
        )
        await connection.execute(
            text("UPDATE knowledge_base SET active_index_id = :index_id WHERE id = :kb_id"),
            {"index_id": index_id, "kb_id": knowledge_id},
        )
        await connection.execute(
            text(
                """
                INSERT INTO document_source (
                    id, kb_id, filename, display_name, minio_bucket, minio_object_key,
                    file_hash, file_size, mime_type
                ) VALUES (
                    :id, :kb_id, 'm3-smoke.docx', :display_name, 'rag-documents',
                    :object_key, :file_hash, 1,
                    'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                )
                """
            ),
            {
                "id": document_id,
                "kb_id": knowledge_id,
                "object_key": f"m3-smoke/{document_id}.docx",
                "file_hash": "a" * 64,
                "display_name": f"M3 部署手册 {knowledge_id}",
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO index_document (
                    id, index_id, document_id, source_hash, status, started_at, finished_at
                ) VALUES (:id, :index_id, :document_id, :source_hash, 'READY', now(), now())
                """
            ),
            {
                "id": index_document_id,
                "index_id": index_id,
                "document_id": document_id,
                "source_hash": "a" * 64,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO document_element (
                    id, index_id, index_document_id, document_id, element_type,
                    sequence_no, content, section_path
                ) VALUES (
                    :id, :index_id, :index_document_id, :document_id, 'TEXT', 1,
                    '数据库默认端口为3306。', '["数据库配置"]'::jsonb
                ), (
                    :image_element_id, :index_id, :index_document_id, :document_id,
                    'IMAGE', 2, '图片描述：数据库端口配置截图。\n图片文字：DATABASE PORT: 3306',
                    '["数据库配置"]'::jsonb
                )
                """
            ),
            {
                "id": element_id,
                "index_id": index_id,
                "index_document_id": index_document_id,
                "document_id": document_id,
                "image_element_id": image_element_id,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO image_asset (
                    id, index_id, index_document_id, document_id, element_id,
                    minio_bucket, minio_object_key, file_hash, mime_type, width, height,
                    ocr_text, vision_caption, ocr_status, vision_status,
                    ocr_provider, ocr_model_name, vision_provider, vision_model_name,
                    processed_at, metadata
                ) VALUES (
                    :id, :index_id, :index_document_id, :document_id, :element_id,
                    :bucket, :object_key, :file_hash, 'image/png', 640, 180,
                    'DATABASE PORT: 3306', '数据库端口配置截图。', 'READY', 'READY',
                    'fixture', 'fixture-ocr', 'fixture', 'fixture-vision', now(),
                    '{"pipeline":"m5_smoke"}'::jsonb
                )
                """
            ),
            {
                "id": image_asset_id,
                "index_id": index_id,
                "index_document_id": index_document_id,
                "document_id": document_id,
                "element_id": image_element_id,
                "bucket": settings.minio_image_bucket,
                "object_key": image_object_key,
                "file_hash": hashlib.sha256(image_bytes).hexdigest(),
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO document_chunk (
                    id, kb_id, index_id, index_document_id, document_id, chunk_type,
                    sequence_no, content, search_text, token_count, embedding, section_path
                ) VALUES (
                    :id, :kb_id, :index_id, :index_document_id, :document_id, 'MIXED', 1,
                    '数据库默认端口为3306。\n图片文字：DATABASE PORT: 3306',
                    '数据库默认端口为3306 DATABASE PORT 3306', 20,
                    CAST(:embedding AS vector), '["数据库配置"]'::jsonb
                )
                """
            ),
            {
                "id": chunk_id,
                "kb_id": knowledge_id,
                "index_id": index_id,
                "index_document_id": index_document_id,
                "document_id": document_id,
                "embedding": vector_literal,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO chunk_element (chunk_id, element_id, ordinal)
                VALUES (:chunk_id, :element_id, 1), (:chunk_id, :image_element_id, 2)
                """
            ),
            {
                "chunk_id": chunk_id,
                "element_id": element_id,
                "image_element_id": image_element_id,
            },
        )
    return document_id, image_object_key


async def run(
    embedding_provider: Literal["fake", "siliconflow"] = "fake",
    llm_provider: Literal["fake", "zhipu"] = "fake",
    rerank_provider: Literal["fake", "siliconflow"] = "fake",
) -> dict[str, object]:
    base_settings = get_settings()
    settings = base_settings.model_copy(
        update={
            "m2_embedding_provider": embedding_provider,
            "m3_llm_provider": llm_provider,
            "m5_rerank_provider": rerank_provider,
        }
    )
    resources = AppResources.create(settings)
    knowledge_id = uuid4()
    index_id = uuid4()
    decoy_knowledge_id = uuid4()
    decoy_index_id = uuid4()
    question = "数据库默认端口是多少？"
    image_object_keys: list[str] = []
    try:
        document_id, image_object_key = await _seed_fixture(
            resources, settings, knowledge_id, index_id, question
        )
        image_object_keys.append(image_object_key)
        _, decoy_image_object_key = await _seed_fixture(
            resources,
            settings,
            decoy_knowledge_id,
            decoy_index_id,
            question,
        )
        image_object_keys.append(decoy_image_object_key)
        headers = {"Accept": "text/event-stream"}
        if settings.service_token is not None:
            headers["Authorization"] = f"Bearer {settings.service_token.get_secret_value()}"

        def call_api() -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any], list[Any]]:
            with TestClient(create_app(settings)) as client:
                response = client.post(
                    f"{settings.api_prefix}/chat/stream",
                    headers=headers,
                    json={"knowledge_id": str(knowledge_id), "question": question},
                )
                response.raise_for_status()
                if not response.headers["content-type"].startswith("text/event-stream"):
                    raise RuntimeError("M3 chat did not return text/event-stream")
                events = _parse_sse(response.text)
                trace_payload = next(payload for event, payload in events if event == "trace")
                trace_response = client.get(
                    f"{settings.api_prefix}/traces/{trace_payload['trace_id']}", headers=headers
                )
                trace_response.raise_for_status()
                message_response = client.get(
                    f"{settings.api_prefix}/conversations/"
                    f"{trace_payload['conversation_id']}/messages",
                    headers=headers,
                )
                message_response.raise_for_status()
                return events, trace_response.json(), message_response.json()

        events, trace, messages = await asyncio.to_thread(call_api)
        event_names = [event for event, _ in events]
        if event_names[:2] != ["trace", "source"] or event_names[-1] != "done":
            raise RuntimeError(f"M3 SSE event order invalid: {event_names}")
        if "message" not in event_names:
            raise RuntimeError("M3 SSE stream had no message delta")
        if trace["status"] != "COMPLETED":
            raise RuntimeError("M3 trace was not completed")
        if len(trace["retrieval_result"].get("vector_candidates", [])) != 1:
            raise RuntimeError("M3 vector retrieval did not return the scoped chunk")
        if len(trace["retrieval_result"].get("bm25_candidates", [])) != 1:
            raise RuntimeError("M5 BM25 retrieval did not return the scoped chunk")
        if len(trace["retrieval_result"].get("fusion_candidates", [])) != 1:
            raise RuntimeError("M5 RRF fusion did not deduplicate the scoped chunk")
        if trace["rerank_result"].get("status") != "PASSED":
            raise RuntimeError("M5 rerank did not pass")
        selected_context = trace["selected_context"]
        if len(selected_context) != 1 or selected_context[0]["document_id"] != str(document_id):
            raise RuntimeError("M3 vector retrieval crossed the requested knowledge boundary")
        if len(selected_context[0].get("image_asset_ids", [])) != 1:
            raise RuntimeError("M5 multimodal source did not contain its image asset")
        if [message["role"] for message in messages] != ["USER", "ASSISTANT"]:
            raise RuntimeError("M3 conversation messages were not persisted")
        done = events[-1][1]
        if trace["citation_result"].get("citation_missing") is not False:
            raise RuntimeError("M5 citation validation did not register the real source")
        return {
            "status": "passed",
            "knowledge_id": str(knowledge_id),
            "index_id": str(index_id),
            "trace_id": trace["trace_id"],
            "embedding_provider": embedding_provider,
            "llm_provider": llm_provider,
            "rerank_provider": rerank_provider,
            "vector_candidates": 1,
            "bm25_candidates": 1,
            "fusion_candidates": 1,
            "rerank_status": trace["rerank_result"]["status"],
            "knowledge_scope_verified": True,
            "multimodal_rerank_input_verified": True,
            "sse_events": event_names,
            "answer_chars": len(done["answer"]),
            "validated_sources": len(done["sources"]),
            "citation_missing": trace["citation_result"]["citation_missing"],
            "trace_status": trace["status"],
            "assistant_status": messages[-1]["status"],
        }
    finally:
        async with resources.database.begin() as connection:
            for fixture_knowledge_id in (knowledge_id, decoy_knowledge_id):
                await connection.execute(
                    text("UPDATE knowledge_base SET active_index_id = NULL WHERE id = :id"),
                    {"id": fixture_knowledge_id},
                )
                await connection.execute(
                    text("DELETE FROM knowledge_base WHERE id = :id"),
                    {"id": fixture_knowledge_id},
                )
        for object_key in image_object_keys:
            await asyncio.to_thread(
                resources.minio.remove_object, settings.minio_image_bucket, object_key
            )
        await resources.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the M3 text RAG end-to-end smoke test")
    parser.add_argument("--embedding-provider", choices=("fake", "siliconflow"), default="fake")
    parser.add_argument("--llm-provider", choices=("fake", "zhipu"), default="fake")
    parser.add_argument("--rerank-provider", choices=("fake", "siliconflow"), default="fake")
    args = parser.parse_args()
    report = asyncio.run(run(args.embedding_provider, args.llm_provider, args.rerank_provider))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
