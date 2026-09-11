from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings
from app.rag.repository import RagRepository


@pytest.mark.asyncio
async def test_title_recall_is_bounded_index_scoped_and_rename_aware():
    url = os.getenv("SUPPORT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SUPPORT_TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                schema = "title_recall_" + uuid4().hex
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                for statement in [
                    "CREATE TABLE document_source("
                    "id uuid, kb_id uuid, display_name text, status text)",
                    """CREATE TABLE document_chunk(
                        id uuid, kb_id uuid, index_id uuid, document_id uuid,
                        chunk_type text DEFAULT 'TEXT', sequence_no integer DEFAULT 1,
                        content text DEFAULT '正文', token_count integer DEFAULT 10,
                        section_path text[] DEFAULT '{}', parent_id uuid, previous_chunk_id uuid,
                        next_chunk_id uuid, suspected_incomplete boolean DEFAULT false,
                        incomplete_reasons text[] DEFAULT '{}', is_procedural boolean DEFAULT false
                    )""",
                    "CREATE TABLE chunk_element(chunk_id uuid, element_id uuid, ordinal integer)",
                    "CREATE TABLE image_asset(id uuid, element_id uuid)",
                ]:
                    await connection.execute(text(statement))
                kb, index = uuid4(), uuid4()
                cases = [
                    (kb, index, "FFC600更换电池组", "STORED"),
                    (kb, uuid4(), "FFC600旧索引", "STORED"),
                    (uuid4(), index, "FFC600其他知识库", "STORED"),
                    (kb, index, "FFC600已删除", "DELETED"),
                    (kb, index, "FFC6000其他型号", "STORED"),
                ]
                valid_id = None
                for i, (knowledge_id, index_id, title, status) in enumerate(cases):
                    document_id, chunk_id = uuid4(), uuid4()
                    if i == 0:
                        valid_id = document_id
                    await connection.execute(
                        text("INSERT INTO document_source VALUES (:id,:kb,:title,:status)"),
                        {"id": document_id, "kb": knowledge_id, "title": title, "status": status},
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO document_chunk(id,kb_id,index_id,document_id) "
                            "VALUES (:id,:kb,:index,:doc)"
                        ),
                        {"id": chunk_id, "kb": knowledge_id, "index": index_id, "doc": document_id},
                    )

                class BoundEngine:
                    @asynccontextmanager
                    async def connect(self):
                        yield connection

                repo = RagRepository(cast(AsyncEngine, BoundEngine()), Settings(_env_file=None))
                kwargs = {
                    "knowledge_id": kb,
                    "index_id": index,
                    "question": "FFC600电池怎么换",
                    "terms": ["ffc600"],
                }
                result = await repo.title_recall_candidates(**kwargs)
                assert [item.document_id for item in result] == [valid_id]
                await connection.execute(
                    text("UPDATE document_source SET display_name='其他型号' WHERE id=:id"),
                    {"id": valid_id},
                )
                assert await repo.title_recall_candidates(**kwargs) == []
                # A broad product code must not pick three arbitrary UUIDs.
                for i in range(4):
                    doc, chunk = uuid4(), uuid4()
                    await connection.execute(
                        text("INSERT INTO document_source VALUES (:id,:kb,:title,'STORED')"),
                        {"id": doc, "kb": kb, "title": f"FFC600操作{i}"},
                    )
                    await connection.execute(
                        text("""INSERT INTO document_chunk(id,kb_id,index_id,document_id)
                             VALUES (:id,:kb,:index,:doc)"""),
                        {"id": chunk, "kb": kb, "index": index, "doc": doc},
                    )
                    recalled = await repo.title_recall_candidates(**kwargs)
                    assert len(recalled) == (i + 1 if i < 3 else 0)
            finally:
                # The schema and all fixture rows are rolled back, not persisted.
                await transaction.rollback()
    finally:
        await engine.dispose()
