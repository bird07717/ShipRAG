from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.common.errors import ApiError
from app.rag.llm import FakeLlmProvider, LlmError
from app.support.focused_assistant import FocusedSupportAssistant
from app.support.models import DocumentContentBlock, FocusedDocument


def _document() -> FocusedDocument:
    return FocusedDocument(
        id=uuid4(),
        title="PDC 网络配置",
        problem_code="PDC_NETWORK",
        index_id=uuid4(),
        blocks=[
            DocumentContentBlock(uuid4(), 1, "text", "设备 IP 为 172.16.8.150。"),
            DocumentContentBlock(
                uuid4(),
                2,
                "image",
                image_asset_id=uuid4(),
                caption="网络配置界面",
                mime_type="image/png",
            ),
        ],
    )


def _draft(**overrides: object) -> str:
    payload: dict[str, object] = {
        "mode": "DOCUMENT_EXPLANATION",
        "answer": "文档中的设备地址是 172.16.8.150。",
        "sections": [
            {
                "type": "DOCUMENT_FACT",
                "title": "设备地址",
                "content": "设备 IP 为 172.16.8.150。",
                "evidence_element_ids": ["E1"],
            }
        ],
        "image_ids": ["I1"],
        "questions": [],
        "workflow_update": {},
        "document_switch": None,
        "escalation": {"required": False, "reason": None},
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def _assistant(provider: FakeLlmProvider) -> FocusedSupportAssistant:
    return FocusedSupportAssistant(
        provider,
        max_tokens=2_048,
        temperature=0.1,
        timeout_seconds=30,
    )


@pytest.mark.asyncio
async def test_focused_assistant_validates_and_resolves_document_ids() -> None:
    document = _document()
    provider = FakeLlmProvider(completions=[_draft()])

    draft, evidence, images = await _assistant(provider).answer(
        question="文档中的地址是什么？",
        document=document,
        workflow={},
        history=[],
    )

    assert draft.sections[0].evidence_element_ids == [str(document.blocks[0].element_id)]
    assert evidence["E1"] == str(document.blocks[0].element_id)
    assert images["I1"] == str(document.blocks[1].image_asset_id)
    assert provider.usage["structured_attempts"] == 1


@pytest.mark.asyncio
async def test_focused_assistant_repairs_invalid_json_once() -> None:
    provider = FakeLlmProvider(completions=["not-json", _draft(image_ids=[])])

    draft, _, _ = await _assistant(provider).answer(
        question="解释地址",
        document=_document(),
        workflow={},
        history=[],
    )

    assert draft.mode.value == "DOCUMENT_EXPLANATION"
    assert provider.usage["structured_attempts"] == 2


@pytest.mark.asyncio
async def test_focused_assistant_rejects_two_invalid_structured_outputs() -> None:
    provider = FakeLlmProvider(completions=["bad", "still-bad"])

    with pytest.raises(LlmError) as exc_info:
        await _assistant(provider).answer(
            question="解释地址",
            document=_document(),
            workflow={},
            history=[],
        )

    assert exc_info.value.code == "STRUCTURED_OUTPUT_INVALID"
    assert provider.usage["structured_attempts"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "error_code"),
    [
        (
            _draft(
                sections=[
                    {
                        "type": "DOCUMENT_FACT",
                        "title": "伪造证据",
                        "content": "设备 IP 为 172.16.8.150。",
                        "evidence_element_ids": ["E999"],
                    }
                ],
                image_ids=[],
            ),
            "INVALID_EVIDENCE",
        ),
        (_draft(image_ids=["I999"]), "INVALID_IMAGE_REFERENCE"),
        (
            _draft(
                sections=[
                    {
                        "type": "DOCUMENT_FACT",
                        "title": "伪造参数",
                        "content": "设备 IP 为 10.0.0.99。",
                        "evidence_element_ids": ["E1"],
                    }
                ],
                image_ids=[],
            ),
            "UNSUPPORTED_PRODUCT_PARAMETER",
        ),
    ],
)
async def test_focused_assistant_rejects_out_of_document_references(
    payload: str, error_code: str
) -> None:
    provider = FakeLlmProvider(completions=[payload])

    with pytest.raises(ApiError) as exc_info:
        await _assistant(provider).answer(
            question="继续处理",
            document=_document(),
            workflow={},
            history=[],
        )

    assert exc_info.value.code == error_code


@pytest.mark.asyncio
async def test_focused_assistant_blocks_unsupported_high_risk_action() -> None:
    provider = FakeLlmProvider(
        completions=[
            _draft(
                mode="DIAGNOSTIC_ASSISTANCE",
                answer="请暂停操作。",
                sections=[
                    {
                        "type": "GENERAL_GUIDANCE",
                        "title": "格式化设备",
                        "content": "请格式化设备后重试。",
                        "evidence_element_ids": [],
                    }
                ],
                image_ids=[],
            )
        ]
    )

    draft, _, _ = await _assistant(provider).answer(
        question="下一步怎么办？",
        document=_document(),
        workflow={},
        history=[],
    )

    assert draft.escalation.required is True
    assert draft.sections[0].type.value == "WARNING"
    assert draft.sections[0].risk_level is not None
    assert draft.sections[0].risk_level.value == "HIGH"
