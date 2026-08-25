# ruff: noqa: RUF001
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.schemas import ChatResponse
from app.common.errors import ApiError
from app.core.config import Settings
from app.support.document_delivery import deliver_document
from app.support.models import (
    DocumentContentBlock,
    EvidenceType,
    FocusedDocument,
    ProblemDecision,
    ProblemProfile,
    RiskLevel,
    SupportDraft,
    SupportTurn,
)
from app.support.problem_resolver import ProblemResolver, normalize_for_match
from app.support.repository import SupportRepository
from app.support.service import TechnicalSupportService
from app.support.validator import validate_draft
from app.support.workflow_state import apply_workflow_update, read_workflow, reset_for_document


def _profile(*, alias: str = "PDC U盘升级") -> ProblemProfile:
    return ProblemProfile(
        id=uuid4(),
        document_id=uuid4(),
        knowledge_id=uuid4(),
        problem_code="PDC_USB_UPGRADE",
        canonical_question="如何使用U盘升级PDC",
        aliases=[alias],
        keywords=["PDC", "升级"],
    )


def test_problem_profile_readiness_reports_all_blocking_reasons() -> None:
    row = {
        "id": uuid4(),
        "document_id": uuid4(),
        "kb_id": uuid4(),
        "problem_code": "PDC_NOT_READY",
        "canonical_question": "未就绪文档",
        "aliases": [],
        "keywords": [],
        "product_name": None,
        "model_names": [],
        "task_type": None,
        "enabled": True,
        "document_status": "STORED",
        "active_index_id": None,
        "document_published": False,
        "images_complete": False,
    }

    profile = SupportRepository._profile(row)

    assert profile.support_ready is False
    assert profile.readiness_reasons == [
        "KNOWLEDGE_BASE_NOT_READY",
        "IMAGE_ASSET_MISSING",
    ]


def _document() -> FocusedDocument:
    return FocusedDocument(
        id=uuid4(),
        title="如何使用U盘升级PDC",
        problem_code="PDC_USB_UPGRADE",
        index_id=uuid4(),
        blocks=[
            DocumentContentBlock(uuid4(), 1, "text", "设备 IP 为 172.16.8.150。"),
            DocumentContentBlock(uuid4(), 2, "table", "步骤表格"),
            DocumentContentBlock(
                uuid4(),
                3,
                "image",
                image_asset_id=uuid4(),
                caption="版本界面",
                mime_type="image/png",
                ocr_text="VERSION: 2.1.4",
            ),
        ],
    )


@pytest.mark.asyncio
async def test_problem_resolver_matches_normalized_alias_and_pending_choice() -> None:
    profile = _profile(alias="ＰＤＣ， U盘 升级！")
    resolver = ProblemResolver()
    assert normalize_for_match("ＰＤＣ， U盘 升级！") == normalize_for_match("pdc u盘升级")
    alias = await resolver.resolve(question="pdc u盘升级", profiles=[profile])
    assert alias.decision == ProblemDecision.MATCHED
    assert alias.method == "ALIAS"
    pending = await resolver.resolve(
        question="确认",
        profiles=[profile],
        choice_id="switch_1",
        pending_options=[{"choice_id": "switch_1", "document_id": str(profile.document_id)}],
    )
    assert pending.method == "PENDING_CHOICE"
    invalid = await resolver.resolve(question="确认", profiles=[profile], choice_id="bad")
    assert invalid.decision == ProblemDecision.NO_MATCH


@pytest.mark.asyncio
async def test_problem_resolver_handles_ambiguity_rules_and_closed_catalog() -> None:
    first, second = _profile(alias="same"), _profile(alias="same")
    ambiguous = await ProblemResolver().resolve(question="same", profiles=[first, second])
    assert ambiguous.decision == ProblemDecision.AMBIGUOUS
    deterministic = await ProblemResolver().resolve(question="请升级PDC", profiles=[first])
    assert deterministic.method == "DETERMINISTIC_RULE"

    async def classifier(_: str, __: list[ProblemProfile]) -> str | None:
        return first.problem_code

    classified = await ProblemResolver(classifier).resolve(
        question="完全不同的问题", profiles=[first]
    )
    assert classified.method == "LLM_CATALOG"


