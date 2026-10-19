import asyncio
import json
import os
import time

import pytest

from cedar_brain.agents.base import AgentTask, classify_failure
from cedar_brain.agents.runner import proc_start_ticks
from cedar_brain.app import App
from cedar_brain.config import AgentConfig

from conftest import run


def task(prompt, cwd, owner="t1", **kw):
    return AgentTask(prompt=prompt, cwd=str(cwd), owner_kind="test", owner_id=owner, **kw)


def run_agent(app, agent_id, t, progress=None):
    return run(app.runner.run(app.grove.get(agent_id), t, progress))


def test_detection(config):
    app = App(config)
    agents = {a["id"]: a for a in run(app.call("agents.list"))["agents"]}
    assert agents["claude"]["status"] == "available" and agents["claude"]["version"].startswith("2.1.0")
    assert agents["claude"]["modes"] == ["read_only"]
    assert agents["codex"]["modes"] == ["read_only", "write"]
    assert agents["claude"]["authenticated"] is True and agents["claude"]["auth_method"] == "fake"
    assert "email" not in json.dumps(agents)  # nothing beyond the boolean/method is kept


def test_missing_and_disabled(config, tmp_path):
    config.agents["claude"] = AgentConfig(executable=str(tmp_path / "nope"))
    config.agents["codex"] = AgentConfig(executable=None, enabled=False)
    app = App(config)
    agents = {a["id"]: a for a in run(app.call("agents.list"))["agents"]}
    assert agents["claude"]["status"] == "missing" and agents["codex"]["status"] == "disabled"
    caps = run(app.call("system.capabilities"))["features"]
    assert caps["agents.claude"]["status"] == "unavailable"
    assert caps["pathfinder.plans"]["status"] == "unavailable"
    res = run_agent(app, "claude", task("hi", tmp_path))
    assert res.status == "failed" and res.error["code"] == "AGENT_UNAVAILABLE"


def test_unsupported_cli_flags(config, tmp_path):
    old = tmp_path / "oldclaude"
    old.write_text("#!/bin/sh\nif [ \"$1\" = --version ]; then echo 0.1; else echo 'usage: claude -p'; fi\n")
    old.chmod(0o755)
    config.agents["claude"] = AgentConfig(executable=str(old))
    app = App(config)
    info = run(app.call("agents.status", {"agent_id": "claude", "refresh": True}))
    assert info["status"] == "unsupported" and "--output-format" in info["missing_flags"]


def test_argv_is_safe(config, tmp_path):
    app = App(config)
    argv_file = tmp_path / "argv.json"
    res = run_agent(app, "claude", task(f"hello; rm -rf ~ $(whoami) FAKE_ARGV={argv_file}", tmp_path))
    assert res.status == "succeeded"
    argv = json.loads(argv_file.read_text())
    assert "--dangerously-skip-permissions" not in argv
    assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob"
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--setting-sources") + 1] == "user"  # no repo hooks or settings
    assert not any("rm -rf" in a for a in argv)  # prompt goes over stdin, not argv
    res = run_agent(app, "codex", task(f"x FAKE_ARGV={argv_file}", tmp_path, owner="t2"))
    argv = json.loads(argv_file.read_text())
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv


def test_success_streams_and_redacts(config, tmp_path):
    app = App(config)
    events = []
    res = run_agent(app, "claude", task("FAKE_SLEEP=0.5", tmp_path), events.append)
    assert res.status == "succeeded" and res.exit_code == 0 and res.session_id == "fake-session-1234"
    assert "[REDACTED]" in res.final_text and "sk-ant-" not in res.final_text
    assert any(e.kind == "message" for e in events)
    log = open(res.stdout_log).read()
    assert "sk-ant-abcdefghijklmnop" not in log
    assert oct(os.stat(res.stdout_log).st_mode & 0o777) == "0o600"
    row = app.db.one("SELECT status, session_id FROM agent_jobs WHERE id=?", (res.job_id,))
    assert tuple(row) == ("succeeded", "fake-session-1234")


@pytest.mark.parametrize("kind,code", [("auth", "AGENT_AUTH_FAILED"), ("rate", "AGENT_RATE_LIMITED"),
                                       ("usage", "AGENT_USAGE_LIMIT"), ("network", "AGENT_NETWORK"),
                                       ("crash", "AGENT_KILLED")])
@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_failure_classification(config, tmp_path, agent, kind, code):
    app = App(config)
    res = run_agent(app, agent, task(f"FAKE_FAIL={kind}", tmp_path))
    assert res.status == "failed" and res.error["code"] == code


