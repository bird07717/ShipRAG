from __future__ import annotations

import pytest

from app.document_profiles.service import DocumentProfileService


class _Repository:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    async def pending_rows(self, _knowledge_id: object) -> list[dict[str, object]]:
        return self.rows

    @staticmethod
    def intents(payload: dict[str, object]) -> list[dict[str, object]]:
        intents = payload.get("intents", [])
        return list(intents) if isinstance(intents, list) else []

    @staticmethod
    def normalize(value: str) -> str:
        return "".join(value.lower().split())


@pytest.mark.asyncio
async def test_check_allows_attention_item_without_blocking_publish() -> None:
    repository = _Repository(
        [
            {
                "document_name": "可上线文档",
                "pending_state": "PENDING_ONLINE",
                "pending_payload": {
                    "intents": [
                        {
                            "problem_code": "UPGRADE",
                            "canonical_question": "如何升级？",
                            "aliases": ["怎么升级"],
                        }
                    ]
                },
            },
            {
                "document_name": "保守草稿",
                "pending_state": "NEEDS_ATTENTION",
                "pending_payload": {"intents": []},
            },
        ]
    )

    report = await DocumentProfileService(repository, settings=None).check(object())  # type: ignore[arg-type]

    assert report["valid"] is True
    assert report["publishable"] is True
    assert report["pending_count"] == 1
    assert report["warnings"][0]["code"] == "PROFILE_NEEDS_ATTENTION"


@pytest.mark.asyncio
async def test_check_rejects_alias_conflicts_before_publish() -> None:
    repository = _Repository(
        [
            {
                "document_name": "文档 A",
                "pending_state": "PENDING_ONLINE",
                "pending_payload": {
                    "intents": [
                        {
                            "problem_code": "A",
                            "canonical_question": "问题 A",
                            "aliases": ["同一个问法"],
                        }
                    ]
                },
            },
            {
                "document_name": "文档 B",
                "pending_state": "PENDING_ONLINE",
                "pending_payload": {
                    "intents": [
                        {
                            "problem_code": "B",
                            "canonical_question": "问题 B",
                            "aliases": ["同一个问法"],
                        }
                    ]
                },
            },
        ]
    )

    report = await DocumentProfileService(repository, settings=None).check(object())  # type: ignore[arg-type]

    assert report["valid"] is False
    assert report["publishable"] is False
    assert report["errors"][0]["code"] == "PROFILE_ALIAS_CONFLICT"
