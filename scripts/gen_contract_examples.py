#!/usr/bin/env python3
"""Regenerate contracts/examples/ and the mock-server fixtures from a REAL server run.

Uses the fake agent CLIs in tests/fakes and a throwaway git repo, so no
model is invoked. Paths are rewritten to neutral placeholders.

    .venv/bin/python scripts/gen_contract_examples.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "tests" / "backend")]

from conftest import FAKES, ServerThread, git, sample_graph  # noqa: E402

from cedar_brain.config import AgentConfig, Config  # noqa: E402

EXAMPLES = ROOT / "contracts" / "examples"
FIXTURES = ROOT / "backend" / "cedar_brain" / "fixtures"


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="cbgen-", dir="/tmp"))
    try:
        repo = work / "cedar-shell"
        (repo / "src").mkdir(parents=True)
        git(repo, "init", "-q", "-b", "main")
        for name in ("StrataGeometry.js", "Canopy.qml", "Bar.qml", "NotificationService.qml", "Whispers.qml"):
            (repo / "src" / name).write_text(f"// {name}\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "init")
        (repo / "graphify-out").mkdir()
        (repo / "graphify-out" / "graph.json").write_text(json.dumps(sample_graph(git(repo, "rev-parse", "HEAD"))))
        (repo / ".git" / "info" / "exclude").write_text("graphify-out/\n")
        skills = work / "skills" / "graph-reader"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text("---\nname: graph-reader\ndescription: Reads Graphify graphs.\nversion: 1.0.0\n---\n# Use\nRead the graph.\n")
        cfg = Config(skill_dirs=[work / "skills"], import_roots=[work],
                     agents={"claude": AgentConfig(executable=str(FAKES / "claude")),
                             "codex": AgentConfig(executable=str(FAKES / "codex"))},
                     state_dir=work / "state", socket_path=work / "run" / "brain.sock", default_step_timeout_s=30)
        server = ServerThread(cfg).start()
        try:
            record(server, repo, work)
        finally:
            server.stop()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def scrub(obj, work: Path):
    text = json.dumps(obj)
    for real, fake in ((str(FAKES / "claude"), "/usr/bin/claude"), (str(FAKES / "codex"), "/usr/bin/codex"),
                       (str(work / "state"), "/home/user/.local/state/cedar-brain"),
                       (str(work / "skills"), "/home/user/.claude/skills"), (str(work), "/home/user/src")):
        text = text.replace(real, fake)
    return json.loads(text)


def record(server: ServerThread, repo: Path, work: Path) -> None:
    examples: dict[str, list] = {}
    responses: dict = {}
    c = server.client(timeout=60)

    def ex(method, params=None, note=None, keep=True):
        params = params or {}
        resp = c.request(method, params)
        req = {"v": 1, "id": f"req-{len(examples) + 1:03d}", "method": method, "params": params}
        resp = {**resp, "id": req["id"]}
        examples.setdefault(method, []).append(scrub({"note": note, "request": req, "response": resp}, work))
        if keep and resp.get("ok") and method not in responses:
            responses[method] = scrub(resp["result"], work)
        return resp.get("result")

    ex("system.health")
    ex("system.capabilities")
    ex("events.subscribe", {"topics": ["run", "plan", "project"]})
    ex("projects.list", note="before any project is registered", keep=False)
    p = ex("projects.add", {"path": str(repo)})
    ex("projects.add", {"path": str(work / "missing")}, note="error: not a git repository")
    ex("projects.list")
    ex("projects.select", {"project_id": p["id"]})
    ex("projects.status")
    ex("cortex.status")
    ex("cortex.search", {"query": "StrataGeometry"})
    ex("cortex.dependencies", {"node_id": "strata_geometry", "direction": "in", "depth": 2})
    ex("cortex.impact", {"path": "src/NotificationService.qml"})
    ex("cortex.node", {"node_id": "canopy"})
    ex("cortex.communities")
    ex("cortex.reload")
    ex("cortex.dependencies", {"node_id": "nope"}, note="error: unknown node")
    sk = ex("skills.list", {"refresh": True})
    ex("skills.inspect", {"skill_id": sk["skills"][0]["id"]})
    ex("skills.import_local", {"path": "/etc"}, note="error: path outside approved locations")
    ex("skills.import_local", {"path": str(work / "skills" / "graph-reader")})
    ex("agents.list")
    ex("agents.status", {"agent_id": "codex"})

    plan = ex("plans.create", {"objective": "Summarise how Canopy renders strata FAKE_PLAN"})
    for _ in range(200):
        if c.call("plans.get", {"plan_id": plan["id"]})["status"] != "generating":
            break
        time.sleep(0.05)
    ex("plans.get", {"plan_id": plan["id"]})
    bad = {"title": "bad", "steps": [{"id": "a", "description": "x", "executor": "shell.exec", "inputs": {},
                                      "depends_on": ["a"], "expected_output": "", "approval_required": False,
                                      "validation": "exit_status"}]}
    ex("plans.validate", {"plan": bad}, note="invalid plan: unknown executor", keep=False)
    good = c.call("plans.get", {"plan_id": plan["id"]})["plan"]
    ex("plans.validate", {"plan": good})
    wf = ex("workflows.create", {"plan_id": plan["id"]})
    gated = ex("workflows.create", {"definition": {"name": "Gated codex edit", "steps": [
        {"id": "edit", "description": "Write a file in a worktree", "executor": "agent.codex", "mode": "write",
         "inputs": {"prompt": "Add a CHANGELOG entry FAKE_WRITE=CHANGELOG.md"}, "depends_on": [],
         "expected_output": "file written", "approval_required": True, "validation": "exit_status"}]}},
        note="hand-written workflow with an approval-gated write step", keep=False)
    ex("workflows.list")
    ex("workflows.get", {"workflow_id": wf["id"]})
    ex("runs.start", {"workflow_id": wf["id"], "approve": False}, note="error: approve must be true")

    c.events.clear()
    t0 = time.monotonic()
    stamps: list[tuple[float, dict]] = []
    run = ex("runs.start", {"workflow_id": wf["id"], "approve": True})
    while True:
        e = c.wait_event(lambda e: True, 30)
        stamps.append((time.monotonic() - t0, e))
        if e["event"] == "run.updated" and e["data"]["status"] in ("succeeded", "failed", "cancelled", "incomplete"):
            break
    ex("runs.get", {"run_id": run["id"]})
    ex("runs.events", {"run_id": run["id"], "after_seq": 0, "limit": 5})
    ex("runs.list")

    r2 = c.call("runs.start", {"workflow_id": gated["id"], "approve": True})
    for _ in range(200):
        if c.call("runs.get", {"run_id": r2["id"]})["status"] == "awaiting_approval":
            break
        time.sleep(0.05)
    ex("runs.get", {"run_id": r2["id"]}, note="a run waiting for step approval", keep=False)
    ex("runs.approve", {"run_id": r2["id"], "step_id": "edit", "approve": False})
    long_wf = c.call("workflows.create", {"definition": {"name": "Long analysis", "steps": [
        {"id": "think", "description": "Long read-only analysis", "executor": "agent.claude",
         "inputs": {"prompt": "FAKE_SLEEP=60"}, "depends_on": [], "expected_output": "notes",
         "approval_required": False, "validation": "non_empty_result"}]}})
    r3 = c.call("runs.start", {"workflow_id": long_wf["id"], "approve": True})
    time.sleep(0.6)
    ex("runs.cancel", {"run_id": r3["id"]})
    ex("projects.set_graph", {"project_id": p["id"], "graph_path": None}, note="reset to <root>/graphify-out/graph.json")
    ex("no.such_method", note="error: unknown method", keep=False)
    c.close()

    # raw protocol errors (not representable as method calls)
    raw = []
    with server.client() as rc:
        for label, line in (("invalid JSON", b"{oops\n"),
                            ("unsupported version", b'{"v":2,"id":"x-1","method":"system.health","params":{}}\n')):
            rc.send_raw(line)
            raw.append({"note": label, "request_line": line.decode().strip(), "response": rc.read_message()})
    examples["_protocol_errors"] = raw

    EXAMPLES.mkdir(parents=True, exist_ok=True)
    for old in EXAMPLES.glob("*.json"):
        old.unlink()
    for method, items in examples.items():
        (EXAMPLES / f"{method}.json").write_text(json.dumps(items, indent=2, ensure_ascii=False) + "\n")
    # event stream: scripted replay for the mock server
    FIXTURES.mkdir(parents=True, exist_ok=True)
    lines, prev = [], 0.0
    for ts, e in stamps:
        lines.append(json.dumps({"after_ms": max(150, int((ts - prev) * 1000)), "event": e["event"], "data": scrub(e["data"], work)}))
        prev = ts
    (FIXTURES / "event_stream.jsonl").write_text("\n".join(lines) + "\n")
    shutil.copy(FIXTURES / "event_stream.jsonl", EXAMPLES / "mock-event-stream.jsonl")
    (FIXTURES / "responses.json").write_text(json.dumps(responses, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {len(examples)} example files, {len(responses)} mock responses, {len(lines)} stream events")


if __name__ == "__main__":
    main()