def test_document_delivery_keeps_order_and_optionally_suppresses_images() -> None:
    document = _document()
    content, images = deliver_document(document, include_images=True)
    assert [item["sequence_no"] for item in content] == [1, 2, 3]
    assert [item["type"] for item in content] == ["text", "table", "image"]
    assert images[0]["image_asset_id"] == str(document.blocks[2].image_asset_id)
    # the vision caption ("版本界面") is ingestion enrichment, not document
    # content, so it must not ship under the delivered image
    assert content[2]["caption"] is None
    assert images[0]["caption"] is None
    without_images, hidden_images = deliver_document(document, include_images=False)
    assert [item["sequence_no"] for item in without_images] == [1, 2]
    assert hidden_images == []


def test_document_delivery_keeps_original_alt_text_as_caption() -> None:
    block = DocumentContentBlock(
        uuid4(), 1, "image", "出厂设置界面", image_asset_id=uuid4(), caption="AI 生成的描述"
    )
    document = FocusedDocument(
        id=uuid4(),
        title="t",
        problem_code=None,
        index_id=uuid4(),
        blocks=[block],
    )
    content, _ = deliver_document(document, include_images=True)
    assert content[0]["caption"] == "出厂设置界面"


def test_document_delivery_never_exposes_ai_description_or_ocr_below_image() -> None:
    with_alt = DocumentContentBlock(
        uuid4(),
        1,
        "image",
        "图片替代文本：作者提供的界面名称\n图片描述：AI 生成的界面说明\n图片文字：STATUS OK",
        image_asset_id=uuid4(),
        caption="AI 生成的界面说明",
        ocr_text="STATUS OK",
    )
    enrichment_only = DocumentContentBlock(
        uuid4(),
        2,
        "image",
        "图片描述：AI 生成的第二段说明\n图片文字：ERROR 12",
        image_asset_id=uuid4(),
        caption="AI 生成的第二段说明",
        ocr_text="ERROR 12",
    )
    document = FocusedDocument(
        id=uuid4(),
        title="t",
        problem_code=None,
        index_id=uuid4(),
        blocks=[with_alt, enrichment_only],
    )

    content, images = deliver_document(document, include_images=True)

    assert content[0]["caption"] == "作者提供的界面名称"
    assert content[1]["caption"] is None
    assert images[0]["caption"] == "作者提供的界面名称"
    assert images[1]["caption"] is None
    assert "AI 生成" not in str(content)
    assert "STATUS OK" not in str(content)


def test_focused_prompt_includes_image_ocr_text() -> None:
    """Screenshot text is often the only evidence for a fact; the registry
    line must carry both the vision description and the OCR content."""
    from app.support.prompt import document_registry

    document = _document()
    elements, images, content = document_registry(document)
    image_line = next(line for line in content.splitlines() if "[图片 I1]" in line)
    assert "描述：版本界面" in image_line
    assert "图内文字：VERSION: 2.1.4" in image_line
    assert set(elements) == {"E1", "E2", "E3"}
    assert set(images) == {"I1"}


def test_focused_prompt_image_without_content_falls_back() -> None:
    from app.support.prompt import document_registry

    document = FocusedDocument(
        id=uuid4(),
        title="t",
        problem_code=None,
        index_id=uuid4(),
        blocks=[DocumentContentBlock(uuid4(), 1, "image", image_asset_id=uuid4(), caption=None)],
    )
    _, _, content = document_registry(document)
    assert "（无可识别内容）" in content


def test_focused_prompt_enforces_budget_and_keeps_question_relevant_elements() -> None:
    from app.support.prompt import build_budgeted_focused_prompt

    blocks = [
        DocumentContentBlock(uuid4(), number, "text", f"普通说明{number}" * 120)
        for number in range(1, 8)
    ]
    blocks.append(DocumentContentBlock(uuid4(), 8, "text", "ZX-900 紧急复位步骤" * 20))
    document = FocusedDocument(
        id=uuid4(),
        title="超长维护手册",
        problem_code="RESET",
        index_id=uuid4(),
        blocks=blocks,
    )

    context = build_budgeted_focused_prompt(
        question="ZX-900 如何复位？",
        document=document,
        workflow={},
        history=[{"role": "USER", "content": "较早历史" * 200}],
        token_budget=1_000,
    )

    assert context.usage["estimated_tokens"] <= 1_000
    assert context.usage["truncated"] is True
    assert context.usage["strategy"] == "QUESTION_RELEVANT_COMPLETE_ELEMENTS"
    assert context.usage["document_elements_included"] < len(blocks)
    assert "E8" in context.evidence_registry
    assert "ZX-900 紧急复位步骤" in context.prompt


