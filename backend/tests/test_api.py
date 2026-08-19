from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


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
