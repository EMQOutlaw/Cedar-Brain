"""Workflow model: executors, step states, legal transitions, definition validation.

The same validator checks hand-written workflows (workflows.create) and
model-generated plans (Pathfinder), so a plan can never express more than
a workflow is allowed to.
"""

from __future__ import annotations

import os
import re
from collections import deque
from dataclasses import dataclass

from ..security.paths import resolve_in_project


class StepState:
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


S = StepState
STEP_STATES = (S.PENDING, S.READY, S.RUNNING, S.AWAITING_APPROVAL, S.SUCCEEDED, S.FAILED, S.CANCELLED, S.SKIPPED)
TERMINAL_STEP_STATES = frozenset({S.SUCCEEDED, S.FAILED, S.CANCELLED, S.SKIPPED})
TRANSITIONS: dict[str, frozenset[str]] = {
    S.PENDING: frozenset({S.READY, S.SKIPPED, S.CANCELLED}),
    S.READY: frozenset({S.RUNNING, S.AWAITING_APPROVAL, S.SKIPPED, S.CANCELLED}),
    S.AWAITING_APPROVAL: frozenset({S.RUNNING, S.SKIPPED, S.CANCELLED}),
    S.RUNNING: frozenset({S.SUCCEEDED, S.FAILED, S.CANCELLED}),
    S.SUCCEEDED: frozenset(),
    S.FAILED: frozenset(),
    S.CANCELLED: frozenset(),
    S.SKIPPED: frozenset(),
}


class RunStatus:
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"   # backend stopped mid-run; never resumed automatically
    INCOMPLETE = "incomplete"     # finished without failures, but some steps were skipped (e.g. rejected)


ACTIVE_RUN_STATUSES = frozenset({RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.AWAITING_APPROVAL})
RUN_STATUSES = ("queued", "running", "awaiting_approval", "succeeded", "failed", "cancelled", "interrupted", "incomplete")


class IllegalTransition(Exception):
    pass


def check_transition(old: str, new: str) -> None:
    if new not in TRANSITIONS.get(old, frozenset()):
        raise IllegalTransition(f"illegal step transition {old} → {new}")


# ---------------------------------------------------------------------------
# Executors


@dataclass(frozen=True)
class InputSpec:
    type: type
    required: bool = False
    max_len: int | None = None
    max_items: int | None = None
    min: int | None = None
    max: int | None = None


@dataclass(frozen=True)
class ExecutorSpec:
    id: str
    description: str
    inputs: dict
    modes: tuple[str, ...] = ("read_only",)
    agent_id: str | None = None


EXECUTORS: dict[str, ExecutorSpec] = {
    "agent.claude": ExecutorSpec(
        "agent.claude", "Claude Code CLI, read-only tools (Read, Grep, Glob) in the project root",
        {"prompt": InputSpec(str, required=True, max_len=16000)}, ("read_only",), agent_id="claude"),
    "agent.codex": ExecutorSpec(
        "agent.codex", "Codex CLI. read_only: OS sandbox, read-only. write: OS sandbox limited to a fresh git worktree; requires approval",
        {"prompt": InputSpec(str, required=True, max_len=16000)}, ("read_only", "write"), agent_id="codex"),
    "cortex.search": ExecutorSpec(
        "cortex.search", "Search the project's Graphify graph (deterministic, no model)",
        {"query": InputSpec(str, required=True, max_len=200), "limit": InputSpec(int, min=1, max=100)}),
    "cortex.impact": ExecutorSpec(
        "cortex.impact", "List components that statically depend on a node or file (deterministic)",
        {"node_ids": InputSpec(list, max_items=20), "path": InputSpec(str, max_len=1000),
         "depth": InputSpec(int, min=1, max=4)}),
    "git.status": ExecutorSpec("git.status", "Report branch, HEAD and working-tree cleanliness (read-only)", {}),
}

VALIDATION_METHODS = {
    "exit_status": "the executor finished without error",
    "non_empty_result": "exit_status, and the step produced non-empty output",
    "json_result": "exit_status, and the output is a JSON object or array",
}
FAILURE_POLICIES = ("stop", "continue")
STEP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
MAX_STEPS = 20
STEP_KEYS = {"id", "description", "executor", "inputs", "depends_on", "expected_output",
             "approval_required", "validation", "mode", "on_failure", "timeout_s"}
