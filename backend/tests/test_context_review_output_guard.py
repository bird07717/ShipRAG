"""Report output safety checks; no HTTP, database, or model calls."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("script", "arguments"),
    [
        (
            "run_context_review.py",
            ["--kb-id", "unused", "--label", "test", "--questions", "does-not-exist.jsonl"],
        ),
        ("run_fixed_context_review.py", ["--input", "does-not-exist.json"]),
    ],
)
def test_existing_report_is_rejected_before_reading_input_or_calling_services(
    tmp_path: Path, script: str, arguments: list[str]
) -> None:
    output = tmp_path / "prior-results.json"
    original = b'{"results": "keep the complete baseline"}\n'
    output.write_bytes(original)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "rag-acceptance" / script),
            *arguments,
            "--output",
            str(output),
        ],
        env={**os.environ, "PYTHONPATH": str(ROOT / "backend")},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "Output already exists" in result.stderr
    assert "Traceback" not in result.stderr
    assert output.read_bytes() == original


def test_import_to_new_report_preserves_complete_answer_and_source(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    questions = tmp_path / "questions.jsonl"
    output = tmp_path / "new-report.json"
    response = {"answer": "完整回答正文", "references": [{"document_id": "doc-1"}]}
    original = {
        "knowledge_id": "test-kb",
        "results": [
            {
                "id": "case-1",
                "turns": [{"question": "测试问题", "response": response, "latency_ms": 123}],
            }
        ],
    }
    source.write_text(json.dumps(original), encoding="utf-8")
    questions.write_text(
        json.dumps({"id": "case-1", "focus": "测试", "questions": ["测试问题"]}) + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "rag-acceptance/run_context_review.py"),
            "--kb-id",
            "test-kb",
            "--label",
            "test",
            "--questions",
            str(questions),
            "--import-report",
            str(source),
            "--output",
            str(output),
        ],
        env={**os.environ, "PYTHONPATH": str(ROOT / "backend")},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["results"][0]["turns"][0]["response"] == response
    assert json.loads(source.read_text(encoding="utf-8")) == original