def test_validator_accepts_ip_from_image_ocr_text() -> None:
    """A model faithfully repeating a screenshot-only IP must not fail with
    UNSUPPORTED_PRODUCT_PARAMETER."""
    document = FocusedDocument(
        id=uuid4(),
        title="t",
        problem_code=None,
        index_id=uuid4(),
        blocks=[
            DocumentContentBlock(
                uuid4(),
                1,
                "image",
                image_asset_id=uuid4(),
                caption="IP 配置界面",
                ocr_text="DEVICE IP: 192.168.3.7",
            )
        ],
    )
    draft = SupportDraft.model_validate(
        {
            "mode": "STEP_GUIDANCE",
            "answer": "x",
            "sections": [
                {
                    "type": "DOCUMENT_FACT",
                    "title": "设备地址",
                    "content": "设备 IP 是 192.168.3.7。",
                    "evidence_element_ids": ["E1"],
                }
            ],
        }
    )
    validated = validate_draft(
        draft,
        document=document,
        evidence_registry={"E1": str(document.blocks[0].element_id)},
        image_registry={},
        question="x",
    )
    assert validated.sections[0].content == "设备 IP 是 192.168.3.7。"


def test_workflow_only_records_explicit_user_updates() -> None:
    context = reset_for_document(str(uuid4()), "PDC_USB_UPGRADE", str(uuid4()))
    updated = apply_workflow_update(
        context,
        SupportDraft.model_validate(
            {
                "mode": "STEP_GUIDANCE",
                "answer": "继续下一步",
                "workflow_update": {
                    "confirmed_completed_steps": [1, 2],
                    "user_observations": ["Telnet 超时"],
                    "collected_information": {"computer_os": "Windows 11"},
                    "missing_information": ["ping 结果", "ping 结果"],
                },
            }
        ).workflow_update,
    )
    state = read_workflow(updated)
    assert state.completed_steps == [1, 2]
    assert state.observations == [{"source": "USER", "value": "Telnet 超时"}]
    assert state.missing_information == ["ping 结果"]


def test_validator_requires_evidence_limits_images_and_blocks_unsupported_risk() -> None:
    document = _document()
    element_id = str(document.blocks[0].element_id)
    image_id = str(document.blocks[2].image_asset_id)
    draft = SupportDraft.model_validate(
        {
            "mode": "DIAGNOSTIC_ASSISTANCE",
            "answer": "请先检查。",
            "sections": [
                {
                    "type": "DOCUMENT_FACT",
                    "title": "地址",
                    "content": "IP 为 172.16.8.150",
                    "evidence_element_ids": ["E1"],
                },
                {"type": "GENERAL_GUIDANCE", "title": "检查", "content": "可以 ping 172.16.8.150"},
                {"type": "GENERAL_GUIDANCE", "title": "风险", "content": "请格式化设备"},
            ],
            "image_ids": ["I1"],
        }
    )
    validated = validate_draft(
        draft,
        document=document,
        evidence_registry={"E1": element_id},
        image_registry={"I1": image_id},
        question="我已完成第一步",
    )
    assert validated.sections[0].evidence_element_ids == [element_id]
    assert validated.sections[1].disclaimer
    assert validated.escalation.required
    assert validated.sections[2].type == EvidenceType.WARNING
    assert validated.sections[2].risk_level == RiskLevel.HIGH

    bad_fact = SupportDraft.model_validate(
        {
            "mode": "STEP_GUIDANCE",
            "answer": "x",
            "sections": [{"type": "DOCUMENT_FACT", "title": "x", "content": "x"}],
        }
    )
    with pytest.raises(ApiError, match="文档事实"):
        validate_draft(
            bad_fact, document=document, evidence_registry={}, image_registry={}, question="x"
        )


