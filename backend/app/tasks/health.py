from __future__ import annotations

from datetime import UTC, datetime


def ping() -> dict[str, str]:
    return {"status": "ok", "timestamp": datetime.now(UTC).isoformat()}
