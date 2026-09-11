from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture
def experiment(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "rag-acceptance"))
    return importlib.import_module("experiment_document_rerank")


def test_packing_deduplicates_and_bounds_evidence(experiment: ModuleType) -> None:
    candidates = [{"document_id": "a", "chunk_id": str(n)} for n in range(5)]
    chunks = {str(n): {"search_text": "same" if n < 2 else str(n) * 20} for n in range(5)}
    packed = experiment.pack_documents(
        candidates, chunks, {"a": "new title"}, max_chunks=2, max_chars=12
    )
    assert packed == [{"document_id": "a", "text": "文档标题：new title\nsame\n\n222222"}]


def test_same_titles_do_not_merge_document_ids(experiment: ModuleType) -> None:
    candidates = [{"document_id": key, "chunk_id": key} for key in ["a", "b"]]
    result = experiment.pack_documents(
        candidates, {key: {"search_text": key} for key in ["a", "b"]}, {"a": "same", "b": "same"}
    )
    assert [item["document_id"] for item in result] == ["a", "b"]


def test_diversity_keeps_relevance_order_and_limits(experiment: ModuleType) -> None:
    candidates = [{"document_id": key} for key in ["a", "a", "a", "b", "c"]]
    assert experiment.diversify(candidates, limit=2) == [{"document_id": "a"}, {"document_id": "b"}]
    assert experiment.diversify([], limit=2) == []


@pytest.mark.parametrize(
    ("present", "expected"),
    [
        ([], "ABSENT_FROM_RECORDED_RECALL"),
        (["vector"], "REMOVED_BEFORE_RERANK"),
        (["bm25"], "REMOVED_BEFORE_RERANK"),
        (["vector", "fusion"], "REMOVED_FROM_RERANK_TOP5"),
        (["fusion"], "REMOVED_FROM_RERANK_TOP5"),
        (["fusion", "rerank"], "PRESENT"),
        (["vector", "fusion", "rerank"], "PRESENT"),
    ],
)
def test_stage_diagnosis_preserves_synonym_expansion(
    experiment: ModuleType, present: list[str], expected: str
) -> None:
    stages = {
        key: [{"document_id": "a"}] if key in present else []
        for key in ["vector", "bm25", "fusion", "rerank"]
    }
    assert experiment.classify_stage({"a"}, stages) == expected
    assert experiment.classify_stage(set(), stages) == "NO_UNIQUE_TARGET"


@pytest.mark.parametrize(
    ("chosen", "scores", "expected"),
    [
        ("b", [("a", 0.89), ("b", 0.74), ("b", 0.71)], True),
        ("a", [("a", 0.89), ("b", 0.74)], False),
        ("b", [("a", 0.84), ("b", 0.7)], False),
        ("b", [("a", 0.89), ("b", 0.81)], False),
        (None, [("a", 0.89), ("b", 0.74)], False),
        ("b", [("a", float("nan")), ("b", 0.7)], False),
        ("b", [("a", float("inf")), ("b", 0.7)], False),
        ("b", [("a", True), ("b", 0.7)], False),
        ("b", [], False),
    ],
)
def test_conflict_veto_requires_strong_disagreement(
    experiment: ModuleType, chosen: str | None, scores: list[tuple[str, float]], expected: bool
) -> None:
    candidates = [{"document_id": key, "rerank_score": score} for key, score in scores]
    assert experiment.strong_conflict(chosen, candidates) is expected


def test_new_questions_are_disjoint_from_golden_first_turns(experiment: ModuleType) -> None:
    root = Path(__file__).resolve().parents[2] / "rag-acceptance"
    cases = [json.loads(line) for line in (root / "cases.jsonl").read_text().splitlines()]
    known = {experiment.normalize((case.get("turns") or [case])[0]["question"]) for case in cases}
    heldout = json.loads((root / "document-rerank-heldout.json").read_text())
    assert len(heldout) == 17
    assert all(experiment.normalize(case["question"]) not in known for case in heldout)
    for case in heldout:
        if case["category"] == "noise":
            assert experiment._input_guard_reason(case["question"]) is not None


@pytest.mark.parametrize(
    ("chosen", "scores", "expected"),
    [
        ("b", [("a", 0.752), ("b", 0.714)], True),
        ("b", [("a", 0.89), ("b", 0.74)], True),
        ("b", [("a", 0.75), ("b", 0.75)], False),
        ("a", [("a", 0.89), ("b", 0.74)], False),
        ("b", [("a", float("nan")), ("b", 0.7)], False),
        (None, [("a", 0.89), ("b", 0.74)], False),
    ],
)
def test_conflict_veto_does_not_require_high_confidence_to_abstain(
    experiment: ModuleType, chosen: str | None, scores: list[tuple[str, float]], expected: bool
) -> None:
    candidates = [{"document_id": key, "rerank_score": score} for key, score in scores]
    assert experiment.ranking_conflict(chosen, candidates) is expected