def test_validator_failures_carry_review_details() -> None:
    """Each validator rejection must record what the model attempted, so the
    post-launch review page can explain the failure without re-running it."""
    document = _document()
    element_id = str(document.blocks[0].element_id)

    out_of_doc = SupportDraft.model_validate(
        {
            "mode": "STEP_GUIDANCE",
            "answer": "x",
            "sections": [
                {
                    "type": "DOCUMENT_FACT",
                    "title": "越界证据",
                    "content": "x",
                    "evidence_element_ids": ["E99"],
                }
            ],
        }
    )
    with pytest.raises(ApiError) as invalid_evidence:
        validate_draft(
            out_of_doc,
            document=document,
            evidence_registry={"E1": element_id},
            image_registry={},
            question="x",
        )
    details = invalid_evidence.value.details
    assert details["section_title"] == "越界证据"
    assert details["invalid_evidence_ids"] == ["E99"]
    rejected = details["rejected_draft"]
    assert rejected["sections"][0]["title"] == "越界证据"
    assert rejected["sections"][0]["evidence_count"] == 1
    assert rejected["escalation_required"] is False

    bad_ip = SupportDraft.model_validate(
        {
            "mode": "STEP_GUIDANCE",
            "answer": "x",
            "sections": [
                {
                    "type": "GENERAL_GUIDANCE",
                    "title": "改地址",
                    "content": "请改为 10.0.0.99",
                }
            ],
        }
    )
    with pytest.raises(ApiError) as unsupported:
        validate_draft(
            bad_ip, document=document, evidence_registry={}, image_registry={}, question="x"
        )
    assert unsupported.value.details["parameter"] == "10.0.0.99"
    assert unsupported.value.details["section_title"] == "改地址"

    bad_image = SupportDraft.model_validate(
        {
            "mode": "STEP_GUIDANCE",
            "answer": "x",
            "sections": [],
            "image_ids": ["I9"],
        }
    )
    with pytest.raises(ApiError) as invalid_image:
        validate_draft(
            bad_image, document=document, evidence_registry={}, image_registry={}, question="x"
        )
    assert invalid_image.value.details["invalid_image_ids"] == ["I9"]


@pytest.mark.asyncio
async def test_support_stream_returns_sse_error_after_response_starts() -> None:
    service = TechnicalSupportService(None, Settings(_env_file=None, app_env="test"))  # type: ignore[arg-type]

    async def fail(**_: object) -> dict[str, object]:
        raise ApiError("INVALID_EVIDENCE", "模型引用了当前文档之外的证据", 502)

    service.handle = fail  # type: ignore[method-assign]
    frames = [frame.decode() async for frame in service.stream(question="测试")]
    assert frames == [
        'event: status\ndata: {"stage":"support_processing"}\n\n',
        (
            "event: error\ndata: "
            '{"code":"INVALID_EVIDENCE","message":"模型引用了当前文档之外的证据","status_code":502}\n\n'
        ),
    ]


class _ConflictConnection:
    def __init__(self, *, conflict: bool = True) -> None:
        self.statements: list[str] = []
        self.parameters: list[object] = []
        self.conflict = conflict

    async def execute(self, statement: object, parameters: object = None) -> Any:
        sql = str(statement)
        self.statements.append(sql)
        self.parameters.append(parameters)
        has_conflict = self.conflict and "UPDATE conversation" in sql
        return type("Result", (), {"rowcount": 0 if has_conflict else 1})()


class _ConflictEngine:
    def __init__(self, *, conflict: bool = True) -> None:
        self.connection = _ConflictConnection(conflict=conflict)

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[_ConflictConnection]:
        yield self.connection


class _RecordingConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict[str, Any] | None]] = []

    async def execute(self, statement: object, parameters: object = None) -> Any:
        self.executed.append((str(statement), cast(dict[str, Any] | None, parameters)))
        return _ScriptedResult()


class _RecordingEngine:
    def __init__(self) -> None:
        self.connection = _RecordingConnection()

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[_RecordingConnection]:
        yield self.connection


@pytest.mark.asyncio
async def test_fail_turn_persists_full_error_payload() -> None:
    """Support failures must keep stage/retryable/detail like legacy RAG
    fail_turn, otherwise the review page cannot explain upstream errors."""
    engine = _RecordingEngine()
    repository = SupportRepository(engine, Settings(_env_file=None, app_env="test"))  # type: ignore[arg-type]
    turn = SupportTurn(
        trace_id=uuid4(),
        conversation_id=uuid4(),
        user_message_id=uuid4(),
        assistant_message_id=uuid4(),
        knowledge_id=uuid4(),
        index_id=uuid4(),
        question="测试",
        focus_document_id=uuid4(),
        chat_context={},
        context_version=1,
        history=[],
    )

    await repository.fail_turn(
        turn,
        code="UPSTREAM_UNAVAILABLE",
        message="上游暂时不可用",
        trace={"support_version": 1, "failure": {"code": "UPSTREAM_UNAVAILABLE"}},
        stage="llm",
        retryable=True,
        detail="HTTP 529",
    )

    trace_sql, trace_params = engine.connection.executed[1]
    assert "UPDATE rag_trace" in trace_sql
    assert trace_params is not None
    error = json.loads(str(trace_params["error"]))
    assert error == {
        "code": "UPSTREAM_UNAVAILABLE",
        "message": "上游暂时不可用",
        "stage": "llm",
        "retryable": True,
        "detail": "HTTP 529",
    }
    trace_payload = json.loads(str(trace_params["trace"]))
    assert trace_payload["failure"]["code"] == "UPSTREAM_UNAVAILABLE"


