from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api.ingestion import get_ingestion_service


class _KnowledgeDeleteServiceStub:
    def __init__(self) -> None:
        self.deleted_id: UUID | None = None

    async def delete_knowledge_base(self, knowledge_id: UUID) -> dict[str, Any]:
        self.deleted_id = knowledge_id
        return {
            "knowledge_id": knowledge_id,
            "deleted": True,
            "cleanup_failed_count": 0,
        }


def test_liveness_returns_request_id(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "test-request"})

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}
    assert response.headers["X-Request-ID"] == "test-request"


def test_generated_request_id(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]


def test_service_api_requires_bearer_token(client: TestClient) -> None:
    response = client.get("/api/v1/system/info")

    assert response.status_code == 401
    body: dict[str, Any] = response.json()
    assert body["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert body["error"]["request_id"]


def test_service_api_accepts_configured_token(client: TestClient) -> None:
    response = client.get("/api/v1/system/info", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 200
    assert response.json()["environment"] == "test"


def test_readiness_is_bounded_when_dependencies_are_offline(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert set(body["checks"]) == {"postgres", "redis", "minio"}
    assert all(check["status"] == "error" for check in body["checks"].values())


def test_delete_knowledge_base_uses_ingestion_service(client: TestClient) -> None:
    knowledge_id = uuid4()
    service = _KnowledgeDeleteServiceStub()
    client.app.dependency_overrides[get_ingestion_service] = lambda: service
    try:
        response = client.delete(
            f"/api/v1/knowledge-bases/{knowledge_id}",
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        client.app.dependency_overrides.pop(get_ingestion_service, None)

    assert response.status_code == 200
    assert response.json() == {
        "knowledge_id": str(knowledge_id),
        "deleted": True,
        "cleanup_failed_count": 0,
    }
    assert service.deleted_id == knowledge_id
