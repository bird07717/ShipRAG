from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.core import build_info


def test_image_identity_ignores_runtime_environment(tmp_path: Path, monkeypatch: Any) -> None:
    path = tmp_path / "build-info.json"
    expected = {"commit": "a" * 40, "built_at": "2026-10-03T00:00:00Z", "dirty": False}
    path.write_text(json.dumps(expected))
    monkeypatch.setenv("BUILD_COMMIT", "b" * 40)
    assert build_info.read_build_info(path) == expected


@pytest.mark.parametrize(
    "payload",
    [
        "broken",
        "[]",
        "{}",
        '{"commit":"unknown"}',
        json.dumps({"commit": "a" * 40, "built_at": "bad", "dirty": False}),
        json.dumps({"commit": "a" * 40, "built_at": "2026-10-03", "dirty": False}),
        json.dumps({"commit": "a" * 40, "built_at": "2026-10-03T00:00:00Z", "dirty": "false"}),
    ],
)
def test_missing_or_invalid_metadata_is_explicitly_unknown(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "build-info.json"
    assert build_info.read_build_info(path)["commit"] is None
    path.write_text(payload)
    assert build_info.read_build_info(path) == {"commit": None, "built_at": None, "dirty": None}


def test_stamp_and_show_commands(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setattr(build_info, "__file__", str(tmp_path / "build_info.py"))
    monkeypatch.setattr(
        "sys.argv",
        [
            "build-info",
            "--write",
            "--commit",
            "c" * 40,
            "--built-at",
            "2026-10-03T00:00:00Z",
            "--dirty",
            "true",
        ],
    )
    build_info.main()
    assert json.loads(capsys.readouterr().out)["dirty"] is True
    monkeypatch.setattr("sys.argv", ["build-info"])
    build_info.main()
    assert json.loads(capsys.readouterr().out)["commit"] == "c" * 40


def test_management_version_comes_from_running_app_snapshot(client: Any) -> None:
    identity = {"commit": "d" * 40, "built_at": "2026-10-03T00:00:00Z", "dirty": False}
    client.app.state.build_info = identity
    assert client.get("/api/v1/system/info").status_code == 401
    response = client.get("/api/v1/system/info", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 200
    assert response.json()["build"] == identity
    assert "build" not in client.get("/health/live").json()
