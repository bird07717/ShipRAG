from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api.engine_dependencies import get_gateway_repository
from app.gateway.repository import GatewayRepository

_NOW = datetime(2026, 8, 28, tzinfo=UTC)


class _ConversationRepositoryStub:
    def __init__(self) -> None:
        self.knowledge_id = uuid4()
        self.conversations: dict[UUID, dict[str, Any]] = {}
        self.deleted: list[UUID] = []
        self.messages: dict[UUID, list[dict[str, Any]]] = {}
        self.list_filters: list[tuple[UUID, str | None, int]] = []

    def add_conversation(self, **row: Any) -> UUID:
        conversation_id = uuid4()
        self.conversations[conversation_id] = {
            "id": conversation_id,
            "created_at": _NOW,
            "updated_at": _NOW,
            **row,
        }
        return conversation_id

    async def list_conversations(
        self, knowledge_id: UUID, user_id: str | None, limit: int
    ) -> list[dict[str, Any]]:
        self.list_filters.append((knowledge_id, user_id, limit))
        return [
            row
            for row in self.conversations.values()
            if row["knowledge_id"] == knowledge_id
            and (user_id is None or row.get("user_id") == user_id)
        ]

    async def get_conversation(self, conversation_id: UUID) -> dict[str, Any] | None:
        return self.conversations.get(conversation_id)

    async def list_conversation_messages(
        self, conversation_id: UUID, limit: int
    ) -> list[dict[str, Any]]:
        return self.messages.get(conversation_id, [])

    async def delete_conversation(self, conversation_id: UUID) -> bool:
        self.deleted.append(conversation_id)
        return self.conversations.pop(conversation_id, None) is not None


def _install(client: TestClient, repository: _ConversationRepositoryStub) -> None:
    client.app.dependency_overrides[get_gateway_repository] = lambda: cast(
        GatewayRepository, repository
    )


def test_list_conversations_scopes_by_user(client: TestClient) -> None:
    repository = _ConversationRepositoryStub()
    knowledge_id = repository.knowledge_id
    mine = repository.add_conversation(
        knowledge_id=knowledge_id, user_id="user-a", title="我的会话", message_count=2
    )
    repository.add_conversation(
        knowledge_id=knowledge_id, user_id="user-b", title="他人会话", message_count=4
    )
    _install(client, repository)

    response = client.get(
        f"/api/v1/conversations?knowledge_id={knowledge_id}&user_id=user-a",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["id"] for item in items] == [str(mine)]
    assert items[0]["title"] == "我的会话"
    assert repository.list_filters[-1] == (knowledge_id, "user-a", 50)


def test_get_conversation_hidden_from_other_user(client: TestClient) -> None:
    repository = _ConversationRepositoryStub()
    conversation_id = repository.add_conversation(
        knowledge_id=repository.knowledge_id, user_id="user-a", message_count=0
    )
    _install(client, repository)

    visible = client.get(
        f"/api/v1/conversations/{conversation_id}?user_id=user-a",
        headers={"Authorization": "Bearer test-token"},
    )
    hidden = client.get(
        f"/api/v1/conversations/{conversation_id}?user_id=user-b",
        headers={"Authorization": "Bearer test-token"},
    )

    assert visible.status_code == 200
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"


def test_messages_and_delete_enforce_same_ownership(client: TestClient) -> None:
    repository = _ConversationRepositoryStub()
    conversation_id = repository.add_conversation(
        knowledge_id=repository.knowledge_id, user_id="user-a", message_count=0
    )
    repository.messages[conversation_id] = [
        {
            "id": uuid4(),
            "role": "USER",
            "content": "第一问",
            "status": "COMPLETED",
            "sources": [],
            "created_at": _NOW,
        }
    ]
    _install(client, repository)

    foreign_messages = client.get(
        f"/api/v1/conversations/{conversation_id}/messages?user_id=user-b",
        headers={"Authorization": "Bearer test-token"},
    )
    own_messages = client.get(
        f"/api/v1/conversations/{conversation_id}/messages?user_id=user-a",
        headers={"Authorization": "Bearer test-token"},
    )
    foreign_delete = client.delete(
        f"/api/v1/conversations/{conversation_id}?user_id=user-b",
        headers={"Authorization": "Bearer test-token"},
    )
    own_delete = client.delete(
        f"/api/v1/conversations/{conversation_id}?user_id=user-a",
        headers={"Authorization": "Bearer test-token"},
    )

    assert foreign_messages.status_code == 404
    assert own_messages.status_code == 200
    assert own_messages.json()["items"][0]["content"] == "第一问"
    assert foreign_delete.status_code == 404
    assert own_delete.status_code == 200
    assert own_delete.json() == {
        "conversation_id": str(conversation_id),
        "deleted": True,
    }
    assert repository.deleted == [conversation_id]


def test_anonymous_conversation_remains_service_accessible(client: TestClient) -> None:
    repository = _ConversationRepositoryStub()
    conversation_id = repository.add_conversation(
        knowledge_id=repository.knowledge_id, user_id=None, message_count=0
    )
    _install(client, repository)

    response = client.get(
        f"/api/v1/conversations/{conversation_id}?user_id=anyone",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
