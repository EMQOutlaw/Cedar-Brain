# Codex backlog (newest first)

## 2026-10-19 — Claude setting isolation, installed cockpit + app-menu entry

**What/why:** Audit H1: `claude -p` skips the workspace-trust dialog and loaded the repo's `.claude/settings.json` hooks, so a registered repo could run code on `plans.create`. The adapter now passes `--setting-sources user` (also a required flag). The installer now copies `cockpit/` into `~/.local/share/cedar-brain/cockpit`, writes `~/.local/bin/cedar-brain-cockpit` and a "CEDAR Brain" desktop entry with icon (replaces the old CEDAR AI menu entry).

**Files:** `backend/cedar_brain/agents/claude.py`, `tests/backend/test_agents.py`, `tests/fakes/fake_agent.py`, `scripts/install-user.sh`, `packaging/cedar-brain.desktop`, `packaging/cedar-brain.svg`, `README.md`.

**Tests:** pytest 110 passed. H1 verified offline with real Claude 2.1.296: hooks fire without the flag, not with it.

**Still open from the audit:** M1–M6, L1–L7 (stop-on-failure approvals, projects.remove cascade, service PATH, approval UI prompt, socket reconnect, prompt delimiter).

## 2026-10-18 — Mycelium backend v0.1.0 (branch `feature/mycelium-backend`, from `Dev`)

**What/why:** First implementation of the CEDAR Brain backend in this repo. The repo held only a README; no earlier prototype existed on any branch or on disk. Delivered: stdlib-only Python backend over a Unix socket (JSON Lines protocol v1), project registry, Cortex (Graphify `graph.json` queries), Spore Library (local skills, never executed), Claude/Codex CLI adapters, Hyphae workflow engine, Pathfinder planning, SQLite persistence and crash recovery, CLI, mock server, contract, docs.

**Files:** `pyproject.toml`, `.gitignore`, `README.md`, `backend/cedar_brain/**`, `tests/**`, `contracts/**`, `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, `scripts/install-user.sh`, `scripts/gen_contract_examples.py`, `packaging/cedar-brain.service`, `CODEX_BACKLOG.md`.

**Tests:** `.venv/bin/python -m pytest` → 110 passed (fake CLIs, no model calls); stable across repeated runs. Real smoke test against the installed CLIs (Claude Code 2.1.296, codex-cli 0.162.1), read-only, both succeeded. Real Pathfinder plan with Claude → `ready`, valid, nothing executed.

**Live:** installed via `scripts/install-user.sh --service`; `cedar-brain.service` (systemd user) is enabled and active on `/run/user/1000/cedar-brain/brain.sock`. `~/Work/cedar-brain` is registered as a project.

**Unverified:** Graphify is not installed here, so `cedar-brain graph update` is untested against real Graphify. Cortex was tested only with Graphify-schema fixtures. Codex write mode was tested with the fake CLI only; no real Codex write run was made.

**CHANGELOG:** `Added: CEDAR Brain — Mycelium backend v0.1.0 (protocol v1, Cortex, Spore Library, Agent Grove, Hyphae, Pathfinder).`

**Suggested commit:** `feat: CEDAR Brain Mycelium backend v0.1.0 with protocol v1 contract`
