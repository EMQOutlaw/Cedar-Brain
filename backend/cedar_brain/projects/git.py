"""Read-only git inspection. Nothing here changes refs, remotes or branches."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

from ..util import run_cmd

GIT_ENV_EXTRA = {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}


def _env() -> dict:
    env = {k: v for k, v in os.environ.items() if k in ("HOME", "PATH", "USER", "LOGNAME", "XDG_CONFIG_HOME")}
    env.update(GIT_ENV_EXTRA)
    return env


async def git(root: str, *args: str, timeout: float = 10.0) -> tuple[int, str, str]:
    return await run_cmd(["git", "-C", root, *args], timeout=timeout, env=_env())


async def toplevel(path: str) -> str | None:
    try:
        code, out, _ = await git(path, "rev-parse", "--show-toplevel")
    except (FileNotFoundError, asyncio.TimeoutError):
        return None
    return out.strip() if code == 0 and out.strip() else None


@dataclass
class GitStatus:
    valid: bool
    branch: str | None = None
    detached: bool = False
    head: str | None = None
    clean: bool | None = None
    changed_files: int | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)


async def status(root: str) -> GitStatus:
    if not os.path.isdir(root):
        return GitStatus(valid=False, error="project directory is missing")
    top = await toplevel(root)
    if top is None or os.path.realpath(top) != os.path.realpath(root):
        return GitStatus(valid=False, error="not the top level of a git repository")
    try:
        code, out, _ = await git(root, "symbolic-ref", "--short", "-q", "HEAD")
        branch = out.strip() if code == 0 else None
        code, out, _ = await git(root, "rev-parse", "--verify", "-q", "HEAD")
        head = out.strip() if code == 0 else None  # None on an unborn branch
        code, out, err = await git(root, "status", "--porcelain=v1", "-z", "--untracked-files=normal", timeout=20)
        if code != 0:
            return GitStatus(valid=True, branch=branch, detached=branch is None and head is not None,
                             head=head, error=err.strip()[:300] or "git status failed")
    except asyncio.TimeoutError:
        return GitStatus(valid=True, error="git timed out")
    entries = out.split("\0")
    changed, i = 0, 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        changed += 1
        if "R" in entry[:2] or "C" in entry[:2]:
            i += 1  # renames/copies carry the original path as a second field
    return GitStatus(valid=True, branch=branch, detached=branch is None and head is not None,
                     head=head, clean=changed == 0, changed_files=changed)


async def commits_between(root: str, old: str, new: str) -> int | None:
    code, out, _ = await git(root, "rev-list", "--count", f"{old}..{new}")
    return int(out.strip()) if code == 0 and out.strip().isdigit() else None


async def worktree_support(root: str) -> bool:
    code, _, _ = await git(root, "worktree", "list", "--porcelain")
    return code == 0
