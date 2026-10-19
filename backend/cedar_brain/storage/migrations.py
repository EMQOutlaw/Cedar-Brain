"""Schema migrations, tracked with ``PRAGMA user_version``.

Each migration runs in one transaction. An existing database is backed up
before any migration is applied (see database.Database.migrate).
"""

from __future__ import annotations

MIGRATIONS: list[tuple[int, str]] = [
    (1, """
    CREATE TABLE projects (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        root TEXT NOT NULL UNIQUE,
        graph_path TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE preferences (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE skills (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL,
        path TEXT NOT NULL UNIQUE,
        version TEXT,
        requirements TEXT NOT NULL DEFAULT '{}',
        compatibility TEXT NOT NULL,
        trust TEXT NOT NULL,
        provenance TEXT NOT NULL DEFAULT '{}',
        content_sha256 TEXT,
        imported INTEGER NOT NULL DEFAULT 0,
        discovered_at REAL NOT NULL
    );
    CREATE TABLE plans (
        id TEXT PRIMARY KEY,
        project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
        objective TEXT NOT NULL,
        agent_id TEXT,
        status TEXT NOT NULL,
        plan TEXT,
        validation TEXT,
        error TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE workflows (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        definition TEXT NOT NULL,
        source_plan_id TEXT REFERENCES plans(id) ON DELETE SET NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE runs (
        id TEXT PRIMARY KEY,
        workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
        project_id TEXT NOT NULL,
        status TEXT NOT NULL,
        error TEXT,
        created_at REAL NOT NULL,
        started_at REAL,
        finished_at REAL
    );
    CREATE UNIQUE INDEX runs_one_active_per_workflow ON runs(workflow_id)
        WHERE status IN ('queued', 'running', 'awaiting_approval');
    CREATE TABLE run_steps (
        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        step_id TEXT NOT NULL,
        position INTEGER NOT NULL,
        state TEXT NOT NULL,
        executor TEXT NOT NULL,
        started_at REAL,
        finished_at REAL,
        result TEXT,
        error TEXT,
        worktree TEXT,
        PRIMARY KEY (run_id, step_id)
    );
    CREATE TABLE run_events (
        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        seq INTEGER NOT NULL,
        ts REAL NOT NULL,
        event TEXT NOT NULL,
        data TEXT NOT NULL,
        PRIMARY KEY (run_id, seq)
    );
    CREATE TABLE agent_jobs (
        id TEXT PRIMARY KEY,
        owner_kind TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        status TEXT NOT NULL,
        mode TEXT NOT NULL,
        cwd TEXT NOT NULL,
        pid INTEGER,
        proc_start_ticks INTEGER,
        session_id TEXT,
        exit_code INTEGER,
        error TEXT,
        stdout_log TEXT,
        stderr_log TEXT,
        started_at REAL NOT NULL,
        finished_at REAL
    );
    CREATE UNIQUE INDEX agent_jobs_one_active_per_owner ON agent_jobs(owner_kind, owner_id)
        WHERE status IN ('starting', 'running');
    CREATE TABLE verifications (
        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        step_id TEXT NOT NULL,
        method TEXT NOT NULL,
        passed INTEGER NOT NULL,
        detail TEXT NOT NULL,
        ts REAL NOT NULL
    );
    """),
]

SCHEMA_VERSION = MIGRATIONS[-1][0]
