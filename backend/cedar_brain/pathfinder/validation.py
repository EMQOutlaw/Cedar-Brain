"""Plan validation. A planner response is a proposal; this decides if it is even well-formed."""

from __future__ import annotations

from ..hyphae.executor import extract_json
from ..hyphae.models import validate_definition

MAX_PLAN_STEPS = 12


def parse_plan_text(text: str) -> tuple[dict | None, str | None]:
    if not text or not text.strip():
        return None, "planner returned no output"
    try:
        value = extract_json(text)
    except ValueError:
        return None, "planner output did not contain a JSON object"
    if not isinstance(value, dict):
        return None, "planner output must be a JSON object"
    return value, None


def validate_plan(plan, project_root: str | None, available_agents: set[str] | None,
                  max_steps: int = MAX_PLAN_STEPS, graph_available: bool | None = None) -> dict:
    normalized, errors, unsupported = validate_definition(plan, project_root, available_agents, max_steps=max_steps,
                                                          graph_available=graph_available)
    if isinstance(plan, dict) and not (plan.get("title") or plan.get("name")):
        errors = errors or [{"step": None, "field": "title", "message": "title is required"}]
    return {
        "valid": not errors,
        "errors": errors,
        "unsupported_steps": unsupported,
        "executable": not errors and not unsupported,
        "plan": normalized,
    }
