from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api.ingestion import (
    get_document_profile_repository,
    get_document_profile_service,
    get_ingestion_service,
)


class _KnowledgeDeleteServiceStub:
    def __init__(self) -> None:
        self.deleted_id: UUID | None = None

    async def request_knowledge_base_deletion(self, knowledge_id: UUID) -> dict[str, Any]:
        self.deleted_id = knowledge_id
        return {
            "task_id": uuid4(),
            "task_type": "KNOWLEDGE_BASE_DELETE",
            "status": "QUEUED",
            "replayed": False,
        }


class _ProfileRebuildServiceStub:
    def __init__(self) -> None:
        self.requested_id: UUID | None = None

    async def request_generation(self, knowledge_id: UUID) -> dict[str, Any]:
        self.requested_id = knowledge_id
        return {
            "task_id": uuid4(),
            "task_type": "DOCUMENT_PROFILE_GENERATION",
            "status": "QUEUED",
            "replayed": False,
        }


class _ProfileContextRepositoryStub:
    def __init__(self, context: dict[str, Any] | None) -> None:
        self.context = context

    async def document_context(self, document_id: UUID) -> dict[str, Any] | None:
        return self.context


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

    assert response.status_code == 202
    assert response.json()["task_type"] == "KNOWLEDGE_BASE_DELETE"
    assert response.json()["status"] == "QUEUED"
    assert service.deleted_id == knowledge_id


def test_rebuild_knowledge_profiles_returns_task_submission(client: TestClient) -> None:
    knowledge_id = uuid4()
    service = _ProfileRebuildServiceStub()
    client.app.dependency_overrides[get_document_profile_service] = lambda: service
    try:
        response = client.post(
            f"/api/v1/knowledge-bases/{knowledge_id}/profiles/rebuild",
            headers={"Authorization": "Bearer test-token", "Idempotency-Key": "rebuild-key-1"},
        )
    finally:
        client.app.dependency_overrides.pop(get_document_profile_service, None)

    assert response.status_code == 202
    body: dict[str, Any] = response.json()
    assert body["task_type"] == "DOCUMENT_PROFILE_GENERATION"
    assert body["status"] == "QUEUED"
    assert body["replayed"] is False
    assert service.requested_id == knowledge_id


def test_document_profile_context_returns_published_and_candidate(client: TestClient) -> None:
    document_id = uuid4()
    repository = _ProfileContextRepositoryStub(
        {
            "document_id": str(document_id),
            "published_profile": {
                "id": str(uuid4()),
                "problem_code": "PDC_UPGRADE",
                "canonical_question": "如何升级 PDC？",
                "aliases": ["PDC 怎么升级"],
                "keywords": ["升级"],
                "product_name": "PDC",
                "model_names": ["VDR600"],
                "task_type": "UPGRADE",
                "routing_summary": "解决 PDC 升级问题",
                "symptoms": [],
                "exclusions": [],
                "enabled": True,
                "profile_enrichment_version": 1,
                "updated_at": None,
            },
            "candidate_draft": {
                "id": str(uuid4()),
                "release_id": str(uuid4()),
                "index_id": str(uuid4()),
                "index_version": 7,
                "release_status": "DRAFT",
                "status": "DRAFT",
                "operation": "UPDATE",
                "target_profile_id": None,
                "problem_code": "PDC_UPGRADE",
                "canonical_question": "如何升级 PDC？",
                "aliases": [],
                "keywords": ["升级"],
                "product_name": "PDC",
                "model_names": ["VDR600"],
                "task_type": "UPGRADE",
                "routing_summary": "解决 PDC 升级问题",
                "enabled": True,
                "quality_status": "REVIEW_REQUIRED",
                "quality_notes": ["需人工核对"],
                "generation_source": "LLM",
                "updated_at": None,
            },
        }
    )
    client.app.dependency_overrides[get_document_profile_repository] = lambda: repository
    try:
        response = client.get(
            f"/api/v1/documents/{document_id}/profile-context",
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        client.app.dependency_overrides.pop(get_document_profile_repository, None)

    assert response.status_code == 200
    context: dict[str, Any] = response.json()
    assert context["document_id"] == str(document_id)
    assert context["published_profile"]["problem_code"] == "PDC_UPGRADE"
    assert context["candidate_draft"]["quality_status"] == "REVIEW_REQUIRED"
    assert context["candidate_draft"]["index_version"] == 7


def test_document_profile_context_rejects_unknown_document(client: TestClient) -> None:
    repository = _ProfileContextRepositoryStub(None)
    client.app.dependency_overrides[get_document_profile_repository] = lambda: repository
    try:
        response = client.get(
            f"/api/v1/documents/{uuid4()}/profile-context",
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        client.app.dependency_overrides.pop(get_document_profile_repository, None)

    assert response.status_code == 404
    body: dict[str, Any] = response.json()
    assert body["error"]["code"] == "DOCUMENT_NOT_FOUND"
