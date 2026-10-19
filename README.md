# Cedar-Brain

**CEDAR Brain — Mycelium** is a local backend for project understanding and CLI-agent workflows. It runs without CEDAR Shell.

| Part | What it does |
|---|---|
| **Projects** | explicit registry of local git repositories |
| **Cortex** | deterministic queries over a project's [Graphify](https://graphify.com) `graph.json` |
| **Spore Library** | discovers and inspects local `SKILL.md` skills (never executes them) |
| **Agent Grove** | runs the installed `claude` and `codex` CLIs with your existing login |
| **Hyphae** | dependency-aware workflow engine with approvals, cancellation and durable history |
| **Pathfinder** | asks an agent for a *proposed* workflow and validates it, but never runs it |

The QML frontend talks to the backend over a Unix socket using [`contracts/protocol-v1.md`](contracts/protocol-v1.md).

## Desktop frontend

The standalone **Mycelium** QML application is in `cockpit/`:

```sh
scripts/launch-cockpit.sh  # connect to the running cedar-brain service
```

`scripts/install-user.sh` also installs the cockpit, a `cedar-brain-cockpit`
launcher and a **CEDAR Brain** app-menu entry.

Requires Quickshell 0.3.1 and Qt Quick Controls/Shapes. Includes Pathfinder,
Cortex, Spore Library, Hyphae, Agent Grove and Settings. See
[frontend launch, architecture, tests and integration limits](docs/FRONTEND.md).

## Requirements

* Python ≥ 3.12 (stdlib only at runtime), git
* Optional: `claude` (Claude Code) and/or `codex` (Codex CLI), already logged in
* Optional: `graphify`, to build or refresh `graphify-out/graph.json`

## Install (per user, no root)

```sh
git clone https://github.com/EMQOutlaw/Cedar-Brain.git && cd Cedar-Brain
scripts/install-user.sh              # venv in ~/.local/share/cedar-brain/venv, launcher ~/.local/bin/cedar-brain
scripts/install-user.sh --service    # same, plus the systemd user unit (starts at login)
```

To do the same by hand: `python3 -m venv ~/.local/share/cedar-brain/venv && ~/.local/share/cedar-brain/venv/bin/pip install . && ln -s ~/.local/share/cedar-brain/venv/bin/cedar-brain ~/.local/bin/`.

Uninstall: `systemctl --user disable --now cedar-brain; rm -r ~/.local/share/cedar-brain ~/.local/bin/cedar-brain ~/.config/systemd/user/cedar-brain.service`. State lives in `~/.local/state/cedar-brain` (delete it to reset).

## Run

```sh
cedar-brain serve                      # foreground server ("cedar-brain brain" is an alias)
cedar-brain doctor                     # environment check, works without a server
cedar-brain projects add ~/Work/my-repo --select
cedar-brain projects list | status
cedar-brain graph status | reload | update   # update runs `graphify update <root>` then reloads
cedar-brain skills list [--refresh] | inspect skl_… | import PATH
cedar-brain agents list [--refresh]
cedar-brain call system.capabilities '{}'    # any protocol method
cedar-brain --socket /tmp/mock.sock mock-serve   # fixture-backed mock for frontend work
```

CLI commands use the running server when there is one; otherwise they run in-process against the same database.

## Configuration

`~/.config/cedar-brain/config.json` is optional. All keys are optional:

```json
{
  "skill_dirs": ["~/.claude/skills", "~/.codex/skills"],
  "import_roots": ["~"],
  "agents": {"claude": {"executable": "claude", "enabled": true, "model": null},
             "codex":  {"executable": "codex",  "enabled": true}},
  "max_parallel_steps": 2,
  "max_agent_processes": 2,
  "default_step_timeout_s": 900,
  "planner_timeout_s": 300,
  "graphify_executable": null
}
```

Paths: socket `$XDG_RUNTIME_DIR/cedar-brain/brain.sock` (override with `CEDAR_BRAIN_SOCKET` or `--socket`). Database, job logs and worktrees go in `$XDG_STATE_HOME/cedar-brain/`.

## Develop and test

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest                       # 108 tests; fake claude/codex in tests/fakes, no model calls
.venv/bin/python scripts/gen_contract_examples.py  # regenerate contracts/examples + mock fixtures
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `a cedar-brain server is already running` (exit 3) | one is already up: `systemctl --user status cedar-brain` or `cedar-brain doctor` |
| agent `missing` | the CLI is not on the server's `PATH`. Set `agents.<id>.executable` to an absolute path. systemd user services do not see shell-only PATH entries (for example mise shims) |
| agent `unsupported` | the installed CLI lacks a required flag (see `missing_flags`); update the CLI |
| step fails `AGENT_AUTH_FAILED` | log in yourself (`claude`, `codex login`). The backend never handles credentials |
| `cortex.status` → `unavailable` | no `graphify-out/graph.json`: run `graphify update <repo>` or set one with `projects.set_graph` |
| `freshness.state = stale` | graph built at an older commit: `cedar-brain graph update` |
| `changed_on_disk: true` | the file changed after loading: `cedar-brain graph reload` |
| run shows `interrupted` | the backend stopped mid-run. Nothing is retried automatically; start the workflow again if you want to |
| write step left a worktree | review it at the path in the step result (branch `cedar-brain/<run>/<step>`). Remove it with `git worktree remove <path>` |

More: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/SECURITY.md`](docs/SECURITY.md), [`contracts/FRONTEND_HANDOFF.md`](contracts/FRONTEND_HANDOFF.md).