def test_classify_failure_strings():
    assert classify_failure("HTTP 401 Unauthorized") == "AGENT_AUTH_FAILED"
    assert classify_failure("something odd") == "AGENT_FAILED"


def test_malformed_output_tolerated(config, tmp_path):
    app = App(config)
    res = run_agent(app, "claude", task("FAKE_GARBAGE", tmp_path))
    assert res.status == "succeeded" and res.malformed_lines >= 3


def test_output_flood_is_bounded(config, tmp_path):
    app = App(config)
    t0 = time.monotonic()
    res = run_agent(app, "codex", task("FAKE_FLOOD", tmp_path))
    assert res.status == "failed" and res.error["code"] == "AGENT_OUTPUT_LIMIT"
    assert os.path.getsize(res.stdout_log) < 9 * 1024 * 1024
    assert time.monotonic() - t0 < 30


def test_timeout_kills_process_group(config, tmp_path):
    app = App(config)
    pidfile = tmp_path / "child.pid"
    res = run_agent(app, "claude", task(f"FAKE_CHILD={pidfile} FAKE_SLEEP=30", tmp_path, timeout_s=1.5))
    assert res.status == "timed_out" and res.error["code"] == "AGENT_TIMEOUT"
    child = int(pidfile.read_text())
    time.sleep(0.2)
    assert proc_start_ticks(child) is None or _is_zombie(child)


def _is_zombie(pid):
    try:
        return open(f"/proc/{pid}/stat").read().split(") ")[1][0] == "Z"
    except OSError:
        return True


def test_cancel(config, tmp_path):
    app = App(config)

    async def go():
        t = asyncio.create_task(app.runner.run(app.grove.get("codex"), task("FAKE_SLEEP=30", tmp_path)))
        for _ in range(100):
            await asyncio.sleep(0.05)
            if app.runner.active and next(iter(app.runner.active.values())).proc:
                break
        job_id = next(iter(app.runner.active))
        assert await app.runner.cancel(job_id)
        return await t

    t0 = time.monotonic()
    res = run(go())
    assert res.status == "cancelled" and time.monotonic() - t0 < 10


def test_duplicate_owner_rejected(config, tmp_path):
    app = App(config)

    async def go():
        a = asyncio.create_task(app.runner.run(app.grove.get("claude"), task("FAKE_SLEEP=1", tmp_path, owner="same")))
        await asyncio.sleep(0.3)
        b = await app.runner.run(app.grove.get("claude"), task("x", tmp_path, owner="same"))
        return await a, b

    a, b = run(go())
    assert a.status == "succeeded" and b.error["code"] == "DUPLICATE_JOB"


def test_unsupported_mode(config, tmp_path):
    app = App(config)
    res = run_agent(app, "claude", task("x", tmp_path, mode="write"))
    assert res.status == "failed" and res.error["code"] == "AGENT_UNAVAILABLE"


def test_env_is_minimised(monkeypatch):
    from cedar_brain.security.permissions import child_env
    src = {"PATH": "/bin", "HOME": "/h", "AWS_SECRET_ACCESS_KEY": "x", "GITHUB_TOKEN": "y",
           "ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "o", "LC_ALL": "C"}
    claude = child_env("claude", src)
    assert "ANTHROPIC_API_KEY" in claude and "OPENAI_API_KEY" not in claude
    assert "AWS_SECRET_ACCESS_KEY" not in claude and "GITHUB_TOKEN" not in claude and claude["LC_ALL"] == "C"
    assert "OPENAI_API_KEY" in child_env("codex", src)


def test_orphan_recovery_kills_only_matching_process(config, tmp_path):
    import subprocess
    app = App(config)
    orphan = subprocess.Popen(["sleep", "60"], start_new_session=True)
    unrelated = subprocess.Popen(["sleep", "60"], start_new_session=True)
    ticks = proc_start_ticks(orphan.pid)
    for jid, pid, t in (("job_a", orphan.pid, ticks), ("job_b", unrelated.pid, 12345)):  # job_b: pid reused
        app.db.execute("INSERT INTO agent_jobs(id, owner_kind, owner_id, agent_id, status, mode, cwd, pid, proc_start_ticks, started_at)"
                       " VALUES (?,?,?,?,?,?,?,?,?,?)", (jid, "test", jid, "claude", "running", "read_only", "/", pid, t, 0))
    assert sorted(app.runner.recover()) == ["job_a", "job_b"]
    assert orphan.wait(5) is not None
    assert unrelated.poll() is None
    unrelated.kill()
    unrelated.wait()
    assert {r["status"] for r in app.db.all("SELECT status FROM agent_jobs")} == {"interrupted"}