@pytest.mark.asyncio
async def test_finish_turn_rejects_stale_workflow_version_without_completing_trace() -> None:
    engine = _ConflictEngine()
    repository = SupportRepository(engine, Settings(_env_file=None, app_env="test"))  # type: ignore[arg-type]
    turn = SupportTurn(
        trace_id=uuid4(),
        conversation_id=uuid4(),
        user_message_id=uuid4(),
        assistant_message_id=uuid4(),
        knowledge_id=uuid4(),
        index_id=uuid4(),
        question="测试",
        focus_document_id=None,
        chat_context={},
        context_version=3,
        history=[],
    )
    with pytest.raises(ApiError, match="另一请求"):
        await repository.finish_turn(
            turn,
            response={
                "answer": "x",
                "references": [],
                "content": [],
                "response_type": "ANSWERED",
                "latency_ms": 1,
            },
            context={},
            focus_document_id=None,
            trace={},
        )
    assert (
        "support_context_version = support_context_version + 1" in engine.connection.statements[1]
    )
    assert len(engine.connection.statements) == 2


class _ScriptedResult:
    def __init__(self, value: Any = None, *, rowcount: int = 1) -> None:
        self.value = value
        self.rowcount = rowcount

    def mappings(self) -> _ScriptedResult:
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        return self.value if isinstance(self.value, dict) else None

    def all(self) -> list[dict[str, Any]]:
        return self.value or []

    def scalar_one(self) -> Any:
        return self.value


class _ScriptedConnection:
    def __init__(self, results: list[_ScriptedResult]) -> None:
        self.results = list(results)
        self.executed: list[tuple[str, dict[str, Any] | None]] = []

    async def execute(
        self, statement: object, parameters: dict[str, Any] | None = None
    ) -> _ScriptedResult:
        self.executed.append((str(statement), parameters))
        return self.results.pop(0) if self.results else _ScriptedResult()


class _ScriptedEngine:
    def __init__(self, results: list[_ScriptedResult]) -> None:
        self.connection = _ScriptedConnection(results)

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[_ScriptedConnection]:
        yield self.connection

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[_ScriptedConnection]:
        yield self.connection


@pytest.mark.asyncio
async def test_profile_query_is_scoped_to_knowledge_base_and_document_owner() -> None:
    knowledge_id = uuid4()
    engine = _ScriptedEngine([_ScriptedResult([])])
    repository = SupportRepository(
        cast(AsyncEngine, engine), Settings(_env_file=None, app_env="test")
    )

    assert await repository.list_profiles(knowledge_id, enabled_only=True) == []

    sql, parameters = engine.connection.executed[0]
    assert "WHERE p.kb_id = :kb_id" in sql
    assert "d.id = p.document_id AND d.kb_id = p.kb_id" in sql
    assert parameters == {"kb_id": knowledge_id}


@pytest.mark.asyncio
async def test_begin_turn_uses_pinned_index_for_focused_conversation() -> None:
    knowledge_id = uuid4()
    active_index_id = uuid4()
    pinned_index_id = uuid4()
    conversation_id = uuid4()
    document_id = uuid4()
    engine = _ScriptedEngine(
        [
            _ScriptedResult(
                {
                    "status": "ENABLED",
                    "active_index_id": active_index_id,
                    "index_status": "ACTIVE",
                    "index_kb_id": knowledge_id,
                }
            ),
            _ScriptedResult(
                {
                    "knowledge_id": knowledge_id,
                    "focus_document_id": document_id,
                    "focus_index_id": pinned_index_id,
                    "focus_index_kb_id": knowledge_id,
                    "chat_context": {"support_version": 1},
                    "support_context_version": 2,
                }
            ),
            _ScriptedResult([]),
            _ScriptedResult(4),
        ]
    )
    repository = SupportRepository(
        cast(AsyncEngine, engine), Settings(_env_file=None, app_env="test")
    )

    turn = await repository.begin_turn(
        knowledge_id=knowledge_id,
        conversation_id=conversation_id,
        question="第三步怎么做",
        request_id="request-1",
        user_id=None,
        idempotency_key=None,
        request_hash="hash",
    )

    assert turn.index_id == pinned_index_id
    assert turn.focus_document_id == document_id
    trace_insert = next(
        parameters
        for sql, parameters in engine.connection.executed
        if "INSERT INTO rag_trace" in sql
    )
    assert trace_insert is not None
    assert trace_insert["index_id"] == pinned_index_id


