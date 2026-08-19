from __future__ import annotations

from app.tasks.health import ping


def test_worker_ping_payload() -> None:
    result = ping()

    assert result["status"] == "ok"
    assert result["timestamp"].endswith("+00:00")
