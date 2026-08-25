from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.common.errors import ApiError
from app.ingestion.repository import IngestionRepository
from app.releases.models import GeneratedProfile
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
        "DRAFT_NOT_APPROVED",
        "PROBLEM_CODE_CONFLICT",
        "CANONICAL_QUESTION_CONFLICT",
    }


def test_release_validation_allows_explicit_reference_only_document() -> None:
    report = ReleaseService._validate_snapshot(
        {
            "index_status": "READY",
            "drafts": [_draft(operation="NONE")],
            "live_profiles": [],
        }
    )

    assert report.valid is True


def test_fallback_profile_is_conservative_and_editable() -> None:
    document_id = uuid4()
    profile = ReleaseService._fallback_profile(
        "故障解决文档11—如何U盘升级PDC",
        {"document_id": document_id},
    )

    assert profile.canonical_question.endswith("？")
    assert "升级" in profile.keywords
    assert profile.task_type == "UPGRADE"
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

    operation, target = ReleaseService._suggest_operation("如何通过U盘升级PDC", profile, [existing])
    assert operation == "UPDATE"
    assert target is existing
    merged = ReleaseService._merge_update(profile, existing)
    assert merged.problem_code == "PDC_UPDATE_UPGRADE"
    assert merged.aliases == ["PDC刷版本"]


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
    assert product_and_intent.decision == ProblemDecision.MATCHED
    assert product_and_intent.method == "DETERMINISTIC_RULE"


class _Result:
    def __init__(self, *, row: dict[str, Any] | None = None, scalar: Any = None) -> None:
        self.row = row
        self.scalar = scalar

    def mappings(self) -> _Result:
        return self

    def one_or_none(self) -> dict[str, Any] | None:
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
async def test_new_candidate_cancels_other_open_releases() -> None:
    knowledge_id = uuid4()
    connection = _Connection()

    await IngestionRepository._cancel_open_releases(cast(AsyncConnection, connection), knowledge_id)

    sql, parameters = connection.executed[0]
    assert "SET status = 'CANCELLED'" in sql
    assert "status NOT IN ('PUBLISHED', 'CANCELLED')" in sql
    assert parameters == {"kb_id": knowledge_id}


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
