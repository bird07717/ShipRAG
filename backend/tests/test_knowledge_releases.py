from __future__ import annotations

from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.common.errors import ApiError
from app.core.config import Settings
from app.ingestion.repository import IngestionRepository
from app.releases.models import PROFILE_ENRICHMENT_VERSION, GeneratedProfile
from app.releases.repository import ReleaseRepository
from app.releases.service import ReleaseService
from app.support.models import ProblemDecision, ProblemProfile
from app.support.problem_resolver import ProblemResolver


def _draft(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": str(uuid4()),
        "document_name": "PDC 升级",
        "operation": "CREATE",
        "target_profile_id": None,
        "status": "APPROVED",
        "problem_code": "PDC_UPGRADE",
        "canonical_question": "如何升级 PDC？",
        "aliases": ["PDC 怎么升级"],
        "keywords": ["升级"],
        "model_names": ["VDR600"],
        "candidate_readiness": {"document_published": True, "images_complete": True},
    }
    value.update(overrides)
    return value


def test_release_validation_accepts_an_approved_candidate() -> None:
    report = ReleaseService._validate_snapshot(
        {"index_status": "READY", "drafts": [_draft()], "live_profiles": []}
    )

    assert report.valid is True
    assert report.errors == []


def test_release_validation_blocks_unapproved_and_conflicting_profiles() -> None:
    draft = _draft(status="DRAFT")
    live = {
        "id": str(uuid4()),
        "problem_code": "PDC_UPGRADE",
        "canonical_question": "如何升级 PDC",
        "aliases": [],
        "enabled": True,
    }

    report = ReleaseService._validate_snapshot(
        {"index_status": "READY", "drafts": [draft], "live_profiles": [live]}
    )

    assert report.valid is False
    assert {item["code"] for item in report.errors} >= {
        "PROBLEM_CODE_CONFLICT",
        "CANONICAL_QUESTION_CONFLICT",
    }
    assert report.review_required_count == 1
    assert report.publishable is False


def test_release_validation_marks_unapproved_drafts_for_review_not_as_hard_failure() -> None:
    report = ReleaseService._validate_snapshot(
        {"index_status": "READY", "drafts": [_draft(status="DRAFT")], "live_profiles": []}
    )

    assert report.valid is True
    assert report.publishable is False
    assert report.review_required_count == 1
    assert report.errors == []


def test_quality_review_requirement_blocks_publication_even_after_approval() -> None:
    report = ReleaseService._validate_snapshot(
        {
            "index_status": "READY",
            "drafts": [_draft(quality_status="REVIEW_REQUIRED")],
            "live_profiles": [],
        }
    )

    assert report.valid is True
    assert report.publishable is False
    assert report.review_required_count == 1


def test_release_validation_allows_explicit_reference_only_document() -> None:
    report = ReleaseService._validate_snapshot(
        {
            "index_status": "READY",
            "drafts": [_draft(operation="NONE")],
            "live_profiles": [],
        }
    )

    assert report.valid is True


def test_release_validation_remove_draft_requires_target() -> None:
    report = ReleaseService._validate_snapshot(
        {
            "index_status": "READY",
            "drafts": [_draft(operation="REMOVE", target_profile_id=None)],
            "live_profiles": [],
        }
    )

    assert report.valid is False
    assert {item["code"] for item in report.errors} == {"REMOVE_TARGET_REQUIRED"}


def test_release_validation_remove_draft_skips_index_presence_checks() -> None:
    report = ReleaseService._validate_snapshot(
        {
            "index_status": "READY",
            "drafts": [
                _draft(
                    operation="REMOVE",
                    target_profile_id=uuid4(),
                    candidate_readiness={"document_published": False},
                )
            ],
            "live_profiles": [],
        }
    )

    assert report.valid is True
    assert report.errors == []


def test_remove_draft_publish_disables_live_profile_instead_of_deleting() -> None:
    connection = _Connection()
    profile_id = uuid4()
    statements: list[str] = []

    async def _execute(statement: object, parameters: dict[str, object]) -> object:
        statements.append(str(statement))
        assert parameters["target_profile_id"] == profile_id
        return type("Result", (), {"rowcount": 1})()

    connection.execute = _execute  # type: ignore[method-assign]
    draft = _draft(operation="REMOVE", target_profile_id=profile_id)

    import asyncio

    asyncio.run(ReleaseRepository._publish_profile(connection, draft))  # type: ignore[arg-type]

    assert any("enabled = false" in statement for statement in statements)
    assert not any("DELETE FROM document_problem_profile" in s for s in statements)


