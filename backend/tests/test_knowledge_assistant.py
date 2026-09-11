from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.api.knowledge_assistant import get_assistant_configuration
from app.common.suggestions import validate_suggestions

QUESTIONS = ["如何检查设备电源", "如何查看系统状态", "如何执行设备自检"]


@pytest.mark.parametrize(
    "values",
    [
        ["短"],
        [" "],
        ["字" * 201],
        [*QUESTIONS, "如何查看设备型号"],
        ["如何检查电源？", " 如何检查电源? "],
        ["Test ABC", "test　abc"],
    ],
)
def test_rejects_invalid_or_normalized_duplicate_questions(values: list[str]) -> None:
    with pytest.raises(ValueError):
        validate_suggestions(values)


def test_preserves_order_and_trims_questions() -> None:
    assert validate_suggestions([" 如何检查设备电源 ", QUESTIONS[1]]) == QUESTIONS[:2]
    assert validate_suggestions([]) == []


def test_management_contract_and_authentication(client: Any) -> None:
    knowledge_id = uuid4()
    config = {
        "knowledge_id": str(knowledge_id),
        "assistant_online": False,
        "suggestions": QUESTIONS,
        "revision": 2,
    }
    service = AsyncMock()
    service.get.return_value = {
        **config,
        "ready": False,
        "blockers": ["LLM 未配置"],
        "status": "ENABLED",
    }
    service.update.return_value = config
    client.app.dependency_overrides[get_assistant_configuration] = lambda: service
    path = f"/api/v1/knowledge-bases/{knowledge_id}"
    headers = {"Authorization": "Bearer test-token"}
    try:
        assert client.get(f"{path}/assistant-config").status_code == 401
        response = client.get(f"{path}/assistant-config", headers=headers)
        assert response.status_code == 200
        assert response.json()["blockers"] == ["LLM 未配置"]
        assert "status" not in response.json()
        assert (
            client.put(
                f"{path}/assistant-config",
                headers=headers,
                json={"revision": 1, "suggestions": ["重复问题", "重复问题？"]},
            ).status_code
            == 422
        )
        assert (
            client.put(
                f"{path}/assistant-config",
                headers=headers,
                json={"revision": 1, "suggestions": QUESTIONS, "assistant_online": True},
            ).status_code
            == 422
        )
        response = client.put(
            f"{path}/assistant-config",
            headers=headers,
            json={"revision": 1, "suggestions": QUESTIONS},
        )
        assert response.status_code == 200
        service.update.assert_awaited_with(knowledge_id, revision=1, suggestions=QUESTIONS)
        response = client.put(
            f"{path}/assistant-publication",
            headers=headers,
            json={"revision": 2, "assistant_online": False},
        )
        assert response.status_code == 200
        service.update.assert_awaited_with(knowledge_id, revision=2, online=False)
        assert (
            client.put(
                f"/api/v1/assistant/knowledge-bases/{knowledge_id}/assistant-config",
                headers=headers,
                json={"revision": 1, "suggestions": QUESTIONS},
            ).status_code
            == 404
        )
    finally:
        client.app.dependency_overrides.clear()
