from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings
from app.rag.repository import RagRepository


@pytest.mark.asyncio
async def test_document_retrieval_filters_before_limit_and_isolates_kb_index_and_status():
    url = os.getenv("SUPPORT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SUPPORT_TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            tx = await connection.begin()
            try:
                schema = "quality_" + uuid4().hex
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
                for sql in [
                    "CREATE TABLE document_source("
                    "id uuid PRIMARY KEY, display_name text, status text)",
                    """CREATE TABLE document_chunk(
                        id uuid PRIMARY KEY, kb_id uuid, index_id uuid, document_id uuid,
                        search_text text, embedding vector(1024),
                        chunk_type text DEFAULT 'TEXT', sequence_no integer DEFAULT 1,
                        content text DEFAULT 'scopeunique', token_count integer DEFAULT 10,
                        section_path text[] DEFAULT '{}', parent_id uuid, previous_chunk_id uuid,
                        next_chunk_id uuid, suspected_incomplete boolean DEFAULT false,
                        incomplete_reasons text[] DEFAULT '{}',
                        is_procedural boolean DEFAULT false)""",
                    "CREATE TABLE chunk_element(chunk_id uuid, element_id uuid, ordinal integer)",
                    "CREATE TABLE image_asset(id uuid, element_id uuid)",
                ]:
                    await connection.execute(text(sql))
                kb, index, focus, other = uuid4(), uuid4(), uuid4(), uuid4()
                for doc in [focus, other]:
                    await connection.execute(
                        text("INSERT INTO document_source VALUES(:id,'document','STORED')"),
                        {"id": doc},
                    )
                vector = [1.0] + [0.0] * 1023
                # All scores tie, so IDs force out-of-scope rows before the
                # desired row. Filtering after LIMIT 1 must fail this test.
                expected_chunk = UUID(int=4)
                for chunk, kid, iid, doc in [
                    (expected_chunk, kb, index, focus),
                    (UUID(int=1), kb, index, other),
                    (UUID(int=2), uuid4(), index, focus),
                    (UUID(int=3), kb, uuid4(), focus),
                ]:
                    await connection.execute(
                        text("""INSERT INTO document_chunk
                        (id,kb_id,index_id,document_id,search_text,embedding)
                        VALUES(:id,:kb,:index,:doc,'scopeunique',CAST(:embedding AS vector))"""),
                        {
                            "id": chunk,
                            "kb": kid,
                            "index": iid,
                            "doc": doc,
                            "embedding": str(vector),
                        },
                    )
                await connection.execute(
                    text("""CREATE INDEX quality_bm25 ON document_chunk
                    USING bm25 (id, search_text, kb_id, index_id) WITH (key_field='id')""")
                )

                class BoundEngine:
                    @asynccontextmanager
                    async def connect(self):
                        yield connection

                repo = RagRepository(cast(AsyncEngine, BoundEngine()), Settings(_env_file=None))
                kwargs = {"knowledge_id": kb, "index_id": index, "document_id": focus, "limit": 1}
                for method, extra in [
                    (repo.vector_search, {"embedding": vector}),
                    (repo.bm25_search, {"query": "scopeunique"}),
                ]:
                    result = await method(**kwargs, **extra)
                    assert len(result) == 1 and result[0].document_id == focus
                    assert result[0].chunk_id == expected_chunk
                    assert await method(**{**kwargs, "knowledge_id": uuid4()}, **extra) == []
                    assert await method(**{**kwargs, "index_id": uuid4()}, **extra) == []
                await connection.execute(
                    text("UPDATE document_source SET status='DELETED' WHERE id=:id"), {"id": focus}
                )
                assert await repo.vector_search(**kwargs, embedding=vector) == []
                assert await repo.bm25_search(**kwargs, query="scopeunique") == []
            finally:
                await tx.rollback()
    finally:
        await engine.dispose()
