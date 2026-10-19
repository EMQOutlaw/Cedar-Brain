"""Project registry: explicit list of local git repositories."""

from __future__ import annotations

import os
from pathlib import Path

from ..events import EventBus
from ..protocol import BrainError, ErrorCode
from ..security.paths import ensure_not_sensitive, resolve
from ..storage.database import Database
from ..util import new_id, now
from . import git

ACTIVE_KEY = "active_project_id"
MAX_GRAPH_BYTES = 256 * 1024 * 1024


def default_graph_path(root: str) -> str:
    return str(Path(root) / "graphify-out" / "graph.json")


class ProjectManager:
    def __init__(self, db: Database, bus: EventBus):
        self.db = db
        self.bus = bus

    # -- queries ----------------------------------------------------------
    def _row(self, row) -> dict:
        return {
            "id": row["id"], "name": row["name"], "root": row["root"],
            "graph_path": row["graph_path"] or default_graph_path(row["root"]),
            "graph_path_explicit": row["graph_path"] is not None,
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def list(self) -> list[dict]:
        return [self._row(r) for r in self.db.all("SELECT * FROM projects ORDER BY name COLLATE NOCASE")]

    def get(self, project_id: str) -> dict:
        row = self.db.one("SELECT * FROM projects WHERE id = ?", (project_id,))
        if not row:
            raise BrainError(ErrorCode.NOT_FOUND, f"no project {project_id!r}")
        return self._row(row)

    @property
    def active_id(self) -> str | None:
        pid = self.db.get_pref(ACTIVE_KEY)
        if pid and self.db.one("SELECT 1 FROM projects WHERE id = ?", (pid,)):
            return pid
        return None

    def resolve(self, project_id: str | None) -> dict:
        """The named project, or the active one."""
        if project_id:
            return self.get(project_id)
        active = self.active_id
        if not active:
            raise BrainError(ErrorCode.NO_ACTIVE_PROJECT, "no project selected; call projects.select first")
        return self.get(active)

    # -- mutations --------------------------------------------------------
    async def add(self, path: str, name: str | None = None, graph_path: str | None = None, select: bool = False) -> dict:
        resolved = ensure_not_sensitive(path, "project path")
        if not resolved.is_dir():
            raise BrainError(ErrorCode.INVALID_REPOSITORY, "path is not a directory")
        top = await git.toplevel(str(resolved))
        if top is None:
            # Never `git init` on the user's behalf.
            raise BrainError(ErrorCode.INVALID_REPOSITORY, "path is not inside a git repository")
        root = str(resolve(top))
        existing = self.db.one("SELECT id FROM projects WHERE root = ?", (root,))
        if existing:
            raise BrainError(ErrorCode.CONFLICT, "project already registered", {"project_id": existing["id"]})
        gp = self._check_graph_path(graph_path) if graph_path else None
        pid = new_id("prj")
        ts = now()
        self.db.execute(
            "INSERT INTO projects(id, name, root, graph_path, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (pid, (name or Path(root).name)[:120], root, gp, ts, ts),
        )
        if select or self.active_id is None:
            self.db.set_pref(ACTIVE_KEY, pid)
        project = self.get(pid)
        self.bus.emit("project.updated", {"project_id": pid, "change": "added"})
        return project

    def _check_graph_path(self, graph_path: str) -> str:
        gp = ensure_not_sensitive(graph_path, "graph path")
        if gp.suffix != ".json":
            raise BrainError(ErrorCode.INVALID_PARAMS, "graph path must be a .json file")
        if gp.exists() and not gp.is_file():
            raise BrainError(ErrorCode.INVALID_PARAMS, "graph path is not a regular file")
        return str(gp)

    def set_graph_path(self, project_id: str, graph_path: str | None) -> dict:
        self.get(project_id)
        gp = self._check_graph_path(graph_path) if graph_path else None
        self.db.execute("UPDATE projects SET graph_path = ?, updated_at = ? WHERE id = ?", (gp, now(), project_id))
        self.bus.emit("project.updated", {"project_id": project_id, "change": "graph_path"})
        return self.get(project_id)

    def select(self, project_id: str) -> dict:
        project = self.get(project_id)
        self.db.set_pref(ACTIVE_KEY, project_id)
        self.bus.emit("project.updated", {"project_id": project_id, "change": "selected"})
        return project

    def remove(self, project_id: str) -> None:
        self.get(project_id)
        self.db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        self.bus.emit("project.updated", {"project_id": project_id, "change": "removed"})

    async def status(self, project_id: str | None) -> dict:
        project = self.resolve(project_id)
        gs = await git.status(project["root"])
        graph = project["graph_path"]
        graph_state = "missing"
        if os.path.isfile(graph):
            graph_state = "too_large" if os.path.getsize(graph) > MAX_GRAPH_BYTES else "present"
        return {
            "project": project,
            "active": project["id"] == self.active_id,
            "git": gs.to_dict(),
            "graph": {"path": graph, "state": graph_state},
            "capabilities": {
                "git_status": gs.valid,
                "cortex": graph_state == "present",
                "worktrees": gs.valid and gs.head is not None and await git.worktree_support(project["root"]),
            },
        }
