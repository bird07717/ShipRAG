"""Inspect/verify a local legacy DOCX without uploading or rebuilding an index."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.ingestion.image_understanding import create_ocr_provider
from app.ingestion.legacy_text import restore_legacy_text
from app.ingestion.parser import DocxParseError, parse_docx
from app.rag.models import ModelSnapshot
from app.rag.repository import RagRepository


async def run(args):
    parsed = parse_docx(args.docx.read_bytes())
    result = {
        "before": dict(Counter(e.element_type for e in parsed.elements)),
        "ocr_fragments": sum(
            bool(e.metadata.get("legacy_text_candidate")) for e in parsed.elements
        ),
        "warnings": parsed.mammoth_warnings,
    }
    print(json.dumps(result, ensure_ascii=False), flush=True)
    result["status"] = "PARSED_ONLY"
    if args.real_ocr:
        settings = Settings(m4_ocr_provider="siliconflow")
        url = make_url(settings.database_url.get_secret_value())
        if url.host not in {"localhost", "127.0.0.1", "::1"}:
            raise SystemExit("Local database required")
        engine = create_async_engine(
            url,
            connect_args={"server_settings": {"default_transaction_read_only": "on"}},
        )
        try:
            async with engine.connect() as connection:
                row = dict(
                    (
                        await connection.execute(
                            text(
                                "SELECT * FROM model_config WHERE model_type='OCR' AND enabled"
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
            snapshot = ModelSnapshot(
                row["provider"],
                row["model_name"],
                row["parameters"],
                base_url=row["base_url"],
                api_key=RagRepository(engine, settings)._decrypt_api_key(row),
            )
            provider = create_ocr_provider(settings, snapshot)
            try:
                try:
                    elements = await restore_legacy_text(
                        parsed.elements, provider, concurrency=4
                    )
                except DocxParseError as exc:
                    result.update({"status": "BLOCKED", "error": str(exc)})
                else:
                    result.update(
                        {
                            "status": "OCR_COMPLETED_REVIEW_REQUIRED",
                            "after": dict(Counter(e.element_type for e in elements)),
                            "text": "\n".join(
                                e.content for e in elements if e.element_type == "TEXT"
                            ),
                            "provider": snapshot.provider,
                            "model": snapshot.model_name,
                        }
                    )
            finally:
                await provider.aclose()
        finally:
            await engine.dispose()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "text"},
            ensure_ascii=False,
        )
    )
    if result["status"] == "BLOCKED":
        raise SystemExit(2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("docx", type=Path)
    parser.add_argument(
        "--real-ocr",
        action="store_true",
        help="Calls configured real OCR and incurs provider fees",
    )
    parser.add_argument("--output", type=Path)
    asyncio.run(run(parser.parse_args()))
