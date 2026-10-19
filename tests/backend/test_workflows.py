import os
import time

import pytest

from cedar_brain.hyphae.models import (TRANSITIONS, IllegalTransition, StepState, check_transition,
                                       validate_definition)

from conftest import ServerThread, git, wait_for


def step(sid, executor="git.status", deps=(), **kw):
    s = {"id": sid, "description": f"step {sid}", "executor": executor, "inputs": {}, "depends_on": list(deps),
         "expected_output": "something", "approval_required": False, "validation": "exit_status"}
    if executor.startswith("agent."):
        s["inputs"] = {"prompt": kw.pop("prompt", "Describe the project.")}
        s["validation"] = "non_empty_result"
    s.update(kw)
    return s


# -- model -----------------------------------------------------------------------

def test_transitions():
    check_transition("pending", "ready")
    check_transition("awaiting_approval", "running")
    for bad in (("pending", "running"), ("succeeded", "running"), ("running", "pending"), ("failed", "succeeded")):
        with pytest.raises(IllegalTransition):
            check_transition(*bad)
    terminal = [s for s, nxt in TRANSITIONS.items() if not nxt]
    assert sorted(terminal) == sorted(["succeeded", "failed", "cancelled", "skipped"])


def test_validation_rules(tmp_path):
    root = str(tmp_path)
    ok, errors, _ = validate_definition({"name": "w", "steps": [step("a"), step("b", deps=["a"])]}, root)
    assert ok and not errors
    cases = {
        "cycle": {"name": "w", "steps": [step("a", deps=["b"]), step("b", deps=["a"])]},
        "self": {"name": "w", "steps": [step("a", deps=["a"])]},
        "unknown dep": {"name": "w", "steps": [step("a", deps=["zz"])]},
        "dup": {"name": "w", "steps": [step("a"), step("a")]},
        "executor": {"name": "w", "steps": [step("a", executor="shell.exec")]},
        "write w/o approval": {"name": "w", "steps": [step("a", executor="agent.codex", mode="write")]},
        "claude write": {"name": "w", "steps": [step("a", executor="agent.claude", mode="write", approval_required=True)]},
        "extra input": {"name": "w", "steps": [step("a", inputs={"cmd": "ls"})]},
        "escape path": {"name": "w", "steps": [step("a", executor="cortex.impact", inputs={"path": "../../etc/passwd"})]},
        "too many": {"name": "w", "steps": [step(f"s{i}") for i in range(25)]},
        "bad id": {"name": "w", "steps": [step("Bad ID!")]},
        "no name": {"steps": [step("a")]},
        "unknown key": {"name": "w", "steps": [{**step("a"), "run_as_root": True}]},
    }
    for label, defn in cases.items():
        out, errors, _ = validate_definition(defn, root)
        assert out is None and errors, label
    _, errors, _ = validate_definition(cases["cycle"], root)
    assert "cycle" in errors[0]["message"]


def test_unsupported_reported_separately(tmp_path):
    out, errors, unsupported = validate_definition({"name": "w", "steps": [step("a", executor="agent.codex")]},
                                                   str(tmp_path), available_agents={"claude"})
    assert out and not errors and unsupported[0]["executor"] == "agent.codex"


# -- engine over the real socket -----------------------------------------------------

@pytest.fixture
def ctx(server, repo):
    c = server.client(timeout=60)
    c.call("events.subscribe", {"topics": ["run", "plan"]})
    project = c.call("projects.add", {"path": str(repo)})
    yield server, c, project
    c.close()


def create(c, steps, name="wf"):
    return c.call("workflows.create", {"definition": {"name": name, "steps": steps}})


def wait_run(c, run_id, statuses=("succeeded", "failed", "cancelled", "incomplete", "interrupted"), timeout=40):
    return wait_for(lambda: (r := c.call("runs.get", {"run_id": run_id}))["status"] in statuses and r, timeout)


