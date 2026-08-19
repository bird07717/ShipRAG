from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

ProbeStatus = Literal["passed", "failed", "blocked"]


@dataclass(slots=True)
class ProbeResult:
    name: str
    status: ProbeStatus
    provider: str
    model: str
    latency_ms: float = 0
    details: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProbeReport:
    results: list[ProbeResult]
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def status(self) -> ProbeStatus:
        if any(result.status == "failed" for result in self.results):
            return "failed"
        if any(result.status == "blocked" for result in self.results):
            return "blocked"
        return "passed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "generated_at": self.generated_at,
            "status": self.status,
            "summary": {
                status: sum(result.status == status for result in self.results)
                for status in ("passed", "failed", "blocked")
            },
            "results": [result.to_dict() for result in self.results],
        }
