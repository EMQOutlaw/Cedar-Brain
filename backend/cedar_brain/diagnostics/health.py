"""`cedar-brain doctor`: environment checks that need no running server."""

from __future__ import annotations

import os
import shutil
import socket
import sqlite3
import stat
import sys

from ..agents.registry import ADAPTERS
from ..config import Config, config_dir


def _mode(path) -> str | None:
    try:
        return oct(stat.S_IMODE(os.stat(path).st_mode))
    except OSError:
        return None


async def doctor(config: Config) -> list[dict]:
    checks: list[dict] = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": ok, "detail": detail})

    add("python>=3.12", sys.version_info >= (3, 12), sys.version.split()[0])
    add("sqlite", True, sqlite3.sqlite_version)
    add("git on PATH", shutil.which("git") is not None, shutil.which("git") or "missing")
    add("config file", True, str(config_dir() / "config.json") + ("" if (config_dir() / "config.json").exists() else " (not present; defaults in use)"))
    sd = config.state_dir
    add("state dir", True, f"{sd} mode={_mode(sd) or 'not created yet'}")
    if sd.exists():
        add("state dir is owner-only", _mode(sd) == "0o700", _mode(sd) or "")
    if config.db_path.exists():
        add("database is owner-only", _mode(config.db_path) == "0o600", _mode(config.db_path) or "")
    sp = config.socket_path
    running = False
    if sp.exists():
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(str(sp))
            running = True
        except OSError:
            pass
        finally:
            probe.close()
        add("socket is owner-only", _mode(sp) == "0o600", f"{sp} {_mode(sp)}")
        add("socket dir is owner-only", _mode(sp.parent) == "0o700", f"{sp.parent} {_mode(sp.parent)}")
    add("server running", running, str(sp) if running else f"no server on {sp} (start: cedar-brain serve)")
    for aid, cls in ADAPTERS.items():
        info = await cls(config.agent(aid)).detect(force=True)
        detail = f"{info.status}; {info.version or ''}; modes={info.modes}"
        if info.authenticated is not None:
            detail += f"; authenticated={info.authenticated}"
        if info.detail:
            detail += f"; {info.detail}"
        add(f"agent {aid}", info.status == "available", detail)
    gx = config.graphify_executable or shutil.which("graphify")
    add("graphify (optional)", gx is not None, gx or "not installed; Cortex can still read existing graph.json files")
    existing = [str(p) for p in config.skill_dirs if p.is_dir()]
    add("skill directories", True, ", ".join(existing) or "none of the configured skill dirs exist")
    return checks
