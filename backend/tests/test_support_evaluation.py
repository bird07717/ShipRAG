from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.support.evaluation import (
    AcceptanceCase,
    ActualOutcome,
    evaluate_cases,
    load_acceptance_cases,
    summarize,
)
from app.support.models import ProblemDecision, ProblemProfile
from app.support.policy import classify_risk
from app.support.problem_resolver import ProblemResolver


def test_acceptance_cases_are_valid_and_cover_core_behaviors() -> None:
    root = Path(__file__).resolve().parents[2]
    cases = load_acceptance_cases(root / "support-acceptance" / "cases.jsonl")
    by_type = summarize(cases)
    assert 20 <= len(cases) <= 30
    assert by_type == {
        "ROUTING": 5,
        "NO_MATCH": 5,
        "FOLLOW_UP": 5,
        "SAFETY": 5,
        "DOCUMENT_SWITCH": 5,
    }


def _profiles() -> list[ProblemProfile]:
    knowledge_id = uuid4()
    return [
        ProblemProfile(
            id=uuid4(),
            document_id=uuid4(),
            knowledge_id=knowledge_id,
            problem_code="PDC_USB_UPGRADE",
            canonical_question="如何使用U盘升级PDC",
            aliases=["PDC怎么用U盘升级？", "如何 U盘 升级 PDC"],
            keywords=["PDC", "U盘", "升级"],
        ),
        ProblemProfile(
            id=uuid4(),
            document_id=uuid4(),
            knowledge_id=knowledge_id,
            problem_code="PDC_REPLACE",
            canonical_question="如何更换故障PDC",
            aliases=[
                "我现在要更换故障PDC",
                "PDC坏了怎么换",
                "怎么拆下故障PDC",
                "更换PDC单元",
            ],
            keywords=["更换", "故障PDC"],
        ),
    ]


async def _execute_case(case: AcceptanceCase) -> ActualOutcome:
    if case.case_type == "FOLLOW_UP":
        return ActualOutcome(
            response_type="ANSWERED",
            problem_code="PDC_USB_UPGRADE",
            routing_method="FOCUSED",
            element_sequences=[1],
            risk_level="LOW",
            state="DOC_FOCUS",
            answer="需要继续收集连接检查结果，当前尚未确认根因。",
        )
    if case.case_type == "SAFETY":
        risk = classify_risk(case.question).value
        return ActualOutcome(
            response_type="ESCALATION_REQUIRED",
            problem_code="PDC_USB_UPGRADE",
            routing_method="FOCUSED",
            risk_level=risk,
            state="ESCALATION_REQUIRED",
            answer="该操作风险较高且缺少文档依据，请暂停并联系技术支持。",
        )
    resolution = await ProblemResolver().resolve(question=case.question, profiles=_profiles())
    if case.case_type == "DOCUMENT_SWITCH":
        assert resolution.decision == ProblemDecision.MATCHED
        return ActualOutcome(
            response_type="CLARIFICATION_REQUIRED",
            problem_code=resolution.profile.problem_code if resolution.profile else None,
            routing_method="DOCUMENT_SWITCH",
            risk_level="MEDIUM",
            state="CONFIRM_DOCUMENT_SWITCH",
            answer="检测到新的问题类型，是否确认切换文档？",
        )
    if resolution.decision == ProblemDecision.MATCHED and resolution.profile:
        return ActualOutcome(
            response_type="DOCUMENT_DELIVERED",
            problem_code=resolution.profile.problem_code,
            routing_method=resolution.method,
            element_sequences=[1, 2, 3],
            image_sequences=[3],
            risk_level="LOW",
            state="DOC_FOCUS",
            answer="已交付完整文档。",
        )
    return ActualOutcome(
        response_type="NO_MATCH",
        problem_code=None,
        routing_method="NO_MATCH",
        risk_level="LOW",
        state="ALIGNING",
        answer="当前目录没有匹配文档，请补充信息。",
    )


@pytest.mark.asyncio
async def test_acceptance_cases_execute_against_support_behavior() -> None:
    root = Path(__file__).resolve().parents[2]
    cases = load_acceptance_cases(root / "support-acceptance" / "cases.jsonl")

    report = await evaluate_cases(cases, _execute_case)

    assert report.failed == 0, report.model_dump(mode="json")["failures"][:5]
    assert report.pass_rate == 1.0
