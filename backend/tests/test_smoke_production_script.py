"""The production smoke script must never retag the live production images."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "smoke-production.sh"
PRODUCTION_BACKEND = "enterprise-rag-backend:1.0.1"
PRODUCTION_FRONTEND = "enterprise-rag-frontend:1.0.1"

# `docker compose config` fails so the script stops right after the images are
# chosen; the fake records the image variables compose would have received.
FAKE_DOCKER = """#!/usr/bin/env bash
printf 'docker %s | BACKEND_IMAGE=%s FRONTEND_IMAGE=%s\\n' \\
  "$*" "${BACKEND_IMAGE:-}" "${FRONTEND_IMAGE:-}" >> "$TEST_LOG"
[[ " $* " == *" config "* ]] && exit 1
exit 0
"""


def run_smoke(tmp_path: Path, **env: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    log = tmp_path / "docker.log"
    log.touch()
    process = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "TEST_LOG": str(log),
            **env,
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return process, log.read_text().splitlines()


def test_smoke_ignores_caller_and_production_image_names(tmp_path: Path) -> None:
    process, log = run_smoke(
        tmp_path, BACKEND_IMAGE=PRODUCTION_BACKEND, FRONTEND_IMAGE=PRODUCTION_FRONTEND
    )

    assert process.returncode == 1  # fake `compose config` failure, after image selection
    assert log, process.stderr
    for line in log:
        assert PRODUCTION_BACKEND not in line
        assert PRODUCTION_FRONTEND not in line
        assert "BACKEND_IMAGE=enterprise-rag-backend:m7-smoke" in line
        assert "FRONTEND_IMAGE=enterprise-rag-frontend:m7-smoke" in line


@pytest.mark.parametrize("variable", ["SMOKE_BACKEND_IMAGE", "SMOKE_FRONTEND_IMAGE"])
def test_smoke_refuses_non_smoke_image_tag_before_touching_docker(
    tmp_path: Path, variable: str
) -> None:
    process, log = run_smoke(tmp_path, **{variable: PRODUCTION_BACKEND})

    assert process.returncode == 1
    assert "must contain 'smoke'" in process.stderr
    assert log == []
