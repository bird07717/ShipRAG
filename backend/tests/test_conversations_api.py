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
        self.list_filters: list[
            tuple[UUID, str | None, int, str | None, datetime | None, datetime | None]
        ] = []

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
        self,
        knowledge_id: UUID,
        user_id: str | None,
        limit: int,
        source: str | None = None,
        activity_from: datetime | None = None,
        activity_to: datetime | None = None,
    ) -> list[dict[str, Any]]:
        self.list_filters.append((knowledge_id, user_id, limit, source, activity_from, activity_to))
        return [
            row
            for row in self.conversations.values()
            if row["knowledge_id"] == knowledge_id
            and (user_id is None or row.get("user_id") == user_id)
            and (source is None or row.get("last_source") == source)
            and (activity_from is None or row["updated_at"] >= activity_from)
            and (activity_to is None or row["updated_at"] <= activity_to)
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
    assert repository.list_filters[-1] == (knowledge_id, "user-a", 50, None, None, None)


def test_list_conversations_filters_by_channel_and_reports_last_source(
    client: TestClient,
) -> None:
    repository = _ConversationRepositoryStub()
    knowledge_id = repository.knowledge_id
    external = repository.add_conversation(
        knowledge_id=knowledge_id,
        user_id="biz-user",
        message_count=2,
        last_source="ASSISTANT_API",
    )
    repository.add_conversation(
        knowledge_id=knowledge_id, message_count=2, last_source="INTERNAL_CHAT"
    )
    _install(client, repository)

    response = client.get(
        f"/api/v1/conversations?knowledge_id={knowledge_id}&source=ASSISTANT_API&limit=200",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["id"] for item in items] == [str(external)]
    assert items[0]["last_source"] == "ASSISTANT_API"
    assert repository.list_filters[-1] == (
        knowledge_id,
        None,
        200,
        "ASSISTANT_API",
        None,
        None,
    )

    rejected = client.get(
        f"/api/v1/conversations?knowledge_id={knowledge_id}&source=UNKNOWN",
        headers={"Authorization": "Bearer test-token"},
    )
    assert rejected.status_code == 422


def test_list_conversations_filters_by_recent_activity_range(client: TestClient) -> None:
    repository = _ConversationRepositoryStub()
    knowledge_id = repository.knowledge_id
    included = repository.add_conversation(
        knowledge_id=knowledge_id,
        title="目标会话",
        message_count=2,
        updated_at=datetime(2026, 10, 7, 10, tzinfo=UTC),
    )
    repository.add_conversation(
        knowledge_id=knowledge_id,
        title="较早会话",
        message_count=2,
        updated_at=datetime(2026, 10, 6, 10, tzinfo=UTC),
    )
    _install(client, repository)

    response = client.get(
        f"/api/v1/conversations?knowledge_id={knowledge_id}"
        "&activity_from=2026-10-07T09:00:00Z&activity_to=2026-10-07T11:00:00Z",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [str(included)]
    assert repository.list_filters[-1][4:] == (
        datetime(2026, 10, 7, 9, tzinfo=UTC),
        datetime(2026, 10, 7, 11, tzinfo=UTC),
    )


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
            "conversation_id": conversation_id,
            "role": "USER",
            "content": "第一问",
            "status": "COMPLETED",
            "sources": [],
            "content_blocks": [],
            "response_type": None,
            "support_mode": None,
            "created_at": _NOW,
            "updated_at": _NOW,
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
    message = own_messages.json()["items"][0]
    assert message["conversation_id"] == str(conversation_id)
    assert message["content"] == "第一问"
    assert message["content_blocks"] == []
    assert message["response_type"] is None
    assert message["support_mode"] is None
    assert foreign_delete.status_code == 404
    assert own_delete.status_code == 200
    assert own_delete.json() == {
        "conversation_id": str(conversation_id),
        "deleted": True,
    }
    assert repository.deleted == [conversation_id]


def test_conversation_message_route_is_registered_once(client: TestClient) -> None:
    matches = [
        route
        for route in client.app.routes
        if getattr(route, "path", None) == "/api/v1/conversations/{conversation_id}/messages"
        and "GET" in (getattr(route, "methods", set()) or set())
    ]

    assert len(matches) == 1


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
