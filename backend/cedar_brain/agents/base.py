"""Agent adapter interface and shared types."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..config import AgentConfig
from ..security.permissions import child_env
from ..util import run_cmd

DETECT_TTL_S = 120
FLAG_RE = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]+)")
# Flags that must never be passed, whatever the configuration says.
FORBIDDEN_FLAGS = frozenset({
    "--dangerously-skip-permissions", "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust", "--allow-dangerously-skip-permissions",
})


class AgentStatus:
    AVAILABLE = "available"
    MISSING = "missing"          # executable not found
    DISABLED = "disabled"        # turned off in config
    UNSUPPORTED = "unsupported"  # found, but lacks flags this backend requires
    ERROR = "error"              # found, but --version failed


@dataclass
class AgentInfo:
    id: str
    name: str
    status: str
    executable: str | None = None
    version: str | None = None
    authenticated: bool | None = None
    auth_method: str | None = None
    flags: frozenset = frozenset()
    missing_flags: list[str] = field(default_factory=list)
    modes: list[str] = field(default_factory=list)
    detail: str | None = None
    checked_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "status": self.status, "executable": self.executable,
            "version": self.version, "authenticated": self.authenticated, "auth_method": self.auth_method,
            "modes": self.modes, "missing_flags": self.missing_flags, "detail": self.detail,
            "checked_at": self.checked_at,
        }


@dataclass
class AgentTask:
    prompt: str
    cwd: str
    owner_kind: str          # "run_step" | "plan"
    owner_id: str
    mode: str = "read_only"  # "read_only" | "write"
    timeout_s: float = 900
    output_schema_path: str | None = None


@dataclass
class ParseState:
    session_id: str | None = None
    final_text: str | None = None
    reported_error: str | None = None
    usage: dict | None = None
    malformed_lines: int = 0


@dataclass
class Progress:
    kind: str  # message | tool | status | error
    text: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "text": self.text}


class AgentAdapter(ABC):
    id: str
    name: str
    required_flags: tuple[str, ...] = ()
    write_capable: bool = False

    def __init__(self, cfg: AgentConfig):
        self.cfg = cfg
        self._info: AgentInfo | None = None
        self._lock = asyncio.Lock()

    def locate(self) -> str | None:
        exe = self.cfg.executable or self.id
        if os.path.sep in exe:
            return exe if os.path.isfile(exe) and os.access(exe, os.X_OK) else None
        return shutil.which(exe)

    async def detect(self, force: bool = False) -> AgentInfo:
        async with self._lock:
            if self._info and not force and time.time() - self._info.checked_at < DETECT_TTL_S:
                return self._info
            self._info = await self._detect()
            return self._info

    async def _detect(self) -> AgentInfo:
        info = AgentInfo(self.id, self.name, AgentStatus.MISSING, checked_at=time.time())
        if not self.cfg.enabled:
            info.status, info.detail = AgentStatus.DISABLED, "disabled in config"
            return info
        exe = self.locate()
        if not exe:
            info.detail = f"'{self.cfg.executable or self.id}' not found on PATH"
            return info
        info.executable = exe
        env = child_env(self.id)
        try:
            code, out, err = await run_cmd([exe, "--version"], timeout=10, env=env)
        except (OSError, asyncio.TimeoutError) as exc:
            info.status, info.detail = AgentStatus.ERROR, f"--version failed: {exc.__class__.__name__}"
            return info
        if code != 0:
            info.status, info.detail = AgentStatus.ERROR, f"--version exited {code}"
            return info
        info.version = (out.strip() or err.strip()).splitlines()[0][:120] if (out.strip() or err.strip()) else None
        try:
            _, help_out, help_err = await run_cmd([exe, *self.help_args()], timeout=10, env=env)
        except (OSError, asyncio.TimeoutError):
            help_out = help_err = ""
        info.flags = frozenset(FLAG_RE.findall(help_out + help_err))
        info.missing_flags = [f for f in self.required_flags if f not in info.flags]
        if info.missing_flags:
            info.status = AgentStatus.UNSUPPORTED
            info.detail = "installed CLI lacks required flags: " + ", ".join(info.missing_flags)
            return info
        info.status = AgentStatus.AVAILABLE
        info.modes = ["read_only"] + (["write"] if self.write_capable and self.write_supported(info) else [])
        info.authenticated, info.auth_method = await self.auth_status(exe, env)
        return info

    def help_args(self) -> list[str]:
        return ["--help"]

    async def auth_status(self, exe: str, env: dict) -> tuple[bool | None, str | None]:
        return None, None

    def write_supported(self, info: AgentInfo) -> bool:
        return False

    def argv(self, task: AgentTask, info: AgentInfo) -> list[str]:
        argv = self.build_argv(task, info)
        bad = FORBIDDEN_FLAGS.intersection(argv)
        if bad:
            raise RuntimeError(f"refusing to pass forbidden flag(s) {sorted(bad)}")
        return argv

    @abstractmethod
    def build_argv(self, task: AgentTask, info: AgentInfo) -> list[str]: ...

    @abstractmethod
    def parse_line(self, obj: dict, state: ParseState) -> list[Progress]: ...


def classify_failure(text: str) -> str:
    """Map stderr / error text to a stable error code."""
    t = text.lower()
    if any(s in t for s in ("not logged in", "please run /login", "authentication", "unauthorized", "401",
                            "invalid api key", "login required", "please log in", "codex login")):
        return "AGENT_AUTH_FAILED"
    if any(s in t for s in ("usage limit", "quota", "credit balance", "out of credits", "plan limit")):
        return "AGENT_USAGE_LIMIT"
    if any(s in t for s in ("rate limit", "rate_limit", "429", "too many requests", "overloaded")):
        return "AGENT_RATE_LIMITED"
    if any(s in t for s in ("enotfound", "econnrefused", "econnreset", "network", "getaddrinfo",
                            "connection refused", "connection reset", "dns", "timed out connecting")):
        return "AGENT_NETWORK"
    return "AGENT_FAILED"