REQUIRED_STEP_KEYS = ("id", "description", "executor", "inputs", "depends_on", "expected_output",
                      "approval_required", "validation")


def executor_catalog(include_cortex: bool = True) -> list[dict]:
    return [{"id": e.id, "description": e.description, "modes": list(e.modes),
             "inputs": {k: {"type": v.type.__name__, "required": v.required} for k, v in e.inputs.items()}}
            for e in EXECUTORS.values() if include_cortex or not e.id.startswith("cortex.")]


def graph_available(project: dict) -> bool:
    return os.path.isfile(project["graph_path"])


def validate_definition(defn, project_root: str | None, available_agents: set[str] | None = None,
                        max_steps: int = MAX_STEPS, graph_available: bool | None = None,
                        ) -> tuple[dict | None, list[dict], list[dict]]:
    """Validate a workflow/plan body.

    Returns (normalized, errors, unsupported). ``normalized`` is None when
    there are errors. ``unsupported`` lists steps whose executor exists but
    is currently unavailable (e.g. the CLI is not installed); those are
    reported separately so a UI can show them without calling the plan
    malformed.
    """
    errors: list[dict] = []
    unsupported: list[dict] = []

    def bad(step, field, msg):
        errors.append({"step": step, "field": field, "message": msg})

    if not isinstance(defn, dict):
        return None, [{"step": None, "field": None, "message": "definition must be an object"}], []
    extra = set(defn) - {"name", "title", "summary", "steps"}
    if extra:
        bad(None, None, f"unknown top-level keys {sorted(extra)}")
    name = defn.get("name") or defn.get("title")
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        bad(None, "name", "name/title must be a non-empty string of at most 200 chars")
    summary = defn.get("summary", "")
    if not isinstance(summary, str) or len(summary) > 4000:
        bad(None, "summary", "summary must be a string of at most 4000 chars")
    steps = defn.get("steps")
    if not isinstance(steps, list) or not steps:
        bad(None, "steps", "steps must be a non-empty list")
        return None, errors, unsupported
    if len(steps) > max_steps:
        bad(None, "steps", f"at most {max_steps} steps are allowed")
        return None, errors, unsupported

    ids: list[str] = []
    norm_steps: list[dict] = []
    for pos, step in enumerate(steps):
        label = f"#{pos + 1}"
        if not isinstance(step, dict):
            bad(label, None, "step must be an object")
            continue
        sid = step.get("id")
        if isinstance(sid, str) and STEP_ID_RE.match(sid):
            label = sid
            if sid in ids:
                bad(sid, "id", "duplicate step id")
            ids.append(sid)
        else:
            bad(label, "id", "id must match ^[a-z0-9][a-z0-9_-]{0,47}$")
        for key in sorted(set(step) - STEP_KEYS):
            bad(label, key, "unknown step key")
        for key in REQUIRED_STEP_KEYS:
            if key not in step:
                bad(label, key, "missing required key")
        desc = step.get("description")
        if not isinstance(desc, str) or not desc.strip() or len(desc) > 1000:
            bad(label, "description", "must be a non-empty string of at most 1000 chars")
        exp = step.get("expected_output")
        if not isinstance(exp, str) or len(exp) > 1000:
            bad(label, "expected_output", "must be a string of at most 1000 chars")
        approval = step.get("approval_required")
        if not isinstance(approval, bool):
            bad(label, "approval_required", "must be true or false")
        validation = step.get("validation")
        if validation not in VALIDATION_METHODS:
            bad(label, "validation", f"must be one of {sorted(VALIDATION_METHODS)}")
        mode = step.get("mode", "read_only")
        on_failure = step.get("on_failure", "stop")
        if on_failure not in FAILURE_POLICIES:
            bad(label, "on_failure", f"must be one of {list(FAILURE_POLICIES)}")
        timeout = step.get("timeout_s")
        if timeout is not None and (not isinstance(timeout, int) or isinstance(timeout, bool) or not 10 <= timeout <= 7200):
            bad(label, "timeout_s", "must be an integer between 10 and 7200")
        deps = step.get("depends_on")
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps) or len(deps) > max_steps:
            bad(label, "depends_on", "must be a list of step ids")
            deps = []
        executor = EXECUTORS.get(step.get("executor"))
        if executor is None:
            bad(label, "executor", f"unknown executor {step.get('executor')!r}; allowed: {sorted(EXECUTORS)}")
        else:
            if mode not in executor.modes:
                bad(label, "mode", f"executor {executor.id} supports modes {list(executor.modes)}")
            if mode == "write" and approval is not True:
                bad(label, "approval_required", "write steps must require approval")
            _check_inputs(label, executor, step.get("inputs"), project_root, bad)
            if executor.agent_id and available_agents is not None and executor.agent_id not in available_agents:
                unsupported.append({"step": label, "executor": executor.id, "reason": f"agent '{executor.agent_id}' is not available"})
            if executor.id.startswith("cortex.") and graph_available is False:
                unsupported.append({"step": label, "executor": executor.id, "reason": "project has no Graphify graph"})
        norm_steps.append({
            "id": sid, "description": desc, "executor": step.get("executor"), "inputs": step.get("inputs") or {},
            "depends_on": list(dict.fromkeys(deps)), "expected_output": exp, "approval_required": approval,
            "validation": validation, "mode": mode, "on_failure": on_failure, "timeout_s": timeout,
        })

    known = set(ids)
    for step in norm_steps:
        for dep in step["depends_on"]:
            if dep not in known:
                bad(step["id"], "depends_on", f"unknown dependency {dep!r}")
            elif dep == step["id"]:
                bad(step["id"], "depends_on", "a step cannot depend on itself")
    if not errors:
        cycle = find_cycle(norm_steps)
        if cycle:
            bad(None, "depends_on", "dependency cycle: " + " → ".join(cycle))
    if errors:
        return None, errors, unsupported
    return {"name": name.strip(), "summary": summary, "steps": norm_steps}, [], unsupported


