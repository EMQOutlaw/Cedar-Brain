import asyncio
import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest

from cedar_brain.app import App
from cedar_brain.config import AgentConfig, Config
from cedar_brain.ipc.client import Client
from cedar_brain.ipc.server import BrainServer

FAKES = Path(__file__).resolve().parent.parent / "fakes"


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}).stdout.strip()


def sample_graph(head: str | None) -> dict:
    nodes = [
        {"id": "strata_geometry", "label": "StrataGeometry.js", "file_type": "code", "source_file": "src/StrataGeometry.js",
         "source_location": "L1", "community": 0, "community_name": "Rendering"},
        {"id": "strata_geometry.build", "label": "build()", "file_type": "code", "source_file": "src/StrataGeometry.js",
         "source_location": "L10", "community": 0, "community_name": "Rendering"},
        {"id": "canopy", "label": "Canopy.qml", "file_type": "code", "source_file": "src/Canopy.qml",
         "source_location": "L1", "community": 0, "community_name": "Rendering"},
        {"id": "bar", "label": "Bar.qml", "file_type": "code", "source_file": "src/Bar.qml", "source_location": "L1",
         "community": 1, "community_name": "Shell"},
        {"id": "notifications", "label": "NotificationService.qml", "file_type": "code",
         "source_file": "src/NotificationService.qml", "source_location": "L1", "community": 1, "community_name": "Shell"},
        {"id": "whispers", "label": "Whispers.qml", "file_type": "code", "source_file": "src/Whispers.qml",
         "source_location": "L1", "community": 1, "community_name": "Shell"},
        {"id": "design_doc", "label": "Design notes", "file_type": "doc_ref", "source_file": "docs/design.md",
         "community": 2},
        {"id": "escape", "label": "Escape", "file_type": "code", "source_file": "../../etc/passwd"},
        {"label": "no id: malformed"},
    ]
    links = [
        {"source": "canopy", "target": "strata_geometry", "relation": "imports", "confidence": "EXTRACTED"},
        {"source": "bar", "target": "canopy", "relation": "imports", "confidence": "EXTRACTED"},
        {"source": "strata_geometry", "target": "strata_geometry.build", "relation": "contains", "confidence": "EXTRACTED"},
        {"source": "whispers", "target": "notifications", "relation": "calls", "confidence": "INFERRED",
         "confidence_score": 0.6},
        {"source": "bar", "target": "notifications", "relation": "references", "confidence": "AMBIGUOUS"},
        {"source": "design_doc", "target": "canopy", "relation": "cites", "confidence": "EXTRACTED"},
        {"source": "ghost", "target": "canopy", "relation": "imports"},
        "not-a-link",
    ]
    g = {"directed": True, "multigraph": False, "graph": {"schema_version": "2", "graphify_version": "0.9.92"},
         "nodes": nodes, "links": links}
    if head:
        g["built_at_commit"] = head
    return g


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    (r / "src").mkdir(parents=True)
    git(r, "init", "-q", "-b", "main")
    for name in ("StrataGeometry.js", "Canopy.qml", "Bar.qml", "NotificationService.qml", "Whispers.qml"):
        (r / "src" / name).write_text(f"// {name}\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "init")
    head = git(r, "rev-parse", "HEAD")
    (r / "graphify-out").mkdir()
    (r / "graphify-out" / "graph.json").write_text(json.dumps(sample_graph(head)))
    (r / ".git" / "info" / "exclude").write_text("graphify-out/\n")
    return r


@pytest.fixture
def skills_dir(tmp_path):
    d = tmp_path / "skills"
    d.mkdir()
    ok = d / "graph-reader"
    ok.mkdir()
    (ok / "SKILL.md").write_text("---\nname: graph-reader\ndescription: >\n  Reads graphs\n  carefully.\n"
                                 "version: 1.2.0\nallowed-tools: [Read, Grep]\n---\n# Instructions\nRead the graph.\n")
    (ok / "scripts").mkdir()
    (ok / "scripts" / "install.sh").write_text("#!/bin/sh\ntouch /tmp/SHOULD_NEVER_RUN\n")
    os.chmod(ok / "scripts" / "install.sh", 0o755)
    nometa = d / "bare"
    nometa.mkdir()
    (nometa / "SKILL.md").write_text("Just text, no front matter.\n")
    return d


@pytest.fixture
def config(tmp_path, skills_dir):
    # AF_UNIX paths are limited to ~108 bytes, so keep the socket in a short dir.
    sock_dir = Path(tempfile.mkdtemp(prefix="cb-", dir="/tmp"))
    cfg = Config(
        skill_dirs=[skills_dir], import_roots=[tmp_path],
        agents={"claude": AgentConfig(executable=str(FAKES / "claude")),
                "codex": AgentConfig(executable=str(FAKES / "codex"))},
        max_parallel_steps=2, max_agent_processes=4, default_step_timeout_s=30, planner_timeout_s=30,
        state_dir=tmp_path / "state", socket_path=sock_dir / "run" / "brain.sock",
    )
    yield cfg
    subprocess.run(["rm", "-rf", str(sock_dir)])


def run(coro):
    return asyncio.run(coro)


class ServerThread:
    """A real BrainServer on a real socket, in a background event loop."""

    def __init__(self, config: Config):
        self.config = config
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._main, daemon=True)
        self.server: BrainServer | None = None
        self.error: BaseException | None = None

    def _main(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._serve())
        except BaseException as e:  # surface startup failures to the test
            self.error = e
            self.ready.set()

    async def _serve(self):
        self.app = App(self.config)
        self.app.recover()
        self.server = BrainServer(self.app, self.config.socket_path)
        await self.server.start()
        self.ready.set()
        await self.server._stopped.wait()

    def start(self):
        self.thread.start()
        self.ready.wait(10)
        if self.error:
            raise self.error
        return self

    def stop(self):
        if self.server and self.server._server is not None:
            asyncio.run_coroutine_threadsafe(self.server.stop(), self.loop).result(30)
        self.thread.join(10)

    def client(self, timeout=30) -> Client:
        return Client(self.config.socket_path, timeout=timeout)


@pytest.fixture
def server(config):
    s = ServerThread(config).start()
    yield s
    s.stop()


def wait_for(predicate, timeout=20.0, interval=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = predicate()
        if v:
            return v
        time.sleep(interval)
    raise AssertionError("condition not met in time")
