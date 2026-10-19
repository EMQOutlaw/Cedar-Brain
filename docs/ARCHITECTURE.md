# CEDAR Brain — Mycelium backend: architecture report

## 1. Repository audit (2026-10-18)

| Item | Finding |
|---|---|
| Repository | `EMQOutlaw/Cedar-Brain`, branches `main`, `Dev`, `Outlaw`, all at `efac2fe Initial commit` |
| Tracked content | `README.md` (one heading) and nothing else |
| Python packages, CLI entry points, tests | None |
| QML cockpit | None in this repo, and no CEDAR Brain cockpit in the CEDAR Shell checkouts on this machine |
| Graphify graph / exporter | None on this machine. Graphify is not installed (`graphify` is not on `PATH`, no `graphify-out/` directories) |
| `cedar-brain` launcher | Not installed |
| Toolchain | Python 3.14.7 and SQLite 3.53.4 on the system. The system Python has no `pip`, so dev tooling lives in a venv. Claude Code 2.1.296 and codex-cli 0.162.1 are installed. Ollama is installed |

The directive refers to an earlier prototype: Graphify data, a graph exporter, a launcher and a QML cockpit. None of it exists on this machine or on any branch of the repo. This backend therefore starts from an empty tree. There is no working code to preserve or deprecate, and no existing command for `cedar-brain brain` compatibility to keep. See §6.

### Graphify schema (verified from published source, graphifyy 0.9.92)

Graphify's `export.to_json` writes `graphify-out/graph.json`, a NetworkX node-link document:

* top level: `directed`, `multigraph`, `graph` (with `schema_version`, `graphify_version`, `hyperedges`), `nodes`, `links`, `built_at_commit`
* node: `id`, `label`, `file_type` (`code`, `concept`, `doc_ref`, `rationale`…), `source_file`, `source_location`, `community`, `community_name`, `norm_label`
* link: `source`, `target`, `relation` (`imports`, `imports_from`, `calls`, `references`, `contains`, `defines`, `re_exports`, `includes`, `cites`, `rationale_for`…), `confidence` (`EXTRACTED` / `INFERRED` / `AMBIGUOUS`), `confidence_score`, `source_file`
* refresh command: `graphify update <path>`

Cortex consumes this format as is. It does not define a second graph format.

## 2. Component map

```
            QML frontend (Codex)            cedar-brain CLI
                    │  JSON Lines over $XDG_RUNTIME_DIR/cedar-brain/brain.sock (0600, SO_PEERCRED uid check)
                    ▼
   ipc/server.py ── protocol.py (envelope, validation, error codes)
                    │
                 app.py  (method table → components)
   ┌──────────┬──────────┬──────────┬───────────┬────────────┬────────────┐
 projects   cortex     spores     agents      hyphae       pathfinder
 (git)      (graph)    (skills)   (CLI        (workflow    (plan via agent,
                                   adapters,   engine,      strict validation,
                                   runner)     journal)     never executes)
   └──────────┴──────────┴─────┬────┴───────────┴────────────┘
                     storage/ (SQLite, WAL, migrations + backup)
                     security/ (paths, env allowlist, redaction)
```

Dependency direction is strictly downward. `app.py` is the only module that knows about every component. `hyphae` uses `agents` and `cortex`. `pathfinder` uses `agents`, `cortex`, `spores` and `hyphae.models` (for validation). Nothing imports `ipc` except `app`/`__main__`.

## 3. Key decisions

* **Stdlib only at runtime.** pytest is the only dev dependency.
* **Single asyncio process.** SQLite calls are short and run on the loop thread. Graph loading runs in `asyncio.to_thread`.
* **One server instance.** A `flock` on the runtime directory enforces it, and a stale socket is unlinked only after a connect probe fails.
* **Agents run as subprocesses** in their own process group via argv. Prompts go over stdin, never argv, so they stay out of `ps`, and never through a shell. pid, pgid and `/proc` start time are persisted, so recovery after a crash can kill only processes that are provably ours.
* **Write access is Codex only.** It runs in `--sandbox workspace-write` inside a backend-created git worktree on a new branch. A Codex sandbox probe on this machine confirmed that writes outside the allowed root fail with `Read-only file system`. Claude's `-p` mode has no OS-level sandbox, so Claude steps are read-only (`--tools Read,Grep,Glob`, `--permission-mode dontAsk`, `--permission-prompts none`).
* **Nothing merges or pushes, ever.** Worktrees are left for the user to review.
* **Graph reloads are explicit** (`cortex.reload` / `cedar-brain graph update`). A changed file on disk is reported, not silently re-indexed.

## 4. Milestones (each independently testable)

1. Protocol + IPC server + `system.*` (socket permissions, peer check, size limits)
2. Storage + migrations + project registry (`projects.*`)
3. Cortex graph loader/index/queries (`cortex.*`)
4. Agent adapters + process runner with fake CLIs (`agents.*`)
5. Spore Library local discovery (`skills.*`)
6. Hyphae engine + journal + recovery (`workflows.*`, `runs.*`)
7. Pathfinder planning + validation (`plans.*`)
8. CLI, mock server, contract, docs

## 5. Security boundary

The socket is owner-only (directory 0700, socket 0600) and rejects peers whose `SO_PEERCRED` uid differs from the server's. That protects against **other users only**. Any process running as the same user can connect and drive the backend, just as it could run `claude` or `codex` directly. Running mutating work needs `runs.start` with `approve: true`, plus per-step approval for write steps. Those are guards against accidents, not against a hostile same-user process.

## 6. Compatibility notes

There was no existing launcher, so `cedar-brain brain` is not a legacy command to keep. To honour the directive's intent it is accepted as an alias for `serve`.

## 7. Module layout (as built)

```
backend/cedar_brain/
  __main__.py          CLI (serve/brain, doctor, call, projects, skills, agents, graph, mock-serve)
  app.py               composition root + protocol method table, capabilities, health
  protocol.py          envelopes, error codes, parameter validation
  config.py            XDG paths, config.json
  events.py            in-process event bus
  mock.py              fixture-backed mock server
  ipc/server.py        socket server: peer check, flock, bounded queues, stream.gap
  ipc/client.py        sync client (CLI, tests)
  projects/            registry (manager.py) and read-only git (git.py)
  cortex/              graph_loader.py, graph_index.py, queries.py, service.py (cache, freshness, update)
  spores/              manifests.py (front-matter parser), discovery.py, registry.py
  agents/              base.py (interface, detection), claude.py, codex.py, runner.py (process supervision), registry.py
  hyphae/              models.py (states, transitions, executors, validator), journal.py (durable history), executor.py (engine)
  pathfinder/          planner.py, validation.py
  storage/             database.py (WAL, transactions, backup), migrations.py
  security/            paths.py, permissions.py (peer creds, env allowlist), redaction.py
  diagnostics/health.py  doctor
  fixtures/            mock responses + recorded event stream (generated)
```

There is no separate `scheduler.py`. The ready-set computation is about 20 lines inside `hyphae/executor.py`, and splitting it out would only add indirection.
