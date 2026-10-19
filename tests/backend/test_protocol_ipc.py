import json
import os
import socket
import stat
import subprocess
import sys

import pytest

from cedar_brain.ipc.server import AlreadyRunning, BrainServer
from cedar_brain.protocol import MAX_MESSAGE_BYTES, BrainError, P, parse_request, validate_params

from conftest import ServerThread, run


# -- pure protocol ------------------------------------------------------------

def test_parse_valid_request():
    r = parse_request(b'{"v":1,"id":"a","method":"system.health","params":{}}')
    assert (r.id, r.method, r.params) == ("a", "system.health", {})


@pytest.mark.parametrize("line,code", [
    (b"not json", "PARSE_ERROR"),
    (b"[1,2]", "INVALID_REQUEST"),
    (b'{"v":1,"method":"system.health"}', "INVALID_REQUEST"),
    (b'{"v":2,"id":"a","method":"system.health"}', "UNSUPPORTED_VERSION"),
    (b'{"v":true,"id":"a","method":"system.health"}', "UNSUPPORTED_VERSION"),
    (b'{"v":1,"id":"a","method":"DROP TABLE"}', "INVALID_REQUEST"),
    (b'{"v":1,"id":"a","method":"x.y","params":[]}', "INVALID_REQUEST"),
    (b'{"v":1,"id":"a","method":"x.y","extra":1}', "INVALID_REQUEST"),
    (b'\xff\xfe', "PARSE_ERROR"),
])
def test_parse_rejects(line, code):
    with pytest.raises(BrainError) as e:
        parse_request(line)
    assert e.value.code == code


def test_parse_oversized():
    with pytest.raises(BrainError) as e:
        parse_request(b"x" * (MAX_MESSAGE_BYTES + 1))
    assert e.value.code == "MESSAGE_TOO_LARGE"


def test_validate_params():
    spec = {"n": P(int, min=1, max=3), "s": P(str, required=True, max_len=3), "b": P(bool, default=False)}
    assert validate_params(spec, {"s": "ab"}) == {"n": None, "s": "ab", "b": False}
    for bad in ({"s": "abcd"}, {}, {"s": "a", "n": True}, {"s": "a", "n": 9}, {"s": "a", "zz": 1}, {"s": "a\x00"}):
        with pytest.raises(BrainError) as e:
            validate_params(spec, bad)
        assert e.value.code == "INVALID_PARAMS"


# -- real socket ----------------------------------------------------------------

def test_health_over_socket(server):
    with server.client() as c:
        r = c.call("system.health")
    assert r["status"] == "ok" and r["protocol"] == 1 and r["database"]["ok"]


def test_socket_permissions(server):
    sp = server.config.socket_path
    assert stat.S_IMODE(os.stat(sp).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(sp.parent).st_mode) == 0o700
    assert stat.S_ISSOCK(os.lstat(sp).st_mode)


def test_invalid_requests_do_not_crash(server):
    with server.client() as c:
        c.send_raw(b"garbage\n")
        assert c.read_message()["error"]["code"] == "PARSE_ERROR"
        c.send_raw(b'{"v":9,"id":"x1","method":"system.health"}\n')
        m = c.read_message()
        assert m["error"]["code"] == "UNSUPPORTED_VERSION" and m["id"] == "x1"
        r = c.request("no.such_method")
        assert r["error"]["code"] == "UNKNOWN_METHOD"
        r = c.request("projects.select", {"project_id": 5})
        assert r["error"]["code"] == "INVALID_PARAMS"
        r = c.request("system.health", {"unexpected": 1})
        assert r["error"]["code"] == "INVALID_PARAMS"
        assert c.call("system.health")["status"] == "ok"  # connection still usable


def test_oversized_message_closes_connection_only(server):
    with server.client() as c:
        c.send_raw(b'{"v":1,"id":"big","method":"system.health","params":{"p":"' + b"a" * (MAX_MESSAGE_BYTES + 10) + b'"}}\n')
        m = c.read_message()
        assert m["error"]["code"] == "MESSAGE_TOO_LARGE"
        with pytest.raises((ConnectionError, OSError)):
            c.read_message()
    with server.client() as c2:  # server survives
        assert c2.call("system.health")["status"] == "ok"


def test_concurrent_requests_correlated(server):
    with server.client() as c:
        for i in range(20):
            c.send_raw(json.dumps({"v": 1, "id": f"r{i}", "method": "system.health", "params": {}}).encode() + b"\n")
        seen = {c.read_message()["id"] for _ in range(20)}
    assert seen == {f"r{i}" for i in range(20)}


def test_capabilities_distinguish_states(server):
    with server.client() as c:
        caps = c.call("system.capabilities")
    statuses = {f["status"] for f in caps["features"].values()}
    assert statuses <= {"supported", "unavailable", "not_implemented"}
    assert caps["features"]["agents.claude.write"]["status"] == "unavailable"
    assert caps["features"]["skills.execution"]["status"] == "not_implemented"
    assert caps["features"]["agents.claude"]["status"] == "supported"
    assert "runs.start" in caps["methods"]


def test_subscription_and_events(server, repo):
    with server.client() as c:
        assert c.call("events.subscribe", {"topics": ["project"]}) == {"topics": ["project"]}
        c.call("projects.add", {"path": str(repo)})
        ev = c.wait_event(lambda e: e["event"] == "project.updated")
        assert ev["v"] == 1 and ev["data"]["change"] == "added"
        r = c.request("events.subscribe", {"topics": ["bogus"]})
        assert r["error"]["code"] == "INVALID_PARAMS"


def test_single_instance(server, config):
    other = BrainServer(server.app, config.socket_path)
    with pytest.raises(AlreadyRunning):
        run(other.start())


def test_stale_socket_is_replaced(config):
    config.socket_path.parent.mkdir(parents=True, mode=0o700)
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(config.socket_path))
    s.close()  # leaves a dead socket file behind
    srv = ServerThread(config).start()
    try:
        with srv.client() as c:
            assert c.call("system.health")["status"] == "ok"
    finally:
        srv.stop()
    assert not config.socket_path.exists()  # removed on clean shutdown


def test_reconnect_after_restart(config):
    s1 = ServerThread(config).start()
    with s1.client() as c:
        first = c.call("system.health")["instance_id"]
    s1.stop()
    s2 = ServerThread(config).start()
    try:
        with s2.client() as c:
            assert c.call("system.health")["instance_id"] != first
    finally:
        s2.stop()


def test_other_uid_rejected(server):
    """Exercise the peer check by pretending the server runs as another uid."""
    import cedar_brain.ipc.server as srv_mod
    real = srv_mod.os.getuid
    srv_mod.os.getuid = lambda: real() + 1
    try:
        with server.client() as c:
            c.send_raw(b'{"v":1,"id":"a","method":"system.health"}\n')
            with pytest.raises((ConnectionError, OSError)):
                c.read_message()
    finally:
        srv_mod.os.getuid = real


def test_cli_entrypoint_version():
    out = subprocess.run([sys.executable, "-m", "cedar_brain", "--version"], capture_output=True, text=True)
    assert out.returncode == 0 and "cedar-brain" in out.stdout
