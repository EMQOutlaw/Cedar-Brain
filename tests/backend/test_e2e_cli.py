"""End to end: `cedar-brain serve` as a real process, driven through the CLI and the socket."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from cedar_brain.ipc.client import Client

from conftest import FAKES, wait_for


@pytest.fixture
def env(tmp_path, skills_dir):
    runtime = Path(tempfile.mkdtemp(prefix="cbe-", dir="/tmp"))
    e = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path / "cfg"), "XDG_STATE_HOME": str(tmp_path / "st"),
         "XDG_RUNTIME_DIR": str(runtime)}
    e.pop("CEDAR_BRAIN_SOCKET", None)
    (tmp_path / "cfg" / "cedar-brain").mkdir(parents=True)
    (tmp_path / "cfg" / "cedar-brain" / "config.json").write_text(json.dumps({
        "skill_dirs": [str(skills_dir)], "import_roots": [str(tmp_path)],
        "agents": {"claude": {"executable": str(FAKES / "claude")}, "codex": {"executable": str(FAKES / "codex")}},
        "default_step_timeout_s": 30,
    }))
    yield e
    subprocess.run(["rm", "-rf", str(runtime)])


def cli(env, *args, check=True):
    p = subprocess.run([sys.executable, "-m", "cedar_brain", *args], env=env, capture_output=True, text=True, timeout=60)
    if check and p.returncode != 0:
        raise AssertionError(p.stderr)
    return p


def test_serve_cli_end_to_end(env, repo):
    sock = Path(env["XDG_RUNTIME_DIR"]) / "cedar-brain" / "brain.sock"
    # Direct (no server) mode works too.
    assert json.loads(cli(env, "agents", "list").stdout)["agents"][0]["status"] == "available"
    proc = subprocess.Popen([sys.executable, "-m", "cedar_brain", "serve"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        wait_for(sock.exists, 15)
        second = cli(env, "serve", check=False)
        assert second.returncode == 3 and "already" in second.stderr
        added = json.loads(cli(env, "projects", "add", str(repo)).stdout)
        assert added["root"] == str(repo)
        assert json.loads(cli(env, "graph", "status").stdout)["nodes"] == 8
        assert any(s["name"] == "graph-reader" for s in json.loads(cli(env, "skills", "list").stdout)["skills"])
        doctor = cli(env, "doctor").stdout
        assert "[ok] server running" in doctor and "[ok] socket is owner-only" in doctor

        steps = [{"id": "look", "description": "d", "executor": "agent.claude", "inputs": {"prompt": "FAKE_SLEEP=0.3"},
                  "depends_on": [], "expected_output": "x", "approval_required": False, "validation": "non_empty_result"}]
        wf = json.loads(cli(env, "call", "workflows.create", json.dumps({"definition": {"name": "e2e", "steps": steps}})).stdout)
        with Client(sock) as c:
            c.call("events.subscribe", {"topics": ["run"]})
            run = c.call("runs.start", {"workflow_id": wf["id"], "approve": True})
            ev = c.wait_event(lambda e: e["event"] == "run.updated" and e["data"]["status"] == "succeeded", 30)
            assert ev["data"]["run_id"] == run["id"]
        err = cli(env, "call", "runs.get", '{"run_id": "run_doesnotexist"}', check=False)
        assert err.returncode == 1 and "NOT_FOUND" in err.stderr
    finally:
        proc.send_signal(signal.SIGTERM)
        out, errs = proc.communicate(timeout=20)
    assert proc.returncode == 0, errs
    assert not sock.exists()
    # history survives the restart (read in direct mode)
    runs = json.loads(cli(env, "call", "runs.list").stdout)["runs"]
    assert runs[0]["status"] == "succeeded"