@pytest.mark.asyncio
async def test_begin_turn_restarts_failed_idempotent_request() -> None:
    knowledge_id = uuid4()
    active_index_id = uuid4()
    engine = _ScriptedEngine(
        [
            _ScriptedResult(
                {
                    "status": "ENABLED",
                    "active_index_id": active_index_id,
                    "index_status": "ACTIVE",
                    "index_kb_id": knowledge_id,
                }
            ),
            _ScriptedResult({"request_hash": "same", "response": None, "status": "FAILED"}),
            _ScriptedResult(),
            _ScriptedResult([]),
            _ScriptedResult(0),
        ]
    )
    repository = SupportRepository(
        cast(AsyncEngine, engine), Settings(_env_file=None, app_env="test")
    )

    turn = await repository.begin_turn(
        knowledge_id=knowledge_id,
        conversation_id=None,
        question="如何升级",
        request_id="request-2",
        user_id=None,
        idempotency_key="retry-key",
        request_hash="same",
    )

    assert turn.index_id == active_index_id
    idempotency_writes = [
        (sql, parameters)
        for sql, parameters in engine.connection.executed
        if "support_idempotency" in sql and sql.lstrip().startswith(("UPDATE", "INSERT"))
    ]
    assert len(idempotency_writes) == 1
    sql, parameters = idempotency_writes[0]
    assert "UPDATE support_idempotency" in sql
    assert "status = 'RUNNING'" in sql
    assert parameters is not None
    assert parameters["key"] == "retry-key"


@pytest.mark.asyncio
async def test_finish_turn_pins_and_clears_focus_index_with_document() -> None:
    engine = _ConflictEngine(conflict=False)
    repository = SupportRepository(
        cast(AsyncEngine, engine), Settings(_env_file=None, app_env="test")
    )
    turn = SupportTurn(
        trace_id=uuid4(),
        conversation_id=uuid4(),
        user_message_id=uuid4(),
        assistant_message_id=uuid4(),
        knowledge_id=uuid4(),
        index_id=uuid4(),
        question="测试",
        focus_document_id=None,
        chat_context={},
        context_version=0,
        history=[],
    )
    document_id = uuid4()

    await repository.finish_turn(
        turn,
        response={
            "answer": "x",
            "references": [],
            "content": [],
            "response_type": "DOCUMENT_DELIVERED",
            "latency_ms": 1,
        },
        context={},
        focus_document_id=document_id,
        trace={},
    )

    conversation_sql = engine.connection.statements[1]
    conversation_parameters = engine.connection.parameters[1]
    assert "focus_index_id = :focus_index" in conversation_sql
    assert isinstance(conversation_parameters, dict)
    assert conversation_parameters["focus"] == document_id
    assert conversation_parameters["focus_index"] == turn.index_id