def _check_inputs(label, executor: ExecutorSpec, inputs, project_root, bad) -> None:
    if not isinstance(inputs, dict):
        bad(label, "inputs", "must be an object")
        return
    for key in sorted(set(inputs) - set(executor.inputs)):
        bad(label, f"inputs.{key}", f"not accepted by {executor.id}")
    for key, spec in executor.inputs.items():
        value = inputs.get(key)
        if value is None:
            if spec.required:
                bad(label, f"inputs.{key}", "required")
            continue
        if not isinstance(value, spec.type) or isinstance(value, bool):
            bad(label, f"inputs.{key}", f"must be {spec.type.__name__}")
            continue
        if isinstance(value, str) and ((spec.max_len and len(value) > spec.max_len) or "\x00" in value):
            bad(label, f"inputs.{key}", f"must be at most {spec.max_len} chars")
        if isinstance(value, list) and ((spec.max_items and len(value) > spec.max_items) or not all(isinstance(v, str) for v in value)):
            bad(label, f"inputs.{key}", f"must be a list of at most {spec.max_items} strings")
        if isinstance(value, int) and ((spec.min is not None and value < spec.min) or (spec.max is not None and value > spec.max)):
            bad(label, f"inputs.{key}", f"must be between {spec.min} and {spec.max}")
    if executor.id == "cortex.impact" and not inputs.get("node_ids") and not inputs.get("path"):
        bad(label, "inputs", "cortex.impact needs node_ids or path")
    if "path" in inputs and isinstance(inputs["path"], str) and project_root:
        if resolve_in_project(project_root, inputs["path"]) is None:
            bad(label, "inputs.path", "path is outside the project")


def find_cycle(steps: list[dict]) -> list[str] | None:
    """Kahn's algorithm; on failure, walk the remainder to report one cycle."""
    indeg = {s["id"]: len(s["depends_on"]) for s in steps}
    children: dict[str, list[str]] = {s["id"]: [] for s in steps}
    for s in steps:
        for d in s["depends_on"]:
            children[d].append(s["id"])
    queue = deque(sid for sid, n in indeg.items() if n == 0)
    seen = 0
    while queue:
        cur = queue.popleft()
        seen += 1
        for c in children[cur]:
            indeg[c] -= 1
            if indeg[c] == 0:
                queue.append(c)
    if seen == len(steps):
        return None
    deps = {s["id"]: s["depends_on"] for s in steps}
    start = next(sid for sid, n in indeg.items() if n > 0)
    path, cur = [], start
    while cur not in path:
        path.append(cur)
        cur = next(d for d in deps[cur] if indeg.get(d, 0) > 0)
    return path[path.index(cur):] + [cur]
