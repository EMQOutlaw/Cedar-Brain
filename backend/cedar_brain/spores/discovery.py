"""Discover skills on disk. Read-only: nothing found here is ever executed."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from ..security.paths import is_sensitive, is_within, resolve
from .manifests import ManifestError, read_manifest

MAX_SKILLS_PER_ROOT = 500
MAX_FILES_LISTED = 200
MAX_WALK_DEPTH = 4
EXECUTABLE_SUFFIXES = {".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".ps1", ".bat", ".exe"}
REQUIREMENT_KEYS = ("requires", "requirements", "dependencies", "allowed-tools", "allowed_tools", "tools", "compatibility", "license")


def skill_id(path: Path) -> str:
    return "skl_" + hashlib.sha256(str(path).encode()).hexdigest()[:16]


def inspect_dir(skill_dir: Path, source: str, approved_roots: list[Path]) -> dict:
    """Build a skill record for ``skill_dir`` (already resolved)."""
    manifest_path = skill_dir / "SKILL.md"
    record: dict = {
        "id": skill_id(skill_dir), "path": str(skill_dir), "source": source,
        "name": skill_dir.name, "description": "", "version": None,
        "requirements": {}, "compatibility": "compatible", "problems": [],
        "trust": "untrusted", "content_sha256": None, "files": [], "has_scripts": False,
        "provenance": {"kind": "local", "root": source},
    }
    try:
        st = os.lstat(manifest_path)
        if stat.S_ISLNK(st.st_mode):
            raise ManifestError("SKILL.md is a symbolic link")
        if not stat.S_ISREG(st.st_mode):
            raise ManifestError("SKILL.md is not a regular file")
        with open(manifest_path, "rb") as fh:
            raw = fh.read(256 * 1024 + 1)
        meta, body = read_manifest(raw)
        record["content_sha256"] = hashlib.sha256(raw).hexdigest()
    except (OSError, ManifestError) as exc:
        record["compatibility"] = "invalid"
        record["problems"].append(str(exc))
        return record
    name, desc = meta.get("name"), meta.get("description")
    if isinstance(name, str) and name.strip():
        record["name"] = name.strip()[:120]
    else:
        record["problems"].append("front matter has no 'name'")
    if isinstance(desc, str) and desc.strip():
        record["description"] = desc.strip()[:2000]
    else:
        record["problems"].append("front matter has no 'description'")
    nested = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    version = meta.get("version") or nested.get("version")
    record["version"] = str(version)[:64] if version else None
    record["requirements"] = {k: meta[k] for k in REQUIREMENT_KEYS if k in meta}
    if record["problems"]:
        record["compatibility"] = "incomplete"
    record["instructions_bytes"] = len(body.encode())
    files, has_scripts = _list_files(skill_dir, approved_roots)
    record["files"], record["has_scripts"] = files, has_scripts
    return record


def _list_files(skill_dir: Path, approved_roots: list[Path]) -> tuple[list[dict], bool]:
    files, has_scripts = [], False
    for dirpath, dirnames, filenames in os.walk(skill_dir, followlinks=False):
        rel_depth = len(Path(dirpath).relative_to(skill_dir).parts)
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and rel_depth < MAX_WALK_DEPTH)
        for fn in sorted(filenames):
            full = Path(dirpath) / fn
            try:
                st = os.lstat(full)
            except OSError:
                continue
            entry = {"path": str(full.relative_to(skill_dir)), "size": st.st_size}
            if stat.S_ISLNK(st.st_mode):
                target = resolve(full)
                entry["symlink"] = True
                entry["symlink_safe"] = not is_sensitive(target) and any(is_within(target, resolve(r)) for r in approved_roots)
            executable = bool(st.st_mode & 0o111) or full.suffix in EXECUTABLE_SUFFIXES
            if executable:
                entry["script"] = True
                has_scripts = True
            files.append(entry)
            if len(files) >= MAX_FILES_LISTED:
                return files, has_scripts
    return files, has_scripts


def discover(roots: list[Path], approved_roots: list[Path]) -> tuple[list[dict], list[dict]]:
    """Scan each root's immediate subdirectories for SKILL.md. Returns (skills, problems)."""
    skills: list[dict] = []
    problems: list[dict] = []
    seen: set[str] = set()
    for root in roots:
        root_abs = Path(os.path.abspath(os.path.expanduser(root)))
        if not root_abs.is_dir():
            continue
        try:
            entries = sorted(os.scandir(root_abs), key=lambda e: e.name)
        except OSError as exc:
            problems.append({"path": str(root_abs), "problem": str(exc)})
            continue
        for entry in entries[:MAX_SKILLS_PER_ROOT]:
            if entry.name.startswith("."):
                continue
            candidate = Path(entry.path)
            target = resolve(candidate)
            if entry.is_symlink():
                # A symlinked skill is accepted only when it lands inside an
                # approved root and outside credential directories.
                if is_sensitive(target) or not any(is_within(target, resolve(r)) for r in approved_roots):
                    problems.append({"path": str(candidate), "problem": "symbolic link to an unapproved location ignored"})
                    continue
            if not target.is_dir() or not (target / "SKILL.md").exists():
                continue
            if str(target) in seen:
                continue
            seen.add(str(target))
            skills.append(inspect_dir(target, str(root_abs), approved_roots))
    return skills, problems