def test_rebase_service_reports_classification_and_requests_backfill() -> None:
    class _Repository:
        def __init__(self) -> None:
            self.rebased: list[UUID] = []

        async def rebase_release(self, release_id: UUID) -> dict[str, object]:
            self.rebased.append(release_id)
            return {
                "source_release_id": str(release_id),
                "target_release_id": str(uuid4()),
                "target_index_id": str(uuid4()),
                "target_index_version": 7,
                "reused_draft_count": 3,
                "review_required_draft_count": 1,
                "regenerate_document_count": 1,
                "remove_suggestion_count": 2,
                "create_suggestion_count": 2,
            }

    class _Service(ReleaseService):
        def __init__(self, repository: _Repository) -> None:
            self.repository = repository  # type: ignore[assignment]
            self.requested: list[tuple[UUID, str]] = []

        async def request_profile_task(
            self, index_id: UUID, *, task_type: str, draft_id: UUID | None = None
        ) -> dict[str, object]:
            self.requested.append((index_id, task_type))
            return {"task_id": str(uuid4()), "replayed": False}

    import asyncio

    repository = _Repository()
    service = _Service(repository)
    release_id = uuid4()

    result = asyncio.run(service.rebase(release_id))

    assert repository.rebased == [release_id]
    assert result["backfill_submitted"] is True
    assert service.requested[0][1] == "PROFILE_BACKFILL"


def test_rebase_service_tolerates_backfill_submission_failure() -> None:
    class _Repository:
        async def rebase_release(self, release_id: UUID) -> dict[str, object]:
            return {
                "source_release_id": str(release_id),
                "target_release_id": str(uuid4()),
                "target_index_id": str(uuid4()),
                "target_index_version": 3,
                "reused_draft_count": 0,
                "review_required_draft_count": 0,
                "regenerate_document_count": 0,
                "remove_suggestion_count": 0,
                "create_suggestion_count": 1,
            }

    class _Service(ReleaseService):
        def __init__(self, repository: _Repository) -> None:
            self.repository = repository  # type: ignore[assignment]

        async def request_profile_task(
            self, index_id: UUID, *, task_type: str, draft_id: UUID | None = None
        ) -> dict[str, object]:
            raise ApiError("UPSTREAM_UNAVAILABLE", "异步任务队列不可用", 503)

    import asyncio

    service = _Service(_Repository())

    result = asyncio.run(service.rebase(uuid4()))

    assert result["backfill_submitted"] is False


def test_fallback_profile_is_conservative_and_editable() -> None:
    document_id = uuid4()
    profile = ReleaseService._fallback_profile(
        "故障解决文档11—如何U盘升级PDC",
        {"document_id": document_id},
    )

    assert profile.canonical_question.endswith("？")
    assert "升级" in profile.keywords
    assert profile.task_type == "UPGRADE"
    assert profile.routing_summary
    assert profile.symptoms == []
    assert profile.exclusions == []
    assert profile.confidence < 0.5


def test_replacement_detection_preserves_stable_problem_code() -> None:
    profile = GeneratedProfile(
        problem_code="AI_NEW_CODE",
        canonical_question="如何通过 U 盘升级 PDC？",
    )
    existing = {
        "id": uuid4(),
        "problem_code": "PDC_UPDATE_UPGRADE",
        "canonical_question": "如何使用U盘升级PDC？",
        "aliases": ["PDC刷版本"],
        "keywords": ["升级"],
        "model_names": ["VDR600"],
        "product_name": "PDC",
        "task_type": "UPGRADE",
    }

    operation, target = ReleaseService._suggest_operation(
        uuid4(), "如何通过U盘升级PDC", profile, [existing]
    )
    assert operation == "UPDATE"
    assert target is existing
    merged = ReleaseService._merge_update(profile, existing)
    assert merged.problem_code == "PDC_UPDATE_UPGRADE"
    assert merged.aliases == ["PDC刷版本"]


