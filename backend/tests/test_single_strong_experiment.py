"""Offline experiment checks; no database, provider, or online policy changes."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from app.core.config import Settings


@pytest.fixture
def experiment() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "rag-acceptance" / "experiment_single_strong.py"
    spec = importlib.util.spec_from_file_location("single_strong_experiment", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("score", [None, "0.9", True, float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_scores_never_become_strong(experiment: ModuleType, score: Any) -> None:
    candidates = [
        {"document_id": "a", "rerank_score": score},
        {"document_id": "b", "rerank_score": 0.1},
    ]
    assert experiment.single_strong(candidates, "PASSED", 0.85, 0.18) is None


@pytest.mark.parametrize("status", ["DEGRADED", "FAILED", None])
def test_requires_successful_rerank(experiment: ModuleType, status: str | None) -> None:
    candidates = [
        {"document_id": "a", "rerank_score": 0.95},
        {"document_id": "b", "rerank_score": 0.1},
    ]
    assert experiment.single_strong(candidates, status, 0.85, 0.18) is None


@pytest.mark.parametrize(
    ("scores", "expected"),
    [
        ([], None),
        ([("a", 0.95)], None),
        ([("a", 0.95), ("a", 0.3)], None),
        ([("a", 0.84), ("b", 0.1)], None),
        ([("a", 0.95), ("b", 0.9)], None),
        ([("a", 0.95), ("b", 0.95)], None),
        ([("a", 0.85), ("b", 0.5)], "a"),
        ([("a", 0.95), ("a", 0.94), ("b", 0.3)], "a"),
        ([("b", 0.3), ("a", 0.95)], "a"),
    ],
)
def test_absolute_strength_and_cross_document_gap(
    experiment: ModuleType, scores: list[tuple[str, float]], expected: str | None
) -> None:
    candidates = [{"document_id": key, "rerank_score": score} for key, score in scores]
    assert experiment.single_strong(candidates, "PASSED", 0.85, 0.18) == expected


@pytest.mark.parametrize(
    ("tolerance", "keys", "forbidden", "expected"),
    [
        ("EXACT", ["升级LRU"], [], "correct_unique"),
        ("ANY_OF", ["升级LRU"], [], "compatible_ambiguous"),
        ("EXACT", ["LRU"], [], "compatible_ambiguous"),
        ("EXACT", ["更换LRU"], [], "incorrect"),
        ("EXACT", ["升级LRU"], ["升级LRU"], "incorrect"),
        ("NONE", [], [], "incorrect"),
        ("EXACT", [], [], "unlabelled"),
    ],
)
def test_ambiguous_labels_are_not_counted_as_unique_success(
    experiment: ModuleType,
    tolerance: str,
    keys: list[str],
    forbidden: list[str],
    expected: str,
) -> None:
    catalog = {"a": "如何升级LRU", "b": "如何更换LRU"}
    case = {
        "expected": {"tolerance": tolerance, "document_keys": keys, "forbidden_keys": forbidden}
    }
    assert experiment.judgement("a", case, catalog) == expected
    assert experiment.judgement("a", None, catalog) == "unlabelled"


def test_baseline_preserves_existing_title_delivery(experiment: ModuleType) -> None:
    document_id = "00000000-0000-0000-0000-000000000001"
    row = {
        "question": "如何升级LRU",
        "rerank_result": {
            "status": "PASSED",
            "candidates": [{"document_id": document_id, "rerank_score": 0.1}],
        },
    }
    assert experiment.baseline(row, {document_id: "如何升级LRU"}, Settings(_env_file=None)) == (
        document_id,
        "TITLE_MATCH",
    )