class _SupportServiceRepository:
    def __init__(
        self,
        *,
        knowledge_id: Any,
        profile: ProblemProfile | None,
        document: FocusedDocument,
    ) -> None:
        self.knowledge_id = knowledge_id
        self.profile = profile
        self.profiles = [profile] if profile is not None else []
        self.document = document
        self.chat_context: dict[str, Any] = {}
        self.finished: dict[str, Any] | None = None

    @staticmethod
    def request_hash(**_: Any) -> str:
        return "request-hash"

    async def begin_turn(self, **kwargs: Any) -> SupportTurn:
        return SupportTurn(
            trace_id=uuid4(),
            conversation_id=kwargs.get("conversation_id") or uuid4(),
            user_message_id=uuid4(),
            assistant_message_id=uuid4(),
            knowledge_id=self.knowledge_id,
            index_id=self.document.index_id,
            question=kwargs["question"],
            focus_document_id=None,
            chat_context=self.chat_context,
            context_version=0,
            history=[],
            idempotency_key=kwargs.get("idempotency_key"),
        )

    async def list_profiles(self, knowledge_id: Any, **__: Any) -> list[ProblemProfile]:
        assert knowledge_id == self.knowledge_id
        return self.profiles

    async def get_profile_by_code(self, *_: Any, **__: Any) -> ProblemProfile | None:
        return self.profile

    async def get_profile_by_document(self, *_: Any, **__: Any) -> ProblemProfile | None:
        return self.profile

    async def get_document(self, **_: Any) -> FocusedDocument:
        return self.document

    async def finish_turn(self, turn: SupportTurn, **kwargs: Any) -> None:
        self.finished = {"turn": turn, **kwargs}

    async def fail_turn(self, *_: Any, **__: Any) -> None:
        raise AssertionError("successful service tests must not fail the turn")

    async def set_stage(self, *_: Any, **__: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_support_service_delivers_complete_document_without_llm() -> None:
    knowledge_id = uuid4()
    profile = _profile()
    profile.knowledge_id = knowledge_id
    document = _document()
    profile.document_id = document.id
    repository = _SupportServiceRepository(
        knowledge_id=knowledge_id,
        profile=profile,
        document=document,
    )
    service = TechnicalSupportService(
        cast(SupportRepository, repository), Settings(_env_file=None, app_env="test")
    )

    response = await service.handle(
        knowledge_id=knowledge_id,
        conversation_id=None,
        question="如何升级",
        request_id="request-3",
        problem_code=profile.problem_code,
    )

    assert response["response_type"] == "DOCUMENT_DELIVERED"
    ChatResponse.model_validate(response)
    assert response["routing"] == {"method": "PROBLEM_CODE", "confidence": 1.0}
    assert [block["sequence_no"] for block in response["content"]] == [1, 2, 3]
    assert response["images"][0]["image_asset_id"] == str(document.blocks[2].image_asset_id)
    assert repository.finished is not None
    assert repository.finished["focus_document_id"] == document.id
    assert repository.finished["context"]["focus"]["index_id"] == str(document.index_id)


@pytest.mark.asyncio
async def test_support_document_delivery_failure_records_delivery_stage() -> None:
    knowledge_id = uuid4()
    profile = _profile()
    profile.knowledge_id = knowledge_id
    document = _document()
    profile.document_id = document.id

    class _FailingDeliveryRepository(_SupportServiceRepository):
        def __init__(self) -> None:
            super().__init__(knowledge_id=knowledge_id, profile=profile, document=document)
            self.failed: dict[str, Any] | None = None

        async def get_document(self, **_: Any) -> FocusedDocument:
            raise ApiError("DOCUMENT_NOT_FOUND", "文档内容不存在", 404)

        async def fail_turn(self, *_: Any, **kwargs: Any) -> None:
            self.failed = kwargs

    repository = _FailingDeliveryRepository()
    service = TechnicalSupportService(
        cast(SupportRepository, repository), Settings(_env_file=None, app_env="test")
    )

    with pytest.raises(ApiError, match="文档内容不存在"):
        await service.handle(
            knowledge_id=knowledge_id,
            conversation_id=None,
            question="如何升级",
            request_id="request-delivery-failure",
            problem_code=profile.problem_code,
        )

    assert repository.failed is not None
    assert repository.failed["stage"] == "DOCUMENT_DELIVERY"


@pytest.mark.asyncio
async def test_support_service_uses_only_profiles_from_selected_knowledge_base() -> None:
    selected_knowledge_id = uuid4()
    other_knowledge_id = uuid4()
    selected_profile = _profile(alias="相同入口问题")
    other_profile = _profile(alias="相同入口问题")
    selected_profile.knowledge_id = selected_knowledge_id
    other_profile.knowledge_id = other_knowledge_id
    selected_document = _document()
    other_document = _document()
    selected_document.title = "知识库 A 文档"
    other_document.title = "知识库 B 文档"
    selected_profile.document_id = selected_document.id
    other_profile.document_id = other_document.id

    class _MultiKnowledgeRepository(_SupportServiceRepository):
        async def list_profiles(self, knowledge_id: Any, **__: Any) -> list[ProblemProfile]:
            return {
                selected_knowledge_id: [selected_profile],
                other_knowledge_id: [other_profile],
            }[knowledge_id]

        async def get_document(self, **kwargs: Any) -> FocusedDocument:
            assert kwargs["knowledge_id"] == selected_knowledge_id
            assert kwargs["document_id"] == selected_document.id
            return selected_document

    repository = _MultiKnowledgeRepository(
        knowledge_id=selected_knowledge_id,
        profile=selected_profile,
        document=selected_document,
    )
    service = TechnicalSupportService(
        cast(SupportRepository, repository), Settings(_env_file=None, app_env="test")
    )

    response = await service.handle(
        knowledge_id=selected_knowledge_id,
        conversation_id=None,
        question="相同入口问题",
        request_id="request-kb-isolation",
    )

    assert response["document"]["id"] == str(selected_document.id)
    assert response["document"]["title"] == "知识库 A 文档"


@pytest.mark.asyncio
async def test_support_stream_emits_trace_and_incremental_validated_answer() -> None:
    service = TechnicalSupportService(None, Settings(_env_file=None, app_env="test"))  # type: ignore[arg-type]
    result = {
        "trace_id": str(uuid4()),
        "conversation_id": str(uuid4()),
        "answer": "这是一个经过校验并分段发送的 Support 回答。" * 3,
    }

    async def succeed(**_: object) -> dict[str, object]:
        return result

    service.handle = succeed  # type: ignore[method-assign]
    frames = [frame.decode() async for frame in service.stream(question="测试")]

    assert frames[0].startswith("event: status")
    assert frames[1].startswith("event: trace")
    message_frames = [frame for frame in frames if frame.startswith("event: message")]
    assert len(message_frames) >= 2
    assert frames[-1].startswith("event: done")


@pytest.mark.asyncio
async def test_support_service_returns_no_match_without_fabricating_document() -> None:
    knowledge_id = uuid4()
    repository = _SupportServiceRepository(
        knowledge_id=knowledge_id,
        profile=None,
        document=_document(),
    )
    service = TechnicalSupportService(
        cast(SupportRepository, repository), Settings(_env_file=None, app_env="test")
    )

    response = await service.handle(
        knowledge_id=knowledge_id,
        conversation_id=None,
        question="完全未知的问题",
        request_id="request-4",
    )

    assert response["response_type"] == "NO_MATCH"
    ChatResponse.model_validate(response)
    assert response["content"] == []
    assert response["references"] == []
    assert response["clarification"]["type"] == "NEED_MORE_INFORMATION"
    assert repository.finished is not None
    assert repository.finished["focus_document_id"] is None


@pytest.mark.asyncio
async def test_support_service_requires_choice_for_ambiguous_catalog_match() -> None:
    knowledge_id = uuid4()
    first = _profile(alias="相同问题")
    second = _profile(alias="相同问题")
    first.knowledge_id = second.knowledge_id = knowledge_id
    repository = _SupportServiceRepository(
        knowledge_id=knowledge_id,
        profile=first,
        document=_document(),
    )
    repository.profiles = [first, second]
    service = TechnicalSupportService(
        cast(SupportRepository, repository), Settings(_env_file=None, app_env="test")
    )

    response = await service.handle(
        knowledge_id=knowledge_id,
        conversation_id=None,
        question="相同问题",
        request_id="request-5",
    )

    assert response["response_type"] == "CLARIFICATION_REQUIRED"
    assert response["clarification"]["type"] == "DOCUMENT_CHOICE"
    assert len(response["clarification"]["choices"]) == 2
    assert repository.finished is not None
    assert repository.finished["focus_document_id"] is None
    assert len(repository.finished["context"]["pending_options"]) == 2


@pytest.mark.asyncio
async def test_support_service_delivers_only_a_pending_confirmed_choice() -> None:
    knowledge_id = uuid4()
    profile = _profile()
    profile.knowledge_id = knowledge_id
    document = _document()
    profile.document_id = document.id
    repository = _SupportServiceRepository(
        knowledge_id=knowledge_id,
        profile=profile,
        document=document,
    )
    repository.chat_context = {
        "support_version": 1,
        "pending_options": [
            {
                "choice_id": "match_1",
                "document_id": str(document.id),
                "problem_code": profile.problem_code,
                "title": profile.canonical_question,
            }
        ],
    }
    service = TechnicalSupportService(
        cast(SupportRepository, repository), Settings(_env_file=None, app_env="test")
    )

    response = await service.handle(
        knowledge_id=knowledge_id,
        conversation_id=uuid4(),
        question="确认第一个",
        request_id="request-6",
        choice_id="match_1",
    )

    assert response["response_type"] == "DOCUMENT_DELIVERED"
    assert response["routing"]["method"] == "PENDING_CHOICE"
    assert repository.finished is not None
    assert repository.finished["focus_document_id"] == document.id
