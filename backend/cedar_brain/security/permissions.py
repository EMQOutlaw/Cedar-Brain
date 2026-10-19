"""Peer identity checks and child-process environment policy."""

from __future__ import annotations

import os
import socket
import struct

# Variables a CLI agent legitimately needs. Everything else in the server's
# environment is dropped before spawning a child.
BASE_ENV_ALLOWLIST = (
    "HOME", "USER", "LOGNAME", "PATH", "LANG", "LANGUAGE", "TZ", "SHELL", "TMPDIR",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
)
# Auth/config variables passed only to the agent that owns them. The
# backend never reads, stores or logs their values; it only forwards what
# the user's own environment already provides.
AGENT_ENV_PASSTHROUGH = {
    "claude": ("CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_BASE_URL"),
    "codex": ("CODEX_HOME", "OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"),
}


def child_env(agent_id: str | None = None, source: dict | None = None) -> dict[str, str]:
    source = os.environ if source is None else source
    keep = set(BASE_ENV_ALLOWLIST) | set(AGENT_ENV_PASSTHROUGH.get(agent_id or "", ()))
    env = {k: v for k, v in source.items() if k in keep or k.startswith("LC_")}
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    return env


def peer_credentials(sock: socket.socket) -> tuple[int, int, int] | None:
    """(pid, uid, gid) of the connected Unix-socket peer, or None if unsupported."""
    so_peercred = getattr(socket, "SO_PEERCRED", None)
    if so_peercred is None:
        return None
    raw = sock.getsockopt(socket.SOL_SOCKET, so_peercred, struct.calcsize("3i"))
    return struct.unpack("3i", raw)
