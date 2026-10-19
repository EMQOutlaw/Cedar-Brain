#!/usr/bin/env python3
"""Actual QML Unix Socket smoke and reconnect test. Uses only the published mock server."""
import os
from pathlib import Path
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix="mycelium-socket-") as temporary:
    temp = Path(temporary)
    try:
        with socket.socket(socket.AF_UNIX) as probe:
            probe.bind(str(temp / "probe.sock"))
    except PermissionError as error:
        print(f"SKIP: sandbox denies Unix sockets: {error}")
        sys.exit(77)
    cockpit = temp / "cockpit"
    shutil.copytree(ROOT / "cockpit", cockpit)
    shutil.copy(ROOT / "tests/frontend/SocketSmoke.qml", cockpit / "SocketSmoke.qml")
    runtime = temp / "runtime"
    runtime.mkdir(mode=0o700)
    env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), XDG_CONFIG_HOME=str(temp / "config"),
               XDG_STATE_HOME=str(temp / "state"), PYTHONPATH=str(ROOT / "backend"),
               QT_QPA_PLATFORM="offscreen", QT_QUICK_CONTROLS_STYLE="Basic", QT_QPA_PLATFORMTHEME="generic",
               QT_QUICK_BACKEND="software", CEDAR_BRAIN_FIXTURES="1", CEDAR_BRAIN_SOCKET=str(temp / "brain.sock"))
    env.pop("WAYLAND_DISPLAY", None)
    command = [sys.executable, "-m", "cedar_brain", "--socket", env["CEDAR_BRAIN_SOCKET"], "mock-serve", "--speed", "5"]
    def start_backend():
        return subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    backend = start_backend()
    frontend = None
    output = []
    try:
        frontend = subprocess.Popen(["qs", "-p", str(cockpit / "SocketSmoke.qml"), "--no-color"], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 35
        buffer = b""
        while time.monotonic() < deadline:
            readable, _, _ = select.select([frontend.stdout], [], [], .5)
            if readable:
                block = os.read(frontend.stdout.fileno(), 65536)
                if not block:
                    break
                buffer += block
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    text = line.decode(errors="replace")
                    output.append(text)
                    print(text)
                    if "SOCKET_RECONNECT_REQUEST" in text:
                        backend.terminate()
                        backend.wait(timeout=5)
                        time.sleep(.7)
                        backend = start_backend()
            if frontend.poll() is not None:
                break
        transcript = "\n".join(output)
        assert "SOCKET_PASS" in transcript and "SOCKET_FAIL" not in transcript, transcript
        assert "WARN scene:" not in transcript, transcript
    finally:
        for process in [frontend, backend]:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
