from __future__ import annotations

import json
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from minio import Minio
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.ingestion.chunker import build_chunks
from app.ingestion.embedding import FakeEmbeddingProvider
from app.ingestion.image_understanding import (
    DisabledImageUnderstandingProvider,
    FakeImageUnderstandingProvider,
    ImageUnderstandingError,
    OpenAiCompatibleImageProvider,
    create_ocr_provider,
)
from app.ingestion.models import ImageUnderstandingResult, ParsedElement
from app.ingestion.pipeline import IndexPipeline
from app.rag.models import RetrievalCandidate
from app.rag.prompt import build_context


@pytest.mark.asyncio
async def test_openai_compatible_ocr_and_vision_contracts() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        content = (
            "端口 3306" if payload["model"] == "ocr-model" else "数据库配置界面显示端口 3306。"
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    async with httpx.AsyncClient(
        base_url="https://provider.example/v1/", transport=httpx.MockTransport(handler)
    ) as client:
        ocr = OpenAiCompatibleImageProvider(
            capability="OCR",
            provider="siliconflow",
            model_name="ocr-model",
            base_url="https://provider.example/v1/",
            api_key="secret",
            timeout_seconds=10,
            max_tokens=100,
            max_output_chars=100,
            client=client,
        )
        vision = OpenAiCompatibleImageProvider(
            capability="VISION",
            provider="zhipu",
            model_name="vision-model",
            base_url="https://provider.example/v1/",
            api_key="secret",
            timeout_seconds=10,
            max_tokens=100,
            max_output_chars=100,
            client=client,
        )
        ocr_result = await ocr.analyze(b"png", "image/png")
        vision_result = await vision.analyze(b"png", "image/png")

    assert ocr_result.text == "端口 3306"
    assert vision_result.text.endswith("3306。")
    assert requests[0]["temperature"] == 0
    assert requests[0]["messages"][0]["content"][0]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert "thinking" not in requests[0]
    assert requests[1]["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_image_provider_rejects_invalid_response_without_body_leak() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"private": "do-not-leak"})

    async with httpx.AsyncClient(
        base_url="https://provider.example/v1/", transport=httpx.MockTransport(handler)
    ) as client:
        provider = OpenAiCompatibleImageProvider(
            capability="OCR",
            provider="siliconflow",
            model_name="ocr-model",
            base_url="https://provider.example/v1/",
            api_key="secret",
            timeout_seconds=10,
            max_tokens=100,
            max_output_chars=100,
            client=client,
        )
        with pytest.raises(ImageUnderstandingError, match="返回格式错误") as captured:
            await provider.analyze(b"png", "image/png")

    assert captured.value.code == "INVALID_RESPONSE"
    assert "do-not-leak" not in str(captured.value)


class _FailedVisionProvider:
    capability = "VISION"
    provider = "zhipu"
    model_name = "failed-vision"

    async def analyze(self, image_bytes: bytes, mime_type: str) -> ImageUnderstandingResult:
        _ = image_bytes, mime_type
        raise ImageUnderstandingError("UPSTREAM_HTTP_ERROR", "Vision 上游失败")

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_image_enrichment_degrades_one_capability_and_builds_mixed_chunk() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        m2_embedding_provider="fake",
        m4_ocr_provider="fake",
        m4_vision_provider="fake",
    )
    pipeline = IndexPipeline(
        cast(AsyncEngine, object()),
        cast(Minio, object()),
        settings,
        embedding_provider=FakeEmbeddingProvider(1024),
        ocr_provider=FakeImageUnderstandingProvider("OCR"),
        vision_provider=cast(Any, _FailedVisionProvider()),
    )
    elements = [
        ParsedElement("TEXT", "数据库配置步骤", ["部署"]),
        ParsedElement(
            "IMAGE",
            "端口截图",
            ["部署"],
            image_bytes=b"image-bytes",
            image_mime_type="image/png",
        ),
        ParsedElement("TEXT", "保存后重启服务", ["部署"]),
    ]
    records = {1: {"id": uuid4()}}

    await pipeline._enrich_images(elements, records)
    chunks = build_chunks(elements, target_chars=100, max_chars=300)

    assert records[1]["ocr_status"] == "READY"
    assert records[1]["vision_status"] == "FAILED"
    assert records[1]["vision_error_code"] == "UPSTREAM_HTTP_ERROR"
    assert "图片替代文本：端口截图" in elements[1].content
    assert "图片文字：模拟OCR文本" in elements[1].content
    assert [chunk.chunk_type for chunk in chunks] == ["TEXT", "MIXED", "TEXT"]
    assert "数据库配置步骤" in chunks[1].search_text
    assert "模拟OCR文本" in chunks[1].search_text
    assert chunks[1].image_bytes == b"image-bytes"


@pytest.mark.asyncio
async def test_disabled_image_capability_is_explicitly_skipped() -> None:
    result = await IndexPipeline._analyze_image(
        DisabledImageUnderstandingProvider("OCR"), b"image", "image/png"
    )

    assert result == {
        "text": "",
        "status": "SKIPPED",
        "provider": "disabled",
        "model_name": "disabled",
        "error_code": None,
        "metadata": {},
    }


def test_multimodal_source_exposes_only_registered_image_assets() -> None:
    image_asset_id = uuid4()
    candidate = RetrievalCandidate(
        chunk_id=uuid4(),
        document_id=uuid4(),
        document="部署手册.docx",
        chunk_type="MIXED",
        content="图片文字：端口 3306",
        token_count=10,
        section_path=["数据库"],
        element_ids=[uuid4()],
        distance=0.1,
        similarity=0.9,
        rank=1,
        image_asset_ids=[image_asset_id],
    )

    context, sources = build_context([candidate])

    assert "图片文字：端口 3306" in context
    assert sources[0].public_dict()["image_asset_ids"] == [str(image_asset_id)]


def test_standalone_image_builds_image_chunk_with_understanding_text() -> None:
    element = ParsedElement(
        "IMAGE",
        "图片描述：服务运行正常\n图片文字：STATUS OK",
        ["监控"],
        image_bytes=b"image",
        image_mime_type="image/png",
    )

    chunks = build_chunks([element])

    assert len(chunks) == 1
    assert chunks[0].chunk_type == "IMAGE"
    assert "STATUS OK" in chunks[0].search_text
    assert chunks[0].image_bytes == b"image"


@pytest.mark.asyncio
async def test_explicit_cloud_mode_without_key_degrades_instead_of_blocking_build() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        siliconflow_api_key=None,
        m4_ocr_provider="siliconflow",
    )
    provider = create_ocr_provider(settings)

    result = await IndexPipeline._analyze_image(provider, b"image", "image/png")

    assert result["status"] == "FAILED"
    assert result["error_code"] == "NOT_CONFIGURED"
    assert result["model_name"] == settings.siliconflow_ocr_model
