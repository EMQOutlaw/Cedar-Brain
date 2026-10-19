# Frontend handoff (QML / Codex)

Everything you need is in [`protocol-v1.md`](protocol-v1.md) and [`examples/`](examples/). You should not need to read Python code. If the contract is ambiguous, ask for a contract update; don't infer behaviour from the implementation.

## Connecting from QML

* Quickshell: use `Socket { path: … }` (Quickshell.Io) with line splitting (`SplitParser`). Otherwise use a small helper process. Write one JSON object plus `\n` per request.
* Socket path: `$CEDAR_BRAIN_SOCKET`, else `$XDG_RUNTIME_DIR/cedar-brain/brain.sock`.
* On connect: `system.health`, then remember `instance_id`, then `events.subscribe {"topics": ["project", "cortex", "plan", "run", "system"]}`, then initial loads.
* Keep a map `request id → callback`. Never assume responses arrive in order.
* On disconnect, reconnect with backoff (250 ms → 5 s). Then resubscribe, compare `instance_id`, and resync open runs with `runs.events(after_seq)`. On `stream.gap`, resync the same way.

## Working without the real backend

```sh
cedar-brain --socket /tmp/cedar-brain-mock.sock mock-serve --speed 1
CEDAR_BRAIN_SOCKET=/tmp/cedar-brain-mock.sock qs -p ./frontend   # or however the cockpit is launched
```

The mock returns the recorded fixture for each method. After `runs.start` or `plans.create` it replays a recorded run (`run.updated` → `run.step.updated` ×N → `run.output` ×N → `succeeded`).

## Screens and the calls behind them

| UI area | Calls | Events |
|---|---|---|
| Status strip | `system.health`, `system.capabilities`, `agents.list` | `system.shutdown` |
| Project picker | `projects.list`, `projects.add`, `projects.select`, `projects.status` | `project.updated` |
| Cortex / graph view | `cortex.status`, `cortex.search`, `cortex.node`, `cortex.dependencies`, `cortex.impact`, `cortex.communities`, `cortex.reload` | `cortex.reloaded` |
| Spore Library | `skills.list`, `skills.inspect`, `skills.import_local` | none |
| Agent Grove | `agents.list`, `agents.status` | none |
| Pathfinder | `plans.create` → wait for `plan.updated` → `plans.get`; after edits, `plans.validate`; then `workflows.create {plan_id}` | `plan.updated` |
| Workflow runner | `runs.start {approve: true}`, `runs.get`, `runs.approve`, `runs.cancel`, `runs.events`, `runs.list` | `run.updated`, `run.step.updated`, `run.output` |

## Rules the UI must follow

1. Show a feature only if `system.capabilities.features[...].status == "supported"`. For `unavailable`, show the `reason`. Hide `not_implemented`.
2. `runs.start` needs an explicit user action (button press) and sends `approve: true`. Never auto-start after planning.
3. Steps in `awaiting_approval` need a visible Approve/Reject control (`runs.approve`). Write steps (`mode: "write"`) should say they modify files in an isolated worktree, and show the worktree path and branch from the step result.
4. Render agent output, skill instructions and graph labels as **plain text** (`Text.PlainText`), never rich text or markdown-to-HTML. They are untrusted.
5. Label Cortex relationships as static code relationships. Show `evidence` / `path_evidence` (`static_extracted`, `inferred`, `ambiguous`). Never present impact results as confirmed breakage.
6. Treat `interrupted` runs as needing attention, not as failures to retry automatically.
7. Show `cortex.status.freshness` (stale graph, changed on disk) so users know when results may be out of date.

## State colours (suggested, not mandatory)

| State | Meaning |
|---|---|
| `pending`, `ready`, `queued` | neutral |
| `running` | active |
| `awaiting_approval` | needs the user |
| `succeeded` | positive |
| `failed`, `interrupted` | negative |
| `cancelled`, `skipped`, `incomplete` | muted |