def test_run_ordered_steps_and_stream(ctx):
    _, c, _ = ctx
    wf = create(c, [step("status"), step("search", executor="cortex.search", deps=["status"], inputs={"query": "Canopy"}),
                    step("analyse", executor="agent.claude", deps=["search"], prompt="Analyse FAKE_SLEEP=0.4")])
    r = c.request("runs.start", {"workflow_id": wf["id"], "approve": False})
    assert r["error"]["code"] == "APPROVAL_REQUIRED"
    run = c.call("runs.start", {"workflow_id": wf["id"], "approve": True})
    assert run["status"] in ("queued", "running")
    done = wait_run(c, run["id"])
    assert done["status"] == "succeeded", done
    steps = {s["step_id"]: s for s in done["steps"]}
    assert steps["status"]["finished_at"] <= steps["search"]["started_at"] <= steps["analyse"]["started_at"]
    assert steps["analyse"]["result"]["job"]["session_id"] == "fake-session-1234"
    assert "[REDACTED]" in steps["analyse"]["result"]["output"]
    assert steps["analyse"]["verification"][0] == {**steps["analyse"]["verification"][0], "method": "non_empty_result", "passed": True}
    names = [e["event"] for e in c.events]
    assert "run.output" in names and "run.step.updated" in names
    hist = c.call("runs.events", {"run_id": run["id"]})
    seqs = [e["seq"] for e in hist["events"]]
    assert seqs == sorted(seqs) and seqs[0] == 1
    tail = c.call("runs.events", {"run_id": run["id"], "after_seq": seqs[-3]})
    assert [e["seq"] for e in tail["events"]] == seqs[-2:]


def test_parallel_independent_steps(ctx):
    _, c, _ = ctx
    wf = create(c, [step("a", executor="agent.claude", prompt="FAKE_SLEEP=1.5"),
                    step("b", executor="agent.codex", prompt="FAKE_SLEEP=1.5"),
                    step("join", deps=["a", "b"])])
    t0 = time.monotonic()
    run = c.call("runs.start", {"workflow_id": wf["id"], "approve": True})
    done = wait_run(c, run["id"])
    assert done["status"] == "succeeded"
    s = {x["step_id"]: x for x in done["steps"]}
    assert s["a"]["started_at"] < s["b"]["finished_at"] and s["b"]["started_at"] < s["a"]["finished_at"]  # overlapped
    assert time.monotonic() - t0 < 6


def test_failure_propagation(ctx):
    _, c, _ = ctx
    wf = create(c, [step("bad", executor="agent.claude", prompt="FAKE_FAIL=rate"),
                    step("after", deps=["bad"]), step("independent")])
    done = wait_run(c, c.call("runs.start", {"workflow_id": wf["id"], "approve": True})["id"])
    s = {x["step_id"]: x for x in done["steps"]}
    assert done["status"] == "failed" and s["bad"]["error"]["code"] == "AGENT_RATE_LIMITED"
    assert s["after"]["state"] == "skipped"
    wf2 = create(c, [step("bad", executor="agent.claude", prompt="FAKE_FAIL=auth", on_failure="continue"),
                     step("after", deps=["bad"]), step("independent", executor="agent.codex", prompt="FAKE_SLEEP=0.5")])
    done = wait_run(c, c.call("runs.start", {"workflow_id": wf2["id"], "approve": True})["id"])
    s = {x["step_id"]: x for x in done["steps"]}
    assert s["independent"]["state"] == "succeeded" and s["after"]["state"] == "skipped"


def test_output_alone_does_not_succeed(ctx):
    _, c, _ = ctx
    wf = create(c, [step("j", executor="agent.claude", prompt="not json", validation="json_result"),
                    step("e", executor="agent.codex", prompt="FAKE_EMPTY")])
    done = wait_run(c, c.call("runs.start", {"workflow_id": wf["id"], "approve": True})["id"])
    s = {x["step_id"]: x for x in done["steps"]}
    assert s["j"]["state"] == "failed" and s["j"]["error"]["code"] == "VALIDATION_FAILED"
    assert s["e"]["state"] == "failed"
    wf = create(c, [step("j", executor="agent.claude", prompt="FAKE_JSON", validation="json_result")])
    done = wait_run(c, c.call("runs.start", {"workflow_id": wf["id"], "approve": True})["id"])
    assert done["status"] == "succeeded"


