"""Minimal synchronous client used by the CLI and tests."""

from __future__ import annotations

import json
import socket
import uuid
from pathlib import Path

from ..protocol import MAX_MESSAGE_BYTES, PROTOCOL_VERSION


class ClientError(Exception):
    def __init__(self, error: dict):
        super().__init__(f"{error.get('code')}: {error.get('message')}")
        self.error = error


class Client:
    def __init__(self, path: Path | str, timeout: float = 60.0):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect(str(path))
        self.buf = b""
        self.events: list[dict] = []

    def close(self) -> None:
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def send_raw(self, data: bytes) -> None:
        self.sock.sendall(data)

    def read_message(self) -> dict:
        while b"\n" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("server closed the connection")
            self.buf += chunk
            if len(self.buf) > MAX_MESSAGE_BYTES * 2:
                raise ConnectionError("oversized message from server")
        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line)

    def request(self, method: str, params: dict | None = None) -> dict:
        """Send a request and return the full response envelope (events are buffered)."""
        rid = uuid.uuid4().hex[:12]
        msg = {"v": PROTOCOL_VERSION, "id": rid, "method": method, "params": params or {}}
        self.send_raw((json.dumps(msg) + "\n").encode())
        while True:
            m = self.read_message()
            if "event" in m:
                self.events.append(m)
                continue
            if m.get("id") == rid:
                return m

    def call(self, method: str, params: dict | None = None):
        resp = self.request(method, params)
        if not resp.get("ok"):
            raise ClientError(resp.get("error") or {})
        return resp["result"]

    def wait_event(self, predicate, timeout: float = 30.0) -> dict:
        import time
        deadline = time.monotonic() + timeout
        for i, e in enumerate(self.events):
            if predicate(e):
                return self.events.pop(i)
        while time.monotonic() < deadline:
            self.sock.settimeout(max(0.05, deadline - time.monotonic()))
            m = self.read_message()
            if "event" in m:
                if predicate(m):
                    return m
                self.events.append(m)
        raise TimeoutError("event not received")
