"""Cortex: per-project graph cache plus freshness reporting.

A graph is loaded once (lazily, off the event loop) and kept until an
explicit reload. If the file changes on disk the cached index keeps
serving and ``cortex.status`` reports ``changed_on_disk``. Re-indexing is
always a deliberate action.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time

from ..events import EventBus
from ..protocol import BrainError, ErrorCode
from ..projects import git
from ..security.paths import resolve_in_project
from ..util import run_cmd
from ..security.permissions import child_env
from .graph_index import GraphIndex
from .graph_loader import load_graph
from . import queries


class Cortex:
    def __init__(self, bus: EventBus, graphify_executable: str | None = None):
        self.bus = bus
        self.graphify_executable = graphify_executable
        self._cache: dict[str, GraphIndex] = {}
        self._errors: dict[str, BrainError] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def index(self, project: dict) -> GraphIndex:
        path = project["graph_path"]
        idx = self._cache.get(project["id"])
        if idx is not None and idx.data.path == path:
            return idx
        return await self.reload(project, emit=False)

    async def reload(self, project: dict, emit: bool = True) -> GraphIndex:
        lock = self._locks.setdefault(project["id"], asyncio.Lock())
        async with lock:
            path = project["graph_path"]
            try:
                data = await asyncio.to_thread(load_graph, path)
            except BrainError as exc:
                self._cache.pop(project["id"], None)
                self._errors[project["id"]] = exc
                raise
            idx = await asyncio.to_thread(GraphIndex, data)
            self._cache[project["id"]] = idx
            self._errors.pop(project["id"], None)
        if emit:
            self.bus.emit("cortex.reloaded", {"project_id": project["id"], "nodes": idx.node_count, "edges": idx.edge_count})
        return idx

    def forget(self, project_id: str) -> None:
        self._cache.pop(project_id, None)
        self._errors.pop(project_id, None)

    async def status(self, project: dict, load: bool = True) -> dict:
        path = project["graph_path"]
        out: dict = {"project_id": project["id"], "graph_path": path, "loaded": False}
        if load:
            try:
                await self.index(project)
            except BrainError:
                pass
        idx = self._cache.get(project["id"])
        if idx is None:
            err = self._errors.get(project["id"])
            out["state"] = "unavailable" if not os.path.exists(path) else ("invalid" if err else "not_loaded")
            if err:
                out["error"] = err.to_dict()
            return out
        d = idx.data
        try:
            st = os.stat(path)
            changed = st.st_mtime_ns != d.mtime_ns or st.st_size != d.size
        except OSError:
            changed = True
        head = (await git.status(project["root"])).head
        if d.built_at_commit and head:
            freshness = "fresh" if d.built_at_commit == head else "stale"
            behind = None if freshness == "fresh" else await git.commits_between(project["root"], d.built_at_commit, head)
        else:
            freshness, behind = "unknown", None
        out.update({
            "loaded": True, "state": "loaded",
            "nodes": idx.node_count, "edges": idx.edge_count,
            "communities": len(idx.communities),
            "relations": dict(sorted(idx.relations.items(), key=lambda kv: -kv[1])[:30]),
            "directed": d.directed,
            "schema_version": d.schema_version, "graphify_version": d.graphify_version,
            "malformed": {"nodes": d.malformed_nodes, "links": d.malformed_links, "dangling_links": d.dangling_links},
            "freshness": {
                "state": freshness, "built_at_commit": d.built_at_commit, "head": head,
                "commits_behind": behind, "graph_mtime": d.mtime_ns / 1e9,
                "age_s": round(time.time() - d.mtime_ns / 1e9, 1),
                "changed_on_disk": changed,
            },
        })
        return out

    # -- queries -------------------------------------------------------------
    async def search(self, project, **kw) -> dict:
        idx = await self.index(project)
        return {"results": queries.search(idx, **kw)}

    async def dependencies(self, project, **kw) -> dict:
        return queries.traverse(await self.index(project), **kw)

    async def impact(self, project, node_ids: list[str], path: str | None, **kw) -> dict:
        idx = await self.index(project)
        targets = list(node_ids or [])
        if path:
            rel = path
            abs_path = resolve_in_project(project["root"], path)
            if abs_path is not None:
                rel = os.path.relpath(abs_path, project["root"])
            matches = idx.by_file.get(rel) or idx.by_file.get(str(abs_path)) or []
            if not matches:
                raise BrainError(ErrorCode.NOT_FOUND, f"no graph nodes for file {path!r}")
            targets.extend(m for m in matches if m not in targets)
        if not targets:
            raise BrainError(ErrorCode.INVALID_PARAMS, "provide node_ids or path")
        return queries.impact(idx, targets[:50], **kw)

    async def node(self, project, node_id: str) -> dict:
        idx = await self.index(project)
        out = queries.node_detail(idx, node_id)
        out["source"] = self.resolve_source(project, out["node"])
        return out

    async def communities(self, project, **kw) -> dict:
        return queries.communities(await self.index(project), **kw)

    @staticmethod
    def resolve_source(project: dict, node: dict) -> dict | None:
        ref = node.get("source_file")
        if not ref:
            return None
        resolved = resolve_in_project(project["root"], ref)
        line = None
        loc = node.get("source_location") or ""
        digits = "".join(ch for ch in loc.split("-")[0] if ch.isdigit())
        if digits:
            line = int(digits)
        return {
            "reference": ref, "path": str(resolved) if resolved else None,
            "within_project": resolved is not None,
            "exists": bool(resolved and resolved.is_file()), "line": line,
        }

    # -- controlled refresh ------------------------------------------------------
    def graphify_path(self) -> str | None:
        return self.graphify_executable or shutil.which("graphify")

    async def update(self, project: dict, timeout: float = 1800) -> dict:
        """Run ``graphify update <root>`` then reload. Explicit CLI action only."""
        exe = self.graphify_path()
        if not exe:
            raise BrainError(ErrorCode.NOT_IMPLEMENTED, "graphify is not installed; install it and build the graph with `graphify update <project>`")
        code, out, err = await run_cmd([exe, "update", project["root"]], cwd=project["root"],
                                       timeout=timeout, env=child_env())
        if code != 0:
            raise BrainError(ErrorCode.GRAPH_INVALID, f"graphify update failed (exit {code})", {"stderr": err[-2000:]})
        idx = await self.reload(project)
        return {"nodes": idx.node_count, "edges": idx.edge_count, "stdout_tail": out[-2000:]}
