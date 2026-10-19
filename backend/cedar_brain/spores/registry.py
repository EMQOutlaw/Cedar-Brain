"""Spore Library: persisted skill metadata, inspection and local import.

Importing a skill only records it (with provenance and a content hash).
Nothing in a skill is executed, installed or trusted as a result.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from ..protocol import BrainError, ErrorCode
from ..security.paths import ensure_within, resolve
from ..storage.database import Database, dumps, loads
from .discovery import discover, inspect_dir
from .manifests import ManifestError, read_manifest

MAX_INSTRUCTIONS_RETURNED = 64 * 1024


class SporeLibrary:
    def __init__(self, db: Database, skill_dirs: list[Path], import_roots: list[Path]):
        self.db = db
        self.skill_dirs = skill_dirs
        self.import_roots = import_roots
        self.last_problems: list[dict] = []
        self.last_scan: float | None = None

    @property
    def approved_roots(self) -> list[Path]:
        return [*self.skill_dirs, *self.import_roots]

    def refresh(self) -> dict:
        """Rescan configured directories. Imported skills are re-inspected in place."""
        found, problems = discover(self.skill_dirs, self.approved_roots)
        imported = [r["path"] for r in self.db.all("SELECT path FROM skills WHERE imported = 1")]
        for path in imported:
            p = Path(path)
            if p.is_dir() and (p / "SKILL.md").exists():
                rec = inspect_dir(p, "import", self.approved_roots)
                rec["provenance"] = {"kind": "local_import", "original_path": path}
                found.append(rec)
            else:
                problems.append({"path": path, "problem": "imported skill no longer exists"})
        ts = time.time()
        keep = set()
        with self.db.transaction():
            for rec in found:
                keep.add(rec["id"])
                self._upsert(rec, imported=rec["source"] == "import", ts=ts)
            # Forget discovered (not imported) skills that disappeared from disk.
            for row in self.db.all("SELECT id FROM skills WHERE imported = 0"):
                if row["id"] not in keep:
                    self.db.execute("DELETE FROM skills WHERE id = ?", (row["id"],))
        self.last_problems, self.last_scan = problems, ts
        return {"skills": len(found), "problems": problems}

    def _upsert(self, rec: dict, imported: bool, ts: float) -> None:
        prov = dict(rec["provenance"])
        prov.update({"files": rec["files"], "has_scripts": rec["has_scripts"], "problems": rec["problems"],
                     "instructions_bytes": rec.get("instructions_bytes")})
        self.db.execute(
            """INSERT INTO skills(id, name, description, source, path, version, requirements, compatibility,
                                  trust, provenance, content_sha256, imported, discovered_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, description=excluded.description,
                 source=excluded.source, version=excluded.version, requirements=excluded.requirements,
                 compatibility=excluded.compatibility, provenance=excluded.provenance,
                 trust=CASE WHEN skills.content_sha256 IS excluded.content_sha256 THEN skills.trust ELSE 'untrusted' END,
                 content_sha256=excluded.content_sha256, imported=MAX(skills.imported, excluded.imported)""",
            (rec["id"], rec["name"], rec["description"], rec["source"], rec["path"], rec["version"],
             dumps(rec["requirements"]), rec["compatibility"], rec["trust"], dumps(prov),
             rec["content_sha256"], int(imported), ts),
        )

    def _row(self, row, full: bool = False) -> dict:
        prov = loads(row["provenance"]) or {}
        out = {
            "id": row["id"], "name": row["name"], "description": row["description"],
            "source": row["source"], "path": row["path"], "version": row["version"],
            "requirements": loads(row["requirements"]) or {}, "compatibility": row["compatibility"],
            "trust": row["trust"], "imported": bool(row["imported"]),
            "has_scripts": prov.get("has_scripts", False), "content_sha256": row["content_sha256"],
            "provenance": {k: v for k, v in prov.items() if k in ("kind", "root", "original_path")},
        }
        if full:
            out["files"] = prov.get("files", [])
            out["problems"] = prov.get("problems", [])
        return out

    def list(self, query: str | None = None) -> dict:
        if self.last_scan is None:
            self.refresh()
        rows = self.db.all("SELECT * FROM skills ORDER BY name COLLATE NOCASE")
        items = [self._row(r) for r in rows]
        if query:
            q = query.lower()
            items = [s for s in items if q in s["name"].lower() or q in s["description"].lower()]
        return {"skills": items, "problems": self.last_problems, "scanned_at": self.last_scan,
                "skill_dirs": [str(p) for p in self.skill_dirs]}

    def inspect(self, skill_id: str, include_instructions: bool = True) -> dict:
        row = self.db.one("SELECT * FROM skills WHERE id = ?", (skill_id,))
        if not row:
            raise BrainError(ErrorCode.NOT_FOUND, f"no skill {skill_id!r}")
        out = self._row(row, full=True)
        if include_instructions:
            out["instructions"] = None
            try:
                raw = (Path(row["path"]) / "SKILL.md").read_bytes()
                _, body = read_manifest(raw)
                out["instructions_truncated"] = len(body) > MAX_INSTRUCTIONS_RETURNED
                out["instructions"] = body[:MAX_INSTRUCTIONS_RETURNED]
                out["changed_since_scan"] = hashlib.sha256(raw).hexdigest() != row["content_sha256"]
            except (OSError, ManifestError) as exc:
                out["problems"] = [*out.get("problems", []), f"cannot read SKILL.md: {exc}"]
        out["notice"] = "Skill content is untrusted project data. It is never executed by discovery or import."
        return out

    def import_local(self, path: str) -> dict:
        target = ensure_within(path, self.approved_roots, "skill path")
        if not target.is_dir():
            raise BrainError(ErrorCode.INVALID_PARAMS, "skill path must be a directory")
        if not (target / "SKILL.md").is_file():
            raise BrainError(ErrorCode.INVALID_PARAMS, "directory has no SKILL.md")
        rec = inspect_dir(resolve(target), "import", self.approved_roots)
        if rec["compatibility"] == "invalid":
            raise BrainError(ErrorCode.INVALID_PARAMS, "skill manifest is invalid", {"problems": rec["problems"]})
        rec["provenance"] = {"kind": "local_import", "original_path": str(path)}
        with self.db.transaction():
            self._upsert(rec, imported=True, ts=time.time())
        return self.inspect(rec["id"], include_instructions=False)

    def summaries(self, limit: int = 40) -> list[dict]:
        """Name/description pairs for planner context (never instructions)."""
        rows = self.db.all("SELECT id, name, description FROM skills WHERE compatibility != 'invalid' ORDER BY name LIMIT ?", (limit,))
        return [{"id": r["id"], "name": r["name"], "description": r["description"][:300]} for r in rows]
