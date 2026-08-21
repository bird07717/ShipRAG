from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from minio import Minio
from minio.commonconfig import CopySource
from PIL import Image
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.ingestion.chunker import build_chunks, build_parent_chunks
from app.ingestion.embedding import EmbeddingProvider, create_embedding_provider
from app.ingestion.image_understanding import (
    ImageUnderstandingError,
    ImageUnderstandingProvider,
    create_ocr_provider,
    create_vision_provider,
)
from app.ingestion.models import EmbeddingInput, ParsedElement
from app.ingestion.parser import parse_docx
from app.ingestion.repository import BuildReference, IngestionRepository


@dataclass(frozen=True, slots=True)
class IndexBuildResult:
    index_id: UUID
    document_count: int
    element_count: int
    chunk_count: int
    followup_build: BuildReference | None


class IndexPipeline:
    def __init__(
        self,
        engine: AsyncEngine,
        minio: Minio,
        settings: Settings,
        embedding_provider: EmbeddingProvider | None = None,
        ocr_provider: ImageUnderstandingProvider | None = None,
        vision_provider: ImageUnderstandingProvider | None = None,
    ) -> None:
        self.engine = engine
        self.minio = minio
        self.settings = settings
        self.embedding_provider = embedding_provider or create_embedding_provider(settings)
        self.ocr_provider = ocr_provider or create_ocr_provider(settings)
        self.vision_provider = vision_provider or create_vision_provider(settings)
        self.repository = IngestionRepository(engine, settings)

    async def run(self, index_id: UUID, task_id: UUID) -> IndexBuildResult:
        index = await self._load_index(index_id)
        kb_id = UUID(str(index["kb_id"]))
        documents = await self._load_documents(index_id)
        await self._update_task(task_id, "RUNNING", "PARSING", 1, increment_attempt=True)
        prev_index_id = await self.repository.get_active_index_id(kb_id)
        try:
            for position, document in enumerate(documents, start=1):
                doc_metadata = document.get("metadata") or {}
                if doc_metadata.get("build_mode") == "REUSE" and prev_index_id is not None:
                    await self._reuse_document(
                        index, document, prev_index_id, task_id, position, len(documents)
                    )
                else:
                    await self._process_document(index, document, task_id, position, len(documents))
            counts = await self._finalize_index(index_id, kb_id, task_id)
            followup = await self.repository.create_followup_build_if_needed(kb_id)
            return IndexBuildResult(
                index_id=index_id,
                document_count=counts[0],
                element_count=counts[1],
                chunk_count=counts[2],
                followup_build=followup,
            )
        except Exception as exc:
            await self._mark_failed(index_id, task_id, exc)
            raise
        finally:
            await asyncio.gather(
                self.embedding_provider.aclose(),
                self.ocr_provider.aclose(),
                self.vision_provider.aclose(),
            )

    async def _load_index(self, index_id: UUID) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text("SELECT * FROM knowledge_index WHERE id = :id"), {"id": index_id}
            )
            row = result.mappings().one_or_none()
        if row is None:
            raise ValueError("索引不存在")
        if row["status"] != "BUILDING":
            raise ValueError("仅 BUILDING 索引可以执行摄取")
        return dict(row)

    async def _load_documents(self, index_id: UUID) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT idoc.id AS index_document_id, idoc.index_id, idoc.document_id,
                           idoc.source_hash, idoc.metadata, d.kb_id, d.minio_bucket,
                           d.minio_object_key, d.filename
                    FROM index_document idoc
                    JOIN document_source d ON d.id = idoc.document_id
                    WHERE idoc.index_id = :index_id
                    ORDER BY idoc.created_at, idoc.id
                    """
                ),
                {"index_id": index_id},
            )
            documents = [dict(row) for row in result.mappings()]
        if not documents:
            raise ValueError("索引快照没有文档")
        return documents

    async def _download_object(self, bucket: str, object_key: str) -> bytes:
        def download() -> bytes:
            response = self.minio.get_object(bucket, object_key)
            try:
                return bytes(response.read())
            finally:
                response.close()
                response.release_conn()

        return await asyncio.to_thread(download)

    async def _process_document(
        self,
        index: dict[str, Any],
        document: dict[str, Any],
        task_id: UUID,
        position: int,
        total: int,
    ) -> None:
        index_id = UUID(str(index["id"]))
        index_document_id = UUID(str(document["index_document_id"]))
        document_id = UUID(str(document["document_id"]))
        progress = max(1, round((position - 1) / total * 90))
        task_metadata = {
            "current_document": str(document["filename"]),
            "current_document_position": position,
            "total_documents": total,
        }
        await self._update_task(task_id, "RUNNING", "PARSING", progress, metadata=task_metadata)
        await self._update_document_status(index_document_id, "PARSING", started=True)
        data = await self._download_object(document["minio_bucket"], document["minio_object_key"])
        if hashlib.sha256(data).hexdigest() != document["source_hash"]:
            raise ValueError("源文档哈希与索引快照不一致")
        parsed = await asyncio.to_thread(parse_docx, data)

        image_count = sum(element.element_type == "IMAGE" for element in parsed.elements)
        await self._update_task(
            task_id,
            "RUNNING",
            "PROCESSING_IMAGES",
            progress,
            metadata={**task_metadata, "image_count": image_count},
        )
        await self._update_document_status(index_document_id, "PROCESSING_IMAGES")
        element_ids = [
            uuid5(NAMESPACE_URL, f"{index_document_id}:element:{sequence_no}")
            for sequence_no in range(1, len(parsed.elements) + 1)
        ]
        image_records = await self._store_images(
            index_id,
            index_document_id,
            document_id,
            parsed.elements,
            element_ids,
        )
        await self._enrich_images(parsed.elements, image_records)

        await self._update_task(task_id, "RUNNING", "CHUNKING", progress, metadata=task_metadata)
        await self._update_document_status(index_document_id, "CHUNKING")
        chunks = build_chunks(
            parsed.elements,
            target_chars=self.settings.m2_chunk_target_chars,
            max_chars=self.settings.m2_chunk_max_chars,
            overlap_paragraphs=self.settings.m2_chunk_overlap_paragraphs,
        )
        parents = build_parent_chunks(
            parsed.elements,
            chunks,
            max_chars=self.settings.m2_parent_chunk_max_chars,
        )

        await self._update_task(task_id, "RUNNING", "EMBEDDING", progress, metadata=task_metadata)
        await self._update_document_status(index_document_id, "EMBEDDING")
        vectors = await self.embedding_provider.embed(
            [
                EmbeddingInput(
                    text=chunk.content,
                    image_bytes=chunk.image_bytes,
                    image_mime_type=chunk.image_mime_type,
                )
                for chunk in chunks
            ]
        )
        if len(vectors) != len(chunks):
            raise ValueError("Embedding 数量与 Chunk 数量不一致")
        if any(len(vector) != self.settings.embedding_dimension for vector in vectors):
            raise ValueError("Embedding 维度不一致")

        await self._persist_document(
            index=index,
            document=document,
            elements=parsed.elements,
            element_ids=element_ids,
            image_records=image_records,
            chunks=chunks,
            parents=parents,
            vectors=vectors,
            mammoth_warnings=parsed.mammoth_warnings,
        )
        completed_progress = min(95, round(position / total * 90))
        await self._update_task(
            task_id,
            "RUNNING",
            "EMBEDDING",
            completed_progress,
            metadata=task_metadata,
        )

    async def _reuse_document(
        self,
        index: dict[str, Any],
        document: dict[str, Any],
        prev_index_id: UUID,
        task_id: UUID,
        position: int,
        total: int,
    ) -> None:
        index_id = UUID(str(index["id"]))
        index_document_id = UUID(str(document["index_document_id"]))
        document_id = UUID(str(document["document_id"]))
        kb_id = UUID(str(document["kb_id"]))
        progress = max(1, round((position - 1) / total * 90))
        task_metadata = {
            "current_document": str(document["filename"]),
            "current_document_position": position,
            "total_documents": total,
            "reused": True,
        }
        await self._update_task(task_id, "RUNNING", "PARSING", progress, metadata=task_metadata)
        await self._update_document_status(index_document_id, "PARSING", started=True)

        # 查询上一版索引中同一文档的 index_document_id
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id FROM index_document
                    WHERE index_id = :index_id AND document_id = :document_id
                    """
                ),
                {"index_id": prev_index_id, "document_id": document_id},
            )
            old_index_document_id = result.scalar_one()

        # 复制 MinIO 图片对象（路径中的 index_id 替换为新索引 ID）
        async with self.engine.connect() as connection:
            image_result = await connection.execute(
                text("SELECT minio_object_key FROM image_asset WHERE index_document_id = :id"),
                {"id": old_index_document_id},
            )
            old_keys = [row["minio_object_key"] for row in image_result.mappings()]
        prev_index_str = str(prev_index_id)
        new_index_str = str(index_id)
        for old_key in old_keys:
            new_key = old_key.replace(prev_index_str, new_index_str, 1)
            await asyncio.to_thread(
                self.minio.copy_object,
                self.settings.minio_image_bucket,
                new_key,
                CopySource(self.settings.minio_image_bucket, old_key),
            )

        # SQL 批量复制解析结果
        params = {
            "new_index_id": index_id,
            "new_index_document_id": index_document_id,
            "old_index_document_id": old_index_document_id,
            "kb_id": kb_id,
            "prev_index_id_str": prev_index_str,
            "new_index_id_str": new_index_str,
        }
        async with self.engine.begin() as connection:
            # 1. 复制 document_element
            await connection.execute(
                text(
                    """
                    INSERT INTO document_element (
                        id, index_id, index_document_id, document_id, element_type,
                        sequence_no, content, section_path, metadata, created_at
                    )
                    SELECT gen_random_uuid(), :new_index_id, :new_index_document_id,
                           document_id, element_type, sequence_no, content,
                           section_path, metadata, now()
                    FROM document_element
                    WHERE index_document_id = :old_index_document_id
                    """
                ),
                params,
            )

            # 2. 复制 image_asset（通过 sequence_no 关联新旧 element_id）
            await connection.execute(
                text(
                    """
                    INSERT INTO image_asset (
                        id, index_id, index_document_id, document_id, element_id,
                        minio_bucket, minio_object_key, file_hash, mime_type, width, height,
                        ocr_text, vision_caption, ocr_status, vision_status,
                        ocr_provider, ocr_model_name, ocr_error_code,
                        vision_provider, vision_model_name, vision_error_code,
                        processed_at, metadata, created_at
                    )
                    SELECT gen_random_uuid(), :new_index_id, :new_index_document_id,
                           ia.document_id, ne.id,
                           ia.minio_bucket,
                           REPLACE(ia.minio_object_key, :prev_index_id_str, :new_index_id_str),
                           ia.file_hash, ia.mime_type, ia.width, ia.height,
                           ia.ocr_text, ia.vision_caption, ia.ocr_status, ia.vision_status,
                           ia.ocr_provider, ia.ocr_model_name, ia.ocr_error_code,
                           ia.vision_provider, ia.vision_model_name, ia.vision_error_code,
                           ia.processed_at, ia.metadata, now()
                    FROM image_asset ia
                    JOIN document_element oe ON oe.id = ia.element_id
                    JOIN document_element ne ON ne.index_document_id = :new_index_document_id
                         AND ne.sequence_no = oe.sequence_no
                    WHERE ia.index_document_id = :old_index_document_id
                    """
                ),
                params,
            )

            # 3. 复制 document_parent_chunk
            await connection.execute(
                text(
                    """
                    INSERT INTO document_parent_chunk (
                        id, kb_id, index_id, index_document_id, document_id, parent_type,
                        sequence_no, content, token_count, section_path, metadata, created_at
                    )
                    SELECT gen_random_uuid(), :kb_id, :new_index_id, :new_index_document_id,
                           document_id, parent_type, sequence_no, content, token_count,
                           section_path, metadata, now()
                    FROM document_parent_chunk
                    WHERE index_document_id = :old_index_document_id
                    """
                ),
                params,
            )

            # 4. 复制 parent_chunk_element（通过 sequence_no 关联）
            await connection.execute(
                text(
                    """
                    INSERT INTO parent_chunk_element (parent_id, element_id, ordinal)
                    SELECT np.id, ne.id, pce.ordinal
                    FROM parent_chunk_element pce
                    JOIN document_parent_chunk op ON op.id = pce.parent_id
                    JOIN document_element oe ON oe.id = pce.element_id
                    JOIN document_parent_chunk np ON np.index_document_id = :new_index_document_id
                         AND np.sequence_no = op.sequence_no
                    JOIN document_element ne ON ne.index_document_id = :new_index_document_id
                         AND ne.sequence_no = oe.sequence_no
                    WHERE op.index_document_id = :old_index_document_id
                    """
                ),
                params,
            )

            # 5. 复制 document_chunk（含 embedding 向量）
            await connection.execute(
                text(
                    """
                    INSERT INTO document_chunk (
                        id, kb_id, index_id, index_document_id, document_id, chunk_type,
                        sequence_no, content, search_text, token_count, embedding,
                        section_path, metadata, parent_id, previous_chunk_id, next_chunk_id,
                        suspected_incomplete, incomplete_reasons, is_procedural, created_at
                    )
                    SELECT gen_random_uuid(), :kb_id, :new_index_id, :new_index_document_id,
                           dc.document_id, dc.chunk_type, dc.sequence_no, dc.content,
                           dc.search_text, dc.token_count, dc.embedding,
                           dc.section_path, dc.metadata,
                           np.id, NULL, NULL,
                           dc.suspected_incomplete, dc.incomplete_reasons,
                           dc.is_procedural, now()
                    FROM document_chunk dc
                    LEFT JOIN document_parent_chunk op ON op.id = dc.parent_id
                    LEFT JOIN document_parent_chunk np
                         ON np.index_document_id = :new_index_document_id
                         AND np.sequence_no = op.sequence_no
                    WHERE dc.index_document_id = :old_index_document_id
                    """
                ),
                params,
            )

            # 6. 复制 chunk_element（通过 sequence_no 关联）
            await connection.execute(
                text(
                    """
                    INSERT INTO chunk_element (chunk_id, element_id, ordinal)
                    SELECT nc.id, ne.id, ce.ordinal
                    FROM chunk_element ce
                    JOIN document_chunk oc ON oc.id = ce.chunk_id
                    JOIN document_element oe ON oe.id = ce.element_id
                    JOIN document_chunk nc ON nc.index_document_id = :new_index_document_id
                         AND nc.sequence_no = oc.sequence_no
                    JOIN document_element ne ON ne.index_document_id = :new_index_document_id
                         AND ne.sequence_no = oe.sequence_no
                    WHERE oc.index_document_id = :old_index_document_id
                    """
                ),
                params,
            )

            # 7. 更新 chunk 邻接关系
            await connection.execute(
                text(
                    """
                    UPDATE document_chunk AS nc
                    SET previous_chunk_id = prev_nc.id,
                        next_chunk_id = next_nc.id
                    FROM document_chunk AS oc
                    LEFT JOIN document_chunk AS prev_oc ON prev_oc.id = oc.previous_chunk_id
                    LEFT JOIN document_chunk AS prev_nc
                         ON prev_nc.index_document_id = :new_index_document_id
                         AND prev_nc.sequence_no = prev_oc.sequence_no
                    LEFT JOIN document_chunk AS next_oc ON next_oc.id = oc.next_chunk_id
                    LEFT JOIN document_chunk AS next_nc
                         ON next_nc.index_document_id = :new_index_document_id
                         AND next_nc.sequence_no = next_oc.sequence_no
                    WHERE oc.index_document_id = :old_index_document_id
                      AND nc.index_document_id = :new_index_document_id
                      AND nc.sequence_no = oc.sequence_no
                      AND (oc.previous_chunk_id IS NOT NULL
                           OR oc.next_chunk_id IS NOT NULL)
                    """
                ),
                params,
            )

            # 8. 更新 index_document 状态为 READY
            await connection.execute(
                text(
                    """
                    UPDATE index_document
                    SET status = 'READY', finished_at = now(), error_code = NULL,
                        error_message = NULL,
                        metadata = CAST(:metadata AS jsonb)
                    WHERE id = :id
                    """
                ),
                {
                    "id": index_document_id,
                    "metadata": json.dumps(
                        {"build_mode": "REUSE", "reused_from": str(old_index_document_id)}
                    ),
                },
            )

        completed_progress = min(95, round(position / total * 90))
        await self._update_task(
            task_id,
            "RUNNING",
            "EMBEDDING",
            completed_progress,
            metadata=task_metadata,
        )

    async def _store_images(
        self,
        index_id: UUID,
        index_document_id: UUID,
        document_id: UUID,
        elements: list[ParsedElement],
        element_ids: list[UUID],
    ) -> dict[int, dict[str, Any]]:
        records: dict[int, dict[str, Any]] = {}
        for index, element in enumerate(elements):
            if element.element_type != "IMAGE" or element.image_bytes is None:
                continue
            extension = self._image_extension(element.image_mime_type)
            object_key = (
                f"indexes/{index_id}/documents/{document_id}/images/"
                f"{element_ids[index]}.{extension}"
            )
            image_bytes = element.image_bytes
            await asyncio.to_thread(
                self.minio.put_object,
                self.settings.minio_image_bucket,
                object_key,
                BytesIO(image_bytes),
                len(image_bytes),
                content_type=element.image_mime_type,
            )
            width, height = await asyncio.to_thread(self._image_size, image_bytes)
            records[index] = {
                "id": uuid5(NAMESPACE_URL, f"{index_document_id}:image:{index + 1}"),
                "object_key": object_key,
                "file_hash": hashlib.sha256(image_bytes).hexdigest(),
                "mime_type": element.image_mime_type or "application/octet-stream",
                "width": width,
                "height": height,
            }
        return records

    async def _enrich_images(
        self,
        elements: list[ParsedElement],
        image_records: dict[int, dict[str, Any]],
    ) -> None:
        semaphore = asyncio.Semaphore(self.settings.m4_image_concurrency)

        async def enrich(element_index: int, record: dict[str, Any]) -> None:
            element = elements[element_index]
            assert element.image_bytes is not None
            mime_type = element.image_mime_type or "application/octet-stream"
            original_alt_text = element.content.strip()
            async with semaphore:
                ocr_result, vision_result = await asyncio.gather(
                    self._analyze_image(self.ocr_provider, element.image_bytes, mime_type),
                    self._analyze_image(self.vision_provider, element.image_bytes, mime_type),
                )

            record.update(
                {
                    "ocr_text": ocr_result["text"],
                    "ocr_status": ocr_result["status"],
                    "ocr_provider": ocr_result["provider"],
                    "ocr_model_name": ocr_result["model_name"],
                    "ocr_error_code": ocr_result["error_code"],
                    "ocr_metadata": ocr_result["metadata"],
                    "vision_caption": vision_result["text"],
                    "vision_status": vision_result["status"],
                    "vision_provider": vision_result["provider"],
                    "vision_model_name": vision_result["model_name"],
                    "vision_error_code": vision_result["error_code"],
                    "vision_metadata": vision_result["metadata"],
                }
            )
            content_parts = []
            if original_alt_text:
                content_parts.append(f"图片替代文本：{original_alt_text}")
            if vision_result["text"]:
                content_parts.append(f"图片描述：{vision_result['text']}")
            if ocr_result["text"]:
                content_parts.append(f"图片文字：{ocr_result['text']}")
            element.content = "\n".join(content_parts) or "文档内图片"
            element.metadata.update(
                {
                    "alt_text": original_alt_text,
                    "ocr_status": ocr_result["status"],
                    "vision_status": vision_result["status"],
                    "ocr_model": ocr_result["model_name"],
                    "vision_model": vision_result["model_name"],
                }
            )

        await asyncio.gather(
            *(enrich(element_index, record) for element_index, record in image_records.items())
        )

    @staticmethod
    async def _analyze_image(
        provider: ImageUnderstandingProvider,
        image_bytes: bytes,
        mime_type: str,
    ) -> dict[str, Any]:
        if provider.provider == "disabled":
            return {
                "text": "",
                "status": "SKIPPED",
                "provider": provider.provider,
                "model_name": provider.model_name,
                "error_code": None,
                "metadata": {},
            }
        started_at = time.perf_counter()
        try:
            result = await provider.analyze(image_bytes, mime_type)
            return {
                "text": result.text,
                "status": "READY",
                "provider": result.provider,
                "model_name": result.model_name,
                "error_code": None,
                "metadata": {
                    **result.metadata,
                    "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
                },
            }
        except ImageUnderstandingError as exc:
            return {
                "text": "",
                "status": "FAILED",
                "provider": provider.provider,
                "model_name": provider.model_name,
                "error_code": exc.code,
                "metadata": {"latency_ms": round((time.perf_counter() - started_at) * 1000, 2)},
            }
        except Exception:
            return {
                "text": "",
                "status": "FAILED",
                "provider": provider.provider,
                "model_name": provider.model_name,
                "error_code": "INTERNAL_ERROR",
                "metadata": {"latency_ms": round((time.perf_counter() - started_at) * 1000, 2)},
            }

    async def _persist_document(
        self,
        *,
        index: dict[str, Any],
        document: dict[str, Any],
        elements: list[ParsedElement],
        element_ids: list[UUID],
        image_records: dict[int, dict[str, Any]],
        chunks: list[Any],
        parents: list[Any],
        vectors: list[list[float]],
        mammoth_warnings: list[str],
    ) -> None:
        index_id = UUID(str(index["id"]))
        index_document_id = UUID(str(document["index_document_id"]))
        document_id = UUID(str(document["document_id"]))
        kb_id = UUID(str(document["kb_id"]))
        async with self.engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM document_chunk WHERE index_document_id = :id"),
                {"id": index_document_id},
            )
            await connection.execute(
                text("DELETE FROM document_parent_chunk WHERE index_document_id = :id"),
                {"id": index_document_id},
            )
            await connection.execute(
                text("DELETE FROM document_element WHERE index_document_id = :id"),
                {"id": index_document_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO document_element (
                        id, index_id, index_document_id, document_id, element_type,
                        sequence_no, content, section_path, metadata
                    ) VALUES (
                        :id, :index_id, :index_document_id, :document_id, :element_type,
                        :sequence_no, :content, CAST(:section_path AS jsonb),
                        CAST(:metadata AS jsonb)
                    )
                    """
                ),
                [
                    {
                        "id": element_ids[element_index],
                        "index_id": index_id,
                        "index_document_id": index_document_id,
                        "document_id": document_id,
                        "element_type": element.element_type,
                        "sequence_no": element_index + 1,
                        "content": element.content,
                        "section_path": json.dumps(element.section_path, ensure_ascii=False),
                        "metadata": json.dumps(element.metadata, ensure_ascii=False),
                    }
                    for element_index, element in enumerate(elements)
                ],
            )
            if image_records:
                await connection.execute(
                    text(
                        """
                        INSERT INTO image_asset (
                            id, index_id, index_document_id, document_id, element_id,
                            minio_bucket, minio_object_key, file_hash, mime_type, width, height,
                            ocr_text, vision_caption, ocr_status, vision_status,
                            ocr_provider, ocr_model_name, ocr_error_code,
                            vision_provider, vision_model_name, vision_error_code,
                            processed_at, metadata
                        ) VALUES (
                            :id, :index_id, :index_document_id, :document_id, :element_id,
                            :bucket, :object_key, :file_hash, :mime_type, :width, :height,
                            :ocr_text, :vision_caption, :ocr_status, :vision_status,
                            :ocr_provider, :ocr_model_name, :ocr_error_code,
                            :vision_provider, :vision_model_name, :vision_error_code,
                            now(), CAST(:metadata AS jsonb)
                        )
                        """
                    ),
                    [
                        {
                            **record,
                            "index_id": index_id,
                            "index_document_id": index_document_id,
                            "document_id": document_id,
                            "element_id": element_ids[element_index],
                            "bucket": self.settings.minio_image_bucket,
                            "metadata": json.dumps(
                                {
                                    "pipeline": "m4_multimodal",
                                    "degraded": (
                                        record["ocr_status"] == "FAILED"
                                        or record["vision_status"] == "FAILED"
                                    ),
                                    "ocr": record["ocr_metadata"],
                                    "vision": record["vision_metadata"],
                                }
                            ),
                        }
                        for element_index, record in image_records.items()
                    ],
                )

            parent_ids = [
                uuid5(NAMESPACE_URL, f"{index_document_id}:parent:{sequence_no}")
                for sequence_no in range(1, len(parents) + 1)
            ]
            await connection.execute(
                text(
                    """
                    INSERT INTO document_parent_chunk (
                        id, kb_id, index_id, index_document_id, document_id, parent_type,
                        sequence_no, content, token_count, section_path, metadata
                    ) VALUES (
                        :id, :kb_id, :index_id, :index_document_id, :document_id, :parent_type,
                        :sequence_no, :content, :token_count, CAST(:section_path AS jsonb),
                        CAST(:metadata AS jsonb)
                    )
                    """
                ),
                [
                    {
                        "id": parent_ids[parent_index],
                        "kb_id": kb_id,
                        "index_id": index_id,
                        "index_document_id": index_document_id,
                        "document_id": document_id,
                        "parent_type": parent.parent_type,
                        "sequence_no": parent_index + 1,
                        "content": parent.content,
                        "token_count": parent.token_count,
                        "section_path": json.dumps(parent.section_path, ensure_ascii=False),
                        "metadata": json.dumps(parent.metadata, ensure_ascii=False),
                    }
                    for parent_index, parent in enumerate(parents)
                ],
            )
            parent_mappings = [
                {
                    "parent_id": parent_ids[parent_index],
                    "element_id": element_ids[element_index],
                    "ordinal": ordinal,
                }
                for parent_index, parent in enumerate(parents)
                for ordinal, element_index in enumerate(parent.element_indexes, start=1)
            ]
            if parent_mappings:
                await connection.execute(
                    text(
                        """
                        INSERT INTO parent_chunk_element (parent_id, element_id, ordinal)
                        VALUES (:parent_id, :element_id, :ordinal)
                        """
                    ),
                    parent_mappings,
                )

            chunk_ids = [
                uuid5(NAMESPACE_URL, f"{index_document_id}:chunk:{sequence_no}")
                for sequence_no in range(1, len(chunks) + 1)
            ]
            await connection.execute(
                text(
                    """
                    INSERT INTO document_chunk (
                        id, kb_id, index_id, index_document_id, document_id, chunk_type,
                        sequence_no, content, search_text, token_count, embedding,
                        section_path, metadata, parent_id, previous_chunk_id, next_chunk_id,
                        suspected_incomplete, incomplete_reasons, is_procedural
                    ) VALUES (
                        :id, :kb_id, :index_id, :index_document_id, :document_id, :chunk_type,
                        :sequence_no, :content, :search_text, :token_count,
                        CAST(:embedding AS vector), CAST(:section_path AS jsonb),
                        CAST(:metadata AS jsonb), :parent_id, :previous_chunk_id, :next_chunk_id,
                        :suspected_incomplete, CAST(:incomplete_reasons AS jsonb), :is_procedural
                    )
                    """
                ),
                [
                    {
                        "id": chunk_ids[chunk_index],
                        "kb_id": kb_id,
                        "index_id": index_id,
                        "index_document_id": index_document_id,
                        "document_id": document_id,
                        "chunk_type": chunk.chunk_type,
                        "sequence_no": chunk_index + 1,
                        "content": chunk.content,
                        "search_text": chunk.search_text,
                        "token_count": chunk.token_count,
                        "embedding": self._vector_literal(vectors[chunk_index]),
                        "section_path": json.dumps(chunk.section_path, ensure_ascii=False),
                        "metadata": json.dumps(
                            {
                                **chunk.metadata,
                                "image_asset_ids": [
                                    str(image_records[element_index]["id"])
                                    for element_index in chunk.element_indexes
                                    if element_index in image_records
                                ],
                                "embedding_provider": self.embedding_provider.provider,
                                "embedding_model": self.embedding_provider.model_name,
                            },
                            ensure_ascii=False,
                        ),
                        "parent_id": (
                            parent_ids[chunk.parent_index]
                            if chunk.parent_index is not None
                            else None
                        ),
                        "previous_chunk_id": None,
                        "next_chunk_id": None,
                        "suspected_incomplete": chunk.suspected_incomplete,
                        "incomplete_reasons": json.dumps(chunk.incomplete_reasons),
                        "is_procedural": chunk.is_procedural,
                    }
                    for chunk_index, chunk in enumerate(chunks)
                ],
            )
            adjacency_updates = [
                {
                    "id": chunk_ids[chunk_index],
                    "previous_chunk_id": (
                        chunk_ids[chunk.previous_chunk_index]
                        if chunk.previous_chunk_index is not None
                        else None
                    ),
                    "next_chunk_id": (
                        chunk_ids[chunk.next_chunk_index]
                        if chunk.next_chunk_index is not None
                        else None
                    ),
                }
                for chunk_index, chunk in enumerate(chunks)
                if chunk.previous_chunk_index is not None or chunk.next_chunk_index is not None
            ]
            if adjacency_updates:
                await connection.execute(
                    text(
                        """
                        UPDATE document_chunk
                        SET previous_chunk_id = :previous_chunk_id,
                            next_chunk_id = :next_chunk_id
                        WHERE id = :id
                        """
                    ),
                    adjacency_updates,
                )
            mappings = []
            for chunk_index, chunk in enumerate(chunks):
                for ordinal, element_index in enumerate(chunk.element_indexes, start=1):
                    mappings.append(
                        {
                            "chunk_id": chunk_ids[chunk_index],
                            "element_id": element_ids[element_index],
                            "ordinal": ordinal,
                        }
                    )
            await connection.execute(
                text(
                    """
                    INSERT INTO chunk_element (chunk_id, element_id, ordinal)
                    VALUES (:chunk_id, :element_id, :ordinal)
                    """
                ),
                mappings,
            )
            await connection.execute(
                text(
                    """
                    UPDATE index_document
                    SET status = 'READY', finished_at = now(), error_code = NULL,
                        error_message = NULL,
                        metadata = CAST(:metadata AS jsonb)
                    WHERE id = :id
                    """
                ),
                {
                    "id": index_document_id,
                    "metadata": json.dumps(
                        {
                            "parser": "mammoth+ooxml",
                            "mammoth_warnings": mammoth_warnings,
                            "element_count": len(elements),
                            "chunk_count": len(chunks),
                            "parent_chunk_count": len(parents),
                            "image_count": len(image_records),
                            "ocr_ready_count": sum(
                                record["ocr_status"] == "READY" for record in image_records.values()
                            ),
                            "vision_ready_count": sum(
                                record["vision_status"] == "READY"
                                for record in image_records.values()
                            ),
                        },
                        ensure_ascii=False,
                    ),
                },
            )

    async def _finalize_index(
        self, index_id: UUID, kb_id: UUID, task_id: UUID
    ) -> tuple[int, int, int]:
        async with self.engine.begin() as connection:
            status_result = await connection.execute(
                text(
                    """
                    SELECT count(*) FILTER (WHERE status = 'READY') AS ready_count,
                           count(*) AS total_count
                    FROM index_document WHERE index_id = :index_id
                    """
                ),
                {"index_id": index_id},
            )
            statuses = status_result.mappings().one()
            if statuses["ready_count"] != statuses["total_count"]:
                raise ValueError("索引存在未完成文档")
            counts_result = await connection.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM index_document WHERE index_id = :index_id) AS documents,
                      (SELECT count(*) FROM document_element
                       WHERE index_id = :index_id) AS elements,
                      (SELECT count(*) FROM document_chunk WHERE index_id = :index_id) AS chunks
                    """
                ),
                {"index_id": index_id},
            )
            counts = counts_result.mappings().one()
            if counts["documents"] <= 0 or counts["elements"] <= 0 or counts["chunks"] <= 0:
                raise ValueError("索引完整性检查失败")
            await connection.execute(
                text(
                    """
                    UPDATE knowledge_index
                    SET status = 'READY', document_count = :documents,
                        element_count = :elements, chunk_count = :chunks,
                        finished_at = now(), error_code = NULL, error_message = NULL
                    WHERE id = :index_id AND status = 'BUILDING'
                    """
                ),
                {"index_id": index_id, **counts},
            )
            activation_result = await connection.execute(
                text(
                    """
                    SELECT activate_on_success FROM knowledge_index
                    WHERE id = :index_id
                    """
                ),
                {"index_id": index_id},
            )
            activate_on_success = bool(activation_result.scalar_one())
            old_result = await connection.execute(
                text("SELECT active_index_id FROM knowledge_base WHERE id = :kb_id FOR UPDATE"),
                {"kb_id": kb_id},
            )
            old_index_id = old_result.scalar_one()
            if activate_on_success:
                if old_index_id is not None:
                    await connection.execute(
                        text(
                            """
                            UPDATE knowledge_index SET status = 'DEPRECATED'
                            WHERE id = :old_index_id AND status = 'ACTIVE'
                            """
                        ),
                        {"old_index_id": old_index_id},
                    )
                await connection.execute(
                    text(
                        """
                        UPDATE knowledge_index
                        SET status = 'ACTIVE', activated_at = now()
                        WHERE id = :index_id AND status = 'READY'
                        """
                    ),
                    {"index_id": index_id},
                )
                await connection.execute(
                    text(
                        """
                        UPDATE knowledge_base
                        SET active_index_id = :index_id, updated_at = now()
                        WHERE id = :kb_id
                        """
                    ),
                    {"index_id": index_id, "kb_id": kb_id},
                )
            await connection.execute(
                text(
                    """
                    UPDATE task_record
                    SET status = 'SUCCEEDED', stage = 'COMPLETED', progress = 100,
                        finished_at = now(), updated_at = now(), error_code = NULL,
                        error_message = NULL
                    WHERE id = :task_id
                    """
                ),
                {"task_id": task_id},
            )
            return int(counts["documents"]), int(counts["elements"]), int(counts["chunks"])

    async def _update_document_status(
        self, index_document_id: UUID, status: str, *, started: bool = False
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE index_document
                    SET status = :status,
                        started_at = CASE WHEN :started
                            THEN COALESCE(started_at, now()) ELSE started_at END
                    WHERE id = :id
                    """
                ),
                {"id": index_document_id, "status": status, "started": started},
            )

    async def _update_task(
        self,
        task_id: UUID,
        status: str,
        stage: str,
        progress: int,
        *,
        increment_attempt: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE task_record
                    SET status = :status, stage = :stage, progress = :progress,
                        attempt = attempt + CASE WHEN :increment_attempt THEN 1 ELSE 0 END,
                        started_at = COALESCE(started_at, now()), updated_at = now(),
                        metadata = COALESCE(CAST(:metadata AS jsonb), metadata)
                    WHERE id = :id
                    """
                ),
                {
                    "id": task_id,
                    "status": status,
                    "stage": stage,
                    "progress": progress,
                    "increment_attempt": increment_attempt,
                    "metadata": (
                        json.dumps(metadata, ensure_ascii=False) if metadata is not None else None
                    ),
                },
            )

    async def _mark_failed(self, index_id: UUID, task_id: UUID, exc: Exception) -> None:
        error_code = type(exc).__name__[:100]
        error_message = str(exc)[:500] or "索引构建失败"
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE index_document
                    SET status = 'FAILED', error_code = :error_code,
                        error_message = :error_message, finished_at = now()
                    WHERE index_id = :index_id AND status <> 'READY'
                    """
                ),
                {
                    "index_id": index_id,
                    "error_code": error_code,
                    "error_message": error_message,
                },
            )
            await connection.execute(
                text(
                    """
                    UPDATE knowledge_index
                    SET status = 'FAILED', error_code = :error_code,
                        error_message = :error_message, finished_at = now()
                    WHERE id = :index_id AND status = 'BUILDING'
                    """
                ),
                {
                    "index_id": index_id,
                    "error_code": error_code,
                    "error_message": error_message,
                },
            )
            await connection.execute(
                text(
                    """
                    UPDATE task_record
                    SET status = 'FAILED', stage = 'FAILED', error_code = :error_code,
                        error_message = :error_message, finished_at = now(), updated_at = now()
                    WHERE id = :task_id
                    """
                ),
                {
                    "task_id": task_id,
                    "error_code": error_code,
                    "error_message": error_message,
                },
            )

    @staticmethod
    def _vector_literal(vector: list[float]) -> str:
        return "[" + ",".join(format(value, ".9g") for value in vector) + "]"

    @staticmethod
    def _image_size(data: bytes) -> tuple[int | None, int | None]:
        try:
            with Image.open(BytesIO(data)) as image:
                return image.width, image.height
        except Exception:
            return None, None

    @staticmethod
    def _image_extension(mime_type: str | None) -> str:
        mapping = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/gif": "gif",
            "image/webp": "webp",
            "image/bmp": "bmp",
            "image/tiff": "tiff",
        }
        if mime_type in mapping:
            return mapping[mime_type]
        return PurePosixPath(f"image.{(mime_type or 'bin').split('/')[-1]}").suffix.lstrip(".")
