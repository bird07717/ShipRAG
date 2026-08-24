from __future__ import annotations

from typing import Any


def build_escalation(
    *, reason: str, problem_code: str | None, workflow: dict[str, Any], trace_id: str
) -> dict[str, Any]:
    state = workflow.get("workflow", {}) if isinstance(workflow, dict) else {}
    return {
        "required": True,
        "reason": reason,
        "summary": {
            "problem_code": problem_code,
            "current_step": state.get("current_step"),
            "completed_steps": state.get("completed_steps", []),
            "observations": [
                item.get("value")
                for item in state.get("observations", [])
                if item.get("source") == "USER"
            ],
            "checks_performed": state.get("completed_steps", []),
            "missing_information": state.get("missing_information", []),
        },
        "trace_id": trace_id,
    }
