from __future__ import annotations

from typing import Any

from app.support.models import WorkflowState, WorkflowUpdate


def read_workflow(context: dict[str, Any]) -> WorkflowState:
    raw = context.get("workflow", {})
    return WorkflowState.model_validate(raw if isinstance(raw, dict) else {})


def apply_workflow_update(context: dict[str, Any], update: WorkflowUpdate) -> dict[str, Any]:
    """Apply only user-confirmed facts; model guesses never become observations."""
    next_context = dict(context)
    state = read_workflow(next_context)
    completed = set(state.completed_steps)
    completed.update(step for step in update.confirmed_completed_steps if step > 0)
    state.completed_steps = sorted(completed)
    if state.current_step in completed:
        state.current_step = None
    for observation in update.user_observations:
        clean = observation.strip()
        if clean:
            state.observations.append({"source": "USER", "value": clean})
    state.collected_information.update(
        {key: value.strip() for key, value in update.collected_information.items() if value.strip()}
    )
    state.missing_information = list(
        dict.fromkeys(item.strip() for item in update.missing_information if item.strip())
    )
    next_context["support_version"] = 1
    next_context["workflow"] = state.model_dump(mode="json")
    return next_context


def reset_for_document(document_id: str, problem_code: str, index_id: str) -> dict[str, Any]:
    return {
        "support_version": 1,
        "focus": {"document_id": document_id, "problem_code": problem_code, "index_id": index_id},
        "workflow": WorkflowState().model_dump(mode="json"),
    }