def test_same_document_always_generates_update_even_when_question_is_rewritten() -> None:
    document_id = uuid4()
    existing = {
        "id": uuid4(),
        "document_id": document_id,
        "problem_code": "PDC_UPGRADE",
        "canonical_question": "PDC 固件升级后无法启动怎么办？",
    }
    profile = GeneratedProfile(
        problem_code="NEW_TITLE_CODE",
        canonical_question="如何从 USB 介质执行控制器软件更新？",
    )

    operation, target = ReleaseService._suggest_operation(
        document_id, "设备软件维护指引", profile, [existing]
    )

    assert operation == "UPDATE"
    assert target is existing


def test_preflight_excludes_update_target_but_flags_other_live_collisions() -> None:
    target = {
        "id": uuid4(),
        "problem_code": "PDC_UPGRADE",
        "canonical_question": "如何升级 PDC？",
        "aliases": ["PDC 怎么升级"],
    }
    profile = GeneratedProfile(
        problem_code="PDC_UPGRADE",
        canonical_question="如何升级 PDC？",
        aliases=["PDC 怎么升级"],
    )

    assert ReleaseService._preflight_quality_notes(profile, [target], target) == []
    notes = ReleaseService._preflight_quality_notes(
        profile,
        [target, {**target, "id": uuid4()}],
        target,
    )
    assert len(notes) == 3


def test_backfill_selects_only_missing_or_outdated_profiles_without_drafts() -> None:
    missing_document = uuid4()
    outdated_document = uuid4()
    current_document = uuid4()
    editing_document = uuid4()

    selected = ReleaseService._backfill_document_ids(
        [
            {"document_id": missing_document},
            {"document_id": outdated_document},
            {"document_id": current_document},
            {"document_id": editing_document},
        ],
        [
            {
                "document_id": outdated_document,
                "profile_enrichment_version": PROFILE_ENRICHMENT_VERSION - 1,
            },
            {
                "document_id": current_document,
                "profile_enrichment_version": PROFILE_ENRICHMENT_VERSION,
            },
            {
                "document_id": editing_document,
                "profile_enrichment_version": 0,
            },
        ],
        {editing_document},
    )

    assert selected == {missing_document, outdated_document}


@pytest.mark.asyncio
async def test_model_name_alone_never_directly_selects_a_profile() -> None:
    profile = ProblemProfile(
        id=uuid4(),
        document_id=uuid4(),
        knowledge_id=uuid4(),
        problem_code="VDR_UPGRADE",
        canonical_question="如何升级 VDR600？",
        aliases=[],
        keywords=[],
        product_name="VDR",
        model_names=["VDR600"],
        task_type="UPGRADE",
    )

    model_only = await ProblemResolver().resolve(question="VDR600", profiles=[profile])
    product_and_intent = await ProblemResolver().resolve(
        question="VDR600 如何升级", profiles=[profile]
    )

    assert model_only.decision == ProblemDecision.NO_MATCH
    assert product_and_intent.decision == ProblemDecision.NO_MATCH


class _Result:
    def __init__(self, *, row: dict[str, Any] | None = None, scalar: Any = None) -> None:
        self.row = row
        self.scalar = scalar

    def mappings(self) -> _Result:
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        return self.row

    def one(self) -> dict[str, Any]:
        assert self.row is not None
        return self.row

    def scalar_one_or_none(self) -> Any:
        return self.scalar


class _Connection:
    def __init__(self, results: list[_Result] | None = None) -> None:
        self.results = list(results or [])
        self.executed: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> _Connection:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def execute(self, statement: Any, parameters: dict[str, Any]) -> _Result:
        self.executed.append((str(statement), parameters))
        return self.results.pop(0) if self.results else _Result()


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def begin(self) -> _Connection:
        return self.connection


@pytest.mark.asyncio
async def test_ready_legacy_index_without_release_can_still_activate() -> None:
    index_id = uuid4()
    knowledge_id = uuid4()
    connection = _Connection(
        [
            _Result(
                row={
                    "id": index_id,
                    "kb_id": knowledge_id,
                    "status": "READY",
                    "active_index_id": None,
                }
            ),
            _Result(row={"id": index_id, "kb_id": knowledge_id, "status": "ACTIVE"}),
        ]
    )
    repository = IngestionRepository(
        cast(AsyncEngine, _Engine(connection)), Settings(_env_file=None, app_env="test")
    )

    published = await repository.activate_index(index_id)

    assert published["status"] == "ACTIVE"
    statements = "\n".join(statement for statement, _ in connection.executed)
    assert "rag_active_index_id" in statements
    assert "LEFT JOIN knowledge_release" not in statements