def test_cancel_run(ctx):
    _, c, _ = ctx
    wf = create(c, [step("long", executor="agent.codex", prompt="FAKE_SLEEP=60"), step("next", deps=["long"])])
    run = c.call("runs.start", {"workflow_id": wf["id"], "approve": True})
    wait_for(lambda: c.call("runs.get", {"run_id": run["id"]})["steps"][0]["state"] == "running")
    dup = c.request("runs.start", {"workflow_id": wf["id"], "approve": True})
    assert dup["error"]["code"] == "CONFLICT" and dup["error"]["details"]["run_id"] == run["id"]
    t0 = time.monotonic()
    out = c.call("runs.cancel", {"run_id": run["id"]})
    assert time.monotonic() - t0 < 10
    assert out["status"] == "cancelled"
    assert [s["state"] for s in out["steps"]] == ["cancelled", "cancelled"]
    assert c.request("runs.cancel", {"run_id": run["id"]})["error"]["code"] == "ILLEGAL_STATE"
    agents = c.call("agents.status", {"agent_id": "codex"})
    assert agents["active_jobs"] == 0 and agents["recent_jobs"][0]["status"] == "cancelled"


def test_approval_gate(ctx):
    _, c, _ = ctx
    wf = create(c, [step("gated", approval_required=True), step("after", deps=["gated"])])
    run = c.call("runs.start", {"workflow_id": wf["id"], "approve": True})
    wait_for(lambda: c.call("runs.get", {"run_id": run["id"]})["status"] == "awaiting_approval")
    assert c.request("runs.approve", {"run_id": run["id"], "step_id": "after", "approve": True})["error"]["code"] == "ILLEGAL_STATE"
    c.call("runs.approve", {"run_id": run["id"], "step_id": "gated", "approve": True})
    assert wait_run(c, run["id"])["status"] == "succeeded"
    run = c.call("runs.start", {"workflow_id": wf["id"], "approve": True})
    wait_for(lambda: c.call("runs.get", {"run_id": run["id"]})["status"] == "awaiting_approval")
    c.call("runs.approve", {"run_id": run["id"], "step_id": "gated", "approve": False})
    done = wait_run(c, run["id"])
    assert done["status"] == "incomplete" and [s["state"] for s in done["steps"]] == ["skipped", "skipped"]


def test_codex_write_step_uses_worktree(ctx, repo):
    _, c, _ = ctx
    wf = create(c, [step("edit", executor="agent.codex", mode="write", approval_required=True,
                         prompt="FAKE_WRITE=new_file.txt")])
    run = c.call("runs.start", {"workflow_id": wf["id"], "approve": True})
    wait_for(lambda: c.call("runs.get", {"run_id": run["id"]})["status"] == "awaiting_approval")
    c.call("runs.approve", {"run_id": run["id"], "step_id": "edit", "approve": True})
    done = wait_run(c, run["id"])
    assert done["status"] == "succeeded", done
    wt = done["steps"][0]["result"]["worktree"]
    assert (wt["path"] and __import__("os").path.exists(wt["path"] + "/new_file.txt"))
    assert not (repo / "new_file.txt").exists()  # original checkout untouched
    assert git(repo, "branch", "--show-current") == "main"  # no branch switch
    assert wt["branch"] in git(repo, "branch", "--list", wt["branch"])  # branch exists, nothing merged
    assert git(repo, "log", "--oneline", "main").count("\n") == 0


def test_pathfinder_plan_to_run(ctx):
    _, c, _ = ctx
    plan = c.call("plans.create", {"objective": "Summarise the Canopy rendering FAKE_PLAN"})
    assert plan["status"] == "generating"
    plan = wait_for(lambda: (p := c.call("plans.get", {"plan_id": plan["id"]}))["status"] != "generating" and p)
    assert plan["status"] == "ready", plan
    assert plan["validation"]["valid"] and plan["validation"]["executable"]
    runs_before = c.call("runs.list", {})["runs"]
    assert runs_before == []  # planning never executes anything
    wf = c.call("workflows.create", {"plan_id": plan["id"]})
    assert wf["source_plan_id"] == plan["id"]
    done = wait_run(c, c.call("runs.start", {"workflow_id": wf["id"], "approve": True})["id"])
    assert done["status"] == "succeeded"


