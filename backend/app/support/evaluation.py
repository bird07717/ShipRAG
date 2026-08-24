from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class AcceptanceExpectation(BaseModel):
    response_type: str
    problem_code: str | None
    routing_method: str
    required_element_sequences: list[int] = Field(default_factory=list)
    required_image_sequences: list[int] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    expected_state: str


class AcceptanceCase(BaseModel):
    id: str = Field(pattern=r"^[a-z_]+_[0-9]{3}$")
    case_type: Literal["ROUTING", "NO_MATCH", "FOLLOW_UP", "SAFETY", "DOCUMENT_SWITCH"]
    question: str = Field(min_length=1, max_length=4_000)
    expected: AcceptanceExpectation


class ActualOutcome(BaseModel):
    response_type: str
    problem_code: str | None = None
    routing_method: str
    element_sequences: list[int] = Field(default_factory=list)
    image_sequences: list[int] = Field(default_factory=list)
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    state: str
    answer: str = ""


class EvaluationFailure(BaseModel):
    case_id: str
    mismatches: list[str]


class EvaluationReport(BaseModel):
    total: int
    passed: int
    failed: int
    pass_rate: float
    failures: list[EvaluationFailure] = Field(default_factory=list)


CaseExecutor = Callable[[AcceptanceCase], Awaitable[ActualOutcome]]


def load_acceptance_cases(path: Path) -> list[AcceptanceCase]:
    """Load a small developer regression suite; this is not a runtime platform dependency."""
    cases: list[AcceptanceCase] = []
    ids: set[str] = set()
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            item = AcceptanceCase.model_validate_json(raw)
        except ValueError as exc:
            raise ValueError(f"invalid acceptance JSONL at line {line_number}: {exc}") from exc
        if item.id in ids:
            raise ValueError(f"duplicate acceptance case id: {item.id}")
        ids.add(item.id)
        cases.append(item)
    if len(cases) < 20:
        raise ValueError("acceptance suite must contain at least 20 cases")
    return cases


def summarize(cases: list[AcceptanceCase]) -> dict[str, int]:
    result: dict[str, int] = {}
    for case in cases:
        result[case.case_type] = result.get(case.case_type, 0) + 1
    return result


async def evaluate_cases(cases: list[AcceptanceCase], executor: CaseExecutor) -> EvaluationReport:
    failures: list[EvaluationFailure] = []
    for case in cases:
        actual = await executor(case)
        expected = case.expected
        mismatches: list[str] = []
        for field, expected_value, actual_value in (
            ("response_type", expected.response_type, actual.response_type),
            ("problem_code", expected.problem_code, actual.problem_code),
            ("routing_method", expected.routing_method, actual.routing_method),
            ("risk_level", expected.risk_level, actual.risk_level),
            ("state", expected.expected_state, actual.state),
        ):
            if expected_value != actual_value:
                mismatches.append(f"{field}: expected={expected_value!r}, actual={actual_value!r}")
        missing_elements = sorted(
            set(expected.required_element_sequences) - set(actual.element_sequences)
        )
        if missing_elements:
            mismatches.append(f"missing_element_sequences={missing_elements}")
        missing_images = sorted(
            set(expected.required_image_sequences) - set(actual.image_sequences)
        )
        if missing_images:
            mismatches.append(f"missing_image_sequences={missing_images}")
        forbidden = [claim for claim in expected.forbidden_claims if claim in actual.answer]
        if forbidden:
            mismatches.append(f"forbidden_claims={forbidden}")
        if mismatches:
            failures.append(EvaluationFailure(case_id=case.id, mismatches=mismatches))
    passed = len(cases) - len(failures)
    return EvaluationReport(
        total=len(cases),
        passed=passed,
        failed=len(failures),
        pass_rate=passed / len(cases) if cases else 0.0,
        failures=failures,
    )


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[3]
    loaded = load_acceptance_cases(root / "support-acceptance" / "cases.jsonl")
    print(json.dumps({"count": len(loaded), "by_type": summarize(loaded)}, ensure_ascii=False))
