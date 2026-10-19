"""The contract document, examples and mock fixtures must match the implementation."""

import asyncio
import json
import re
import tempfile
import threading
from pathlib import Path

import pytest

from cedar_brain.app import App
from cedar_brain.events import TOPICS
from cedar_brain.hyphae.models import RUN_STATUSES, STEP_STATES, TRANSITIONS
from cedar_brain.ipc.client import Client
from cedar_brain.mock import BUNDLED, serve_mock
from cedar_brain.protocol import ALL_ERROR_CODES, parse_request

from conftest import wait_for

ROOT = Path(__file__).resolve().parents[2]
DOC = (ROOT / "contracts" / "protocol-v1.md").read_text()
EXAMPLES = ROOT / "contracts" / "examples"


@pytest.fixture(scope="module")
def methods(tmp_path_factory):
    from cedar_brain.config import Config
    d = tmp_path_factory.mktemp("c")
    return sorted(App(Config(skill_dirs=[], import_roots=[], state_dir=d / "s", socket_path=d / "x.sock")).methods)


def test_every_method_documented(methods):
    documented = set(re.findall(r"^### ([a-z]+\.[a-z_]+)", DOC, re.M))
    documented |= {"events.unsubscribe", "workflows.list"}  # documented on a shared heading
    assert set(methods) == documented


def test_every_error_code_documented():
    for code in ALL_ERROR_CODES:
        assert f"`{code}`" in DOC, code


def test_states_documented():
    for s in STEP_STATES + RUN_STATUSES:
        assert f"`{s}`" in DOC, s
    for src, targets in TRANSITIONS.items():
        for t in targets:
            assert f"`{t}`" in DOC
    for topic in TOPICS:
        assert topic in DOC


def test_examples_cover_methods_and_are_well_formed(methods):
    files = {p.stem for p in EXAMPLES.glob("*.json")}
    missing = set(methods) - files - {"events.unsubscribe", "projects.remove"}
    assert not missing, f"methods without examples: {missing}"
    for path in EXAMPLES.glob("*.json"):
        if path.name == "_protocol_errors.json":
            continue
        for item in json.loads(path.read_text()):
            req = parse_request(json.dumps(item["request"]).encode())
            resp = item["response"]
            assert resp["v"] == 1 and resp["id"] == req.id
            if resp["ok"]:
                assert "result" in resp and "error" not in resp
            else:
                assert resp["error"]["code"] in ALL_ERROR_CODES


def test_event_stream_fixture():
    lines = [json.loads(l) for l in (EXAMPLES / "mock-event-stream.jsonl").read_text().splitlines() if l.strip()]
    assert lines and all(set(l) == {"after_ms", "event", "data"} for l in lines)
    seqs = [l["data"]["seq"] for l in lines]
    assert seqs == list(range(1, len(seqs) + 1))
    assert lines[-1]["event"] == "run.updated" and lines[-1]["data"]["status"] == "succeeded"
    for l in lines:
        if l["event"] == "run.step.updated":
            assert l["data"]["state"] in TRANSITIONS[l["data"]["previous"]]


def test_mock_fixtures_only_use_real_methods(methods):
    responses = json.loads((BUNDLED / "responses.json").read_text())
    assert set(responses) <= set(methods)
    assert {"system.health", "runs.start", "plans.create", "agents.list"} <= set(responses)


def test_mock_server_speaks_the_protocol():
    sock = Path(tempfile.mkdtemp(prefix="cbm-", dir="/tmp")) / "mock.sock"
    loop = asyncio.new_event_loop()
    task_holder = {}

    def runner():
        asyncio.set_event_loop(loop)
        task_holder["t"] = loop.create_task(serve_mock(sock, None, speed=50))
        try:
            loop.run_until_complete(task_holder["t"])
        except asyncio.CancelledError:
            pass

    th = threading.Thread(target=runner, daemon=True)
    th.start()
    wait_for(sock.exists, 5)
    try:
        with Client(sock) as c:
            assert c.call("system.health")["status"] == "ok"
            assert c.request("nope.nothing")["error"]["code"] == "UNKNOWN_METHOD"
            c.send_raw(b"{bad\n")
            assert c.read_message()["error"]["code"] == "PARSE_ERROR"
            c.call("events.subscribe", {"topics": ["run"]})
            run = c.call("runs.start", {"workflow_id": "wf_x", "approve": True})
            ev = c.wait_event(lambda e: e["event"] == "run.updated" and e["data"]["status"] == "succeeded", 20)
            assert ev["data"]["run_id"] == run["id"]
    finally:
        loop.call_soon_threadsafe(task_holder["t"].cancel)
        th.join(5)
