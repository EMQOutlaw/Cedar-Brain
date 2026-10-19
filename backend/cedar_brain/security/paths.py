"""Safe path resolution.

All checks operate on fully resolved paths (``os.path.realpath``) so that
``..`` segments and symbolic links cannot escape an approved root.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..protocol import BrainError, ErrorCode

# Directories that never hold project or skill content and often hold
# credentials. Matched against resolved paths, relative to $HOME.
SENSITIVE_HOME_DIRS = (
    ".ssh", ".gnupg", ".password-store", ".aws", ".azure", ".kube", ".docker",
    ".config/gcloud", ".config/gh", ".netrc", ".pki", ".local/share/keyrings",
    ".mozilla", ".config/BraveSoftware", ".config/google-chrome", ".config/chromium",
    ".codex/auth.json", ".claude/.credentials.json", ".claude.json",
)
SENSITIVE_SYSTEM_DIRS = ("/etc", "/proc", "/sys", "/dev", "/boot", "/root", "/var/lib", "/run")


def resolve(path: str | os.PathLike) -> Path:
    return Path(os.path.realpath(os.path.expanduser(os.fspath(path))))


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def is_sensitive(path: Path) -> bool:
    home = resolve(Path.home())
    for rel in SENSITIVE_HOME_DIRS:
        target = home / rel
        if path == target or is_within(path, target):
            return True
    for sysdir in SENSITIVE_SYSTEM_DIRS:
        if path == Path(sysdir) or is_within(path, Path(sysdir)):
            return True
    return path == Path("/")


def ensure_within(path: str | os.PathLike, roots: list[Path], what: str = "path") -> Path:
    """Resolve ``path`` and require it to sit inside one of ``roots``."""
    resolved = resolve(path)
    if is_sensitive(resolved):
        raise BrainError(ErrorCode.PATH_REJECTED, f"{what} points into a protected location")
    for root in roots:
        if is_within(resolved, resolve(root)):
            return resolved
    raise BrainError(ErrorCode.PATH_REJECTED, f"{what} is outside the approved locations")


def ensure_not_sensitive(path: str | os.PathLike, what: str = "path") -> Path:
    resolved = resolve(path)
    if is_sensitive(resolved):
        raise BrainError(ErrorCode.PATH_REJECTED, f"{what} points into a protected location")
    return resolved


def resolve_in_project(root: Path, rel: str) -> Path | None:
    """Resolve a project-relative (or absolute) file reference; None if it escapes."""
    if not isinstance(rel, str) or not rel or "\x00" in rel:
        return None
    candidate = resolve(Path(root) / rel) if not os.path.isabs(rel) else resolve(rel)
    return candidate if is_within(candidate, resolve(root)) else None
