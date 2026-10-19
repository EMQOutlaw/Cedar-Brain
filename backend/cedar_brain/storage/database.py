"""SQLite access: one connection, WAL, foreign keys, explicit transactions."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .migrations import MIGRATIONS, SCHEMA_VERSION

log = logging.getLogger(__name__)


class SchemaTooNew(RuntimeError):
    pass


def dumps(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value):
    return None if value is None else json.loads(value)


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Create the file owner-only before sqlite opens it.
            if not self.path.exists():
                fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
                os.close(fd)
            os.chmod(self.path, 0o600)
        self.conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 10000")
        if str(path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")

    # -- migrations -------------------------------------------------------
    @property
    def schema_version(self) -> int:
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def migrate(self) -> list[int]:
        current = self.schema_version
        if current > SCHEMA_VERSION:
            raise SchemaTooNew(f"database schema v{current} is newer than this backend (v{SCHEMA_VERSION})")
        pending = [(v, sql) for v, sql in MIGRATIONS if v > current]
        if pending and current > 0 and str(self.path) != ":memory:":
            self.backup(self.path.with_name(f"{self.path.name}.bak-v{current}-{int(time.time())}"))
        applied = []
        for version, sql in pending:
            with self.transaction():
                for stmt in _split(sql):
                    self.conn.execute(stmt)
                self.conn.execute(f"PRAGMA user_version = {int(version)}")
            applied.append(version)
            log.info("applied migration v%d", version)
        return applied

    def backup(self, dest: Path) -> Path:
        fd = os.open(dest, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        os.close(fd)
        target = sqlite3.connect(str(dest))
        try:
            self.conn.backup(target)
        finally:
            target.close()
        return dest

    # -- helpers ----------------------------------------------------------
    @contextmanager
    def transaction(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    def execute(self, sql: str, args=()) -> sqlite3.Cursor:
        return self.conn.execute(sql, args)

    def one(self, sql: str, args=()) -> sqlite3.Row | None:
        return self.conn.execute(sql, args).fetchone()

    def all(self, sql: str, args=()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, args).fetchall()

    def get_pref(self, key: str, default=None):
        row = self.one("SELECT value FROM preferences WHERE key = ?", (key,))
        return loads(row["value"]) if row else default

    def set_pref(self, key: str, value) -> None:
        self.execute(
            "INSERT INTO preferences(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, dumps(value)),
        )

    def ok(self) -> bool:
        try:
            return self.one("PRAGMA quick_check")[0] == "ok"
        except sqlite3.Error:
            return False

    def close(self) -> None:
        self.conn.close()


def _split(sql: str) -> list[str]:
    return [s.strip() for s in sql.split(";") if s.strip()]