@pytest.mark.asyncio
async def test_candidate_index_can_activate_for_rag_without_support_publication() -> None:
    index_id = uuid4()
    knowledge_id = uuid4()
    connection = _Connection(
        [
            _Result(
                row={
                    "id": index_id,
                    "kb_id": knowledge_id,
                    "status": "READY",
                    "active_index_id": None,
                }
            ),
            _Result(row={"id": index_id, "kb_id": knowledge_id, "status": "ACTIVE"}),
        ]
    )
    repository = IngestionRepository(
        cast(AsyncEngine, _Engine(connection)), Settings(_env_file=None, app_env="test")
    )

    activated = await repository.activate_index(index_id)

    assert activated["status"] == "ACTIVE"
    statements = "\n".join(statement for statement, _ in connection.executed)
    assert "release_status" not in statements
    assert "rag_active_index_id = :index_id" in statements


@pytest.mark.asyncio
async def test_publish_rejects_a_release_superseded_by_a_newer_version() -> None:
    release_id = uuid4()
    connection = _Connection(
        [
            _Result(
                row={
                    "id": release_id,
                    "kb_id": uuid4(),
                    "index_id": uuid4(),
                    "status": "READY_TO_PUBLISH",
                    "index_status": "READY",
                    "index_version": 2,
                    "active_index_id": uuid4(),
                }
            ),
            _Result(scalar=1),
        ]
    )
    repository = ReleaseRepository(cast(AsyncEngine, _Engine(connection)))

    with pytest.raises(ApiError) as raised:
        await repository.publish(release_id)

    assert raised.value.code == "RELEASE_SUPERSEDED"
    assert "newer_index.version > :index_version" in connection.executed[1][0]


@pytest.mark.asyncio
async def test_rag_rollback_switches_pointer_and_double_writes_alias() -> None:
    index_id = uuid4()
    knowledge_id = uuid4()
    connection = _Connection(
        [
            _Result(row={"status": "ENABLED", "rag_active_index_id": uuid4()}),
            _Result(
                row={
                    "id": index_id,
                    "kb_id": knowledge_id,
                    "status": "DEPRECATED",
                    "version": 1,
                }
            ),
            _Result(),
            _Result(
                row={
                    "id": index_id,
                    "kb_id": knowledge_id,
                    "status": "ACTIVE",
                    "version": 1,
                }
            ),
            _Result(),
        ]
    )
    repository = IngestionRepository(
        cast(AsyncEngine, _Engine(connection)), Settings(_env_file=None, app_env="test")
    )

    rolled = await repository.rollback_rag_index(knowledge_id, index_id)

    assert rolled["status"] == "ACTIVE"
    statements = "\n".join(statement for statement, _ in connection.executed)
    assert "rag_active_index_id = :index_id" in statements
    assert "active_index_id = :index_id" in statements
    assert "support_active" not in statements


@pytest.mark.asyncio
async def test_rag_rollback_to_current_version_is_idempotent() -> None:
    index_id = uuid4()
    knowledge_id = uuid4()
    connection = _Connection(
        [
            _Result(row={"status": "ENABLED", "rag_active_index_id": index_id}),
            _Result(
                row={
                    "id": index_id,
                    "kb_id": knowledge_id,
                    "status": "ACTIVE",
                    "version": 2,
                }
            ),
        ]
    )
    repository = IngestionRepository(
        cast(AsyncEngine, _Engine(connection)), Settings(_env_file=None, app_env="test")
    )

    rolled = await repository.rollback_rag_index(knowledge_id, index_id)

    assert rolled["status"] == "ACTIVE"
    assert len(connection.executed) == 2


@pytest.mark.asyncio
async def test_rag_rollback_rejects_building_target() -> None:
    index_id = uuid4()
    knowledge_id = uuid4()
    connection = _Connection(
        [
            _Result(row={"status": "ENABLED", "rag_active_index_id": uuid4()}),
            _Result(
                row={
                    "id": index_id,
                    "kb_id": knowledge_id,
                    "status": "BUILDING",
                    "version": 3,
                }
            ),
        ]
    )
    repository = IngestionRepository(
        cast(AsyncEngine, _Engine(connection)), Settings(_env_file=None, app_env="test")
    )

    with pytest.raises(ApiError) as raised:
        await repository.rollback_rag_index(knowledge_id, index_id)

    assert raised.value.code == "INDEX_NOT_ROLLBACKABLE"
