"""Small shared helpers."""

from __future__ import annotations

import asyncio
import os
import time
import uuid


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def now() -> float:
    return time.time()


async def run_cmd(argv: list[str], cwd: str | None = None, timeout: float = 10.0,
                  env: dict | None = None, limit: int = 4 * 1024 * 1024) -> tuple[int, str, str]:
    """Run a short helper command (git, --version probes) via argv, never a shell."""
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        try:
            os.killpg(proc.pid, 9)
        except ProcessLookupError:
            pass
        await proc.wait()
        raise
    return proc.returncode, out[:limit].decode(errors="replace"), err[:limit].decode(errors="replace")
