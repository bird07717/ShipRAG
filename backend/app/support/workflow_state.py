from __future__ import annotations

from typing import Any
from uuid import UUID

from app.support.models import WorkflowState, WorkflowUpdate

_CONTEXT_SCHEMA_VERSION = 2


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def normalize_context(
    context: dict[str, Any] | None,
    *,
    focus_epoch: int | None = None,
    document_id: UUID | str | None = None,
    index_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Return the compact V1 context schema while accepting legacy workflow JSON.

    Pending document choices deliberately do not live here. They have their own
    conversation column and are validated against the focus epoch by Support.
    """
    raw = _as_dict(context)
    workflow_raw = _as_dict(raw.get("support_workflow"))
    if not workflow_raw:
        workflow_raw = _as_dict(raw.get("workflow"))
    # ``pending_options`` used to be embedded in WorkflowState. It is routing
    # state now and must never be persisted in the model-facing workflow.
    workflow = WorkflowState.model_validate(workflow_raw).model_dump(
        mode="json", exclude={"pending_options"}
    )
    existing_focus = _as_dict(raw.get("focus"))
    resolved_epoch = focus_epoch
    if resolved_epoch is None:
        raw_epoch = workflow_raw.get("focus_epoch")
        resolved_epoch = raw_epoch if isinstance(raw_epoch, int) and raw_epoch >= 0 else 0
    resolved_document_id = document_id or existing_focus.get("document_id")
    resolved_index_id = index_id or existing_focus.get("index_id")
    support_workflow: dict[str, Any] = {
        "focus_epoch": resolved_epoch,
        "document_id": str(resolved_document_id) if resolved_document_id else None,
        "index_id": str(resolved_index_id) if resolved_index_id else None,
        **workflow,
    }
    discovery = _as_dict(raw.get("discovery"))
    return {
        "schema_version": _CONTEXT_SCHEMA_VERSION,
        "support_workflow": support_workflow,
        "discovery": {
            "last_clarification_question": (
                str(discovery["last_clarification_question"])
                if discovery.get("last_clarification_question")
                else None
            ),
            "collected_information": _as_dict(discovery.get("collected_information")),
        },
    }


def read_workflow(context: dict[str, Any]) -> WorkflowState:
    normalized = normalize_context(context)
    raw = normalized["support_workflow"]
    return WorkflowState.model_validate(raw if isinstance(raw, dict) else {})


def apply_workflow_update(context: dict[str, Any], update: WorkflowUpdate) -> dict[str, Any]:
    """Apply only user-confirmed facts; model guesses never become observations."""
    next_context = normalize_context(context)
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
    workflow = dict(next_context["support_workflow"])
    workflow.update(state.model_dump(mode="json", exclude={"pending_options"}))
    next_context["support_workflow"] = workflow
    return next_context


def reset_for_document(document_id: str, problem_code: str, index_id: str) -> dict[str, Any]:
    del problem_code  # Profile metadata belongs to the response/trace, not workflow memory.
    return normalize_context(
        {},
        document_id=document_id,
        index_id=index_id,
    )


def discovery_context(
    context: dict[str, Any], *, last_clarification_question: str | None = None
) -> dict[str, Any]:
    """Store only deterministic routing hints for the next Discovery turn."""
    normalized = normalize_context(context)
    discovery = dict(normalized["discovery"])
    if last_clarification_question is not None:
        discovery["last_clarification_question"] = last_clarification_question
    normalized["discovery"] = discovery
    return normalized