@pytest.mark.parametrize("directive", ["FAKE_BADPLAN", "FAKE_CYCLEPLAN"])
def test_pathfinder_rejects_bad_plans(ctx, directive):
    _, c, _ = ctx
    plan = c.call("plans.create", {"objective": f"Do it {directive}", "agent_id": "codex"})
    plan = wait_for(lambda: (p := c.call("plans.get", {"plan_id": plan["id"]}))["status"] != "generating" and p)
    assert plan["status"] == "invalid" and plan["validation"]["errors"]
    r = c.request("workflows.create", {"plan_id": plan["id"]})
    assert r["error"]["code"] == "ILLEGAL_STATE"


def test_plans_validate_endpoint(ctx):
    _, c, _ = ctx
    res = c.call("plans.validate", {"plan": {"title": "t", "steps": [step("a"), step("b", deps=["a"])]}})
    assert res["valid"] and res["executable"]
    res = c.call("plans.validate", {"plan": {"title": "t", "steps": [step("a", executor="rm.rf")]}})
    assert not res["valid"] and res["errors"][0]["field"] == "executor"


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning", "ignore::RuntimeWarning")
def test_history_persists_and_interrupted_runs_are_not_rerun(config, repo):
    s1 = ServerThread(config).start()
    with s1.client() as c:
        c.call("projects.add", {"path": str(repo)})
        ok_wf = create(c, [step("a")])
        ok_run = c.call("runs.start", {"workflow_id": ok_wf["id"], "approve": True})
        wait_run(c, ok_run["id"])
        long_wf = create(c, [step("w", executor="agent.codex", prompt="FAKE_SLEEP=60"), step("after", deps=["w"])])
        long_run = c.call("runs.start", {"workflow_id": long_wf["id"], "approve": True})
        wait_for(lambda: c.call("runs.get", {"run_id": long_run["id"]})["steps"][0]["state"] == "running")
    # Simulate a crash: stop the loop without the graceful shutdown path.
    s1.loop.call_soon_threadsafe(s1.loop.stop)
    s1.thread.join(10)
    # What the kernel would do when the process dies: drop the listener and the lock.
    s1.server._server.close()
    os.close(s1.server._lock_fd)
    s2 = ServerThread(config).start()
    try:
        with s2.client() as c:
            assert c.call("runs.get", {"run_id": ok_run["id"]})["status"] == "succeeded"
            r = c.call("runs.get", {"run_id": long_run["id"]})
            assert r["status"] == "interrupted"
            assert [(s["state"], (s["error"] or {}).get("code")) for s in r["steps"]] == [("failed", "INTERRUPTED"), ("cancelled", None)]
            time.sleep(0.5)
            assert c.call("runs.get", {"run_id": long_run["id"]})["status"] == "interrupted"  # not resumed
            jobs = c.call("agents.status", {"agent_id": "codex"})["recent_jobs"]
            assert jobs[0]["status"] == "interrupted"
            # the workflow can be started again explicitly
            again = c.call("runs.start", {"workflow_id": ok_wf["id"], "approve": True})
            assert wait_run(c, again["id"])["status"] == "succeeded"
    finally:
        s2.stop()
        # The "crashed" loop's abandoned coroutines are collected here, inside this test's filter.
        del s1
        import gc
        gc.collect()


def test_cortex_steps_unsupported_without_graph(ctx, repo):
    _, c, project = ctx
    (repo / "graphify-out" / "graph.json").unlink()
    wf = create(c, [step("s", executor="cortex.search", inputs={"query": "x"})])
    assert wf["unsupported_steps"][0]["reason"] == "project has no Graphify graph"
    r = c.request("runs.start", {"workflow_id": wf["id"], "approve": True})
    assert r["error"]["code"] == "GRAPH_UNAVAILABLE" and r["error"]["details"]["unsupported_steps"]
    v = c.call("plans.validate", {"plan": {"title": "t", "steps": [step("s", executor="cortex.search", inputs={"query": "x"})]}})
    assert v["valid"] and not v["executable"]


def test_planner_prompt_omits_cortex_without_graph(config, repo):
    from cedar_brain.app import App
    from conftest import run as arun
    app = App(config)
    project = arun(app.call("projects.add", {"path": str(repo)}))
    with_graph = arun(app.pathfinder.build_prompt("look around", project, 5))
    (repo / "graphify-out" / "graph.json").unlink()
    without = arun(app.pathfinder.build_prompt("look around", project, 5))
    assert '"cortex.search"' in with_graph and '"cortex.search"' not in without
    assert "untrusted data" in without and "At most 5 steps" in without
