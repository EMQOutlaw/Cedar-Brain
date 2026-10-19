# CEDAR Brain — Mycelium protocol v1

Status: **authoritative** for frontend work. Behaviour changes need a new protocol version, or an additive change recorded in §12.
Every request/response pair in `contracts/examples/` was captured from a real server run. To regenerate them, run `scripts/gen_contract_examples.py`.

---

## 1. Transport

| | |
|---|---|
| Socket | Unix domain stream socket (no TCP, no HTTP) |
| Path | `$CEDAR_BRAIN_SOCKET` if set and absolute, else `$XDG_RUNTIME_DIR/cedar-brain/brain.sock`. If `XDG_RUNTIME_DIR` is unset: `$XDG_STATE_HOME/cedar-brain/run/brain.sock` (default `~/.local/state/cedar-brain/run/brain.sock`) |
| Permissions | directory `0700`, socket `0600`. The server drops any peer whose `SO_PEERCRED` uid differs from its own |
| Framing | UTF-8 JSON Lines: exactly one JSON object per `\n`-terminated line, in both directions |
| Max line | 1 048 576 bytes (`limits.max_message_bytes`). A longer line gets one `MESSAGE_TOO_LARGE` error, then the server closes the connection |
| Connections | at most 64 at once. Each connection can have up to 32 requests in flight |

## 2. Envelopes

**Request** (client → server). These four keys only; any other key is `INVALID_REQUEST`.

```json
{"v": 1, "id": "req-001", "method": "agents.list", "params": {}}
```

* `v`: must be the integer `1`. Anything else gets `UNSUPPORTED_VERSION`.
* `id`: non-empty string, ≤128 chars, chosen by the client. Echoed back in the response. Use a fresh id per request.
* `method`: `namespace.name`.
* `params`: object, optional (default `{}`). Unknown parameter names are rejected with `INVALID_PARAMS`.

**Success response**

```json
{"v": 1, "id": "req-001", "ok": true, "result": {}}
```

**Error response**

```json
{"v": 1, "id": "req-001", "ok": false, "error": {"code": "INVALID_PARAMS", "message": "…", "details": {}}}
```

`details` is optional. `id` is `null` only when the server could not read an id from the request (unparseable JSON, oversized line).

**Event** (server → client, only for subscribed topics)

```json
{"v": 1, "event": "run.updated", "data": {"run_id": "run_…", "seq": 7, "status": "running"}}
```

A message is an event if it has an `event` key and a response if it has an `ok` key.

### Ordering rules

* Responses can arrive in any order relative to each other and to events. Match responses by `id` only.
* Events on one connection keep the order the server emitted them in.
* Run events carry `data.seq`, which increases strictly per run starting at 1. Use it to detect gaps and replay with `runs.events`.

## 3. Lifecycle, connection loss and reconnection

1. **Startup.** The server takes an exclusive lock (`brain.sock.lock`). It removes a stale socket file only after a connect probe fails, and runs crash recovery (§9) *before* it accepts connections. A second `cedar-brain serve` exits with code 3.
2. **Client startup.** Connect, call `system.health`, then `events.subscribe`, then load state (`projects.list`, `runs.list`, …).
3. **Shutdown** (SIGTERM/SIGINT). The server emits `system.shutdown` to `system` subscribers, cancels active runs (they end `cancelled`) and planning tasks, then closes connections and removes the socket.
4. **Connection loss.** The socket closes (EOF). The server keeps working: runs continue without the client. Reconnect with backoff (suggested: 250 ms doubling to 5 s) and resubscribe, because subscriptions are per connection. Then resync:
   * compare `system.health.instance_id` with the value you saw before. If it changed, the server restarted, so reload everything.
   * for each run you were showing, call `runs.events` with `after_seq = last seq you saw` (or just `runs.get`).
5. **`stream.gap` event.** If a client reads too slowly, its outgoing queue (1000 messages) fills and events are dropped. The server then sends `{"event": "stream.gap", "data": {"dropped_events": N}}`. Resync as in step 4.

## 4. Error codes

Protocol-level `error.code` values:

| Code | Meaning |
|---|---|
| `PARSE_ERROR` | line was not valid UTF-8 JSON |
| `INVALID_REQUEST` | envelope malformed (missing/invalid id, method, params, or extra keys) |
| `UNSUPPORTED_VERSION` | `v` is not 1 |
| `MESSAGE_TOO_LARGE` | line over the limit; the connection will be closed |
| `UNKNOWN_METHOD` | no such method |
| `INVALID_PARAMS` | parameter missing, of the wrong type, out of range, or unknown |
| `NOT_FOUND` | referenced project/node/skill/plan/workflow/run does not exist |
| `CONFLICT` | duplicate (project already registered; workflow already has an active run, see `details.run_id`) |
| `NO_ACTIVE_PROJECT` | `project_id` omitted and no project is selected |
| `INVALID_REPOSITORY` | path is not a directory inside a git work tree |
| `PATH_REJECTED` | path is outside the approved locations or points at a protected location |
| `GRAPH_UNAVAILABLE` | the project has no graph file |
| `GRAPH_INVALID` | graph file unreadable, not JSON, not Graphify node-link, or too large |
| `AGENT_UNAVAILABLE` | the required agent CLI is missing, disabled, or lacks required flags |
| `PLAN_INVALID` | plan failed validation (appears inside plan objects) |
| `WORKFLOW_INVALID` | workflow definition failed validation; `details.errors` lists the problems |
| `APPROVAL_REQUIRED` | `runs.start` was called without `approve: true` |
| `ILLEGAL_STATE` | action is not valid in the current state (cancelling a finished run, approving a step that is not waiting, making a workflow from a plan that is not ready) |
| `BUSY` | connection limit reached |
| `NOT_IMPLEMENTED` | feature intentionally unavailable in this release |
| `INTERNAL` | unexpected server error (details are in the server log, never in the response) |

Codes that appear **inside** step, job and plan `error` objects (not as response errors):

| Code | Meaning |
|---|---|
| `AGENT_AUTH_FAILED` | CLI is not logged in or credentials were rejected. The user must run `claude` / `codex login` themselves |
| `AGENT_RATE_LIMITED` | 429 / overloaded |
| `AGENT_USAGE_LIMIT` | plan or quota exhausted |
| `AGENT_NETWORK` | DNS / connection failure |
| `AGENT_TIMEOUT` | step or planner timeout reached; the process group was terminated |
| `AGENT_CANCELLED` | cancelled by the user |
| `AGENT_OUTPUT_LIMIT` | agent exceeded output limits (32 MiB stdout / 4 MiB stderr); terminated |
| `AGENT_KILLED` | process died from a signal |
| `AGENT_FAILED` | non-zero exit or reported error with no more specific cause |
| `AGENT_UNAVAILABLE` | as above |
| `DUPLICATE_JOB` | the owner already has an active agent job (prevents double execution) |
| `VALIDATION_FAILED` | executor finished but the step's validation method failed |
| `STEP_FAILED` | run-level: one or more steps failed |
| `WORKTREE_FAILED` | `git worktree add` failed for a write step |
| `INVALID_REPOSITORY` | the repository was invalid when the step ran |
| `INTERRUPTED` | the backend stopped while this was running; it is never re-run automatically |
| `CANCELLED` | run/plan cancelled |
| `INTERNAL` | engine error |

## 5. States

### Step states

`pending`, `ready`, `running`, `awaiting_approval`, `succeeded`, `failed`, `cancelled`, `skipped`

Legal transitions. The server enforces these; any other transition is a bug.

| From | To |
|---|---|
| `pending` | `ready` (all dependencies succeeded), `skipped` (a dependency did not succeed, or the run stopped), `cancelled` |
| `ready` | `running`, `awaiting_approval` (step has `approval_required`), `skipped`, `cancelled` |
| `awaiting_approval` | `running` (approved), `skipped` (rejected), `cancelled` |
| `running` | `succeeded` (executor OK **and** validation passed), `failed`, `cancelled` |
| `succeeded`, `failed`, `cancelled`, `skipped` | terminal |

`run.step.updated` events carry `previous` and `state`, and sometimes `reason` (for example `"rejected by user"`, `"dependency did not succeed"`, `"run cancelled"`, `"interrupted"`) and `error`.

### Run states

| Status | Meaning |
|---|---|
| `queued` | created, not yet started (brief) |
| `running` | at least one step running, or steps still to schedule |
| `awaiting_approval` | nothing running and at least one step waiting for `runs.approve` |
| `succeeded` | every step succeeded |
| `failed` | at least one step failed (`error.code = STEP_FAILED`) |
| `cancelled` | cancelled by `runs.cancel` or server shutdown |
| `incomplete` | no failures, but some steps were skipped (for example, a rejected approval) |
| `interrupted` | the backend stopped mid-run. This is set during startup recovery, and the run is **not** resumed |

Active statuses: `queued`, `running`, `awaiting_approval`. A workflow can have only one active run.

### Plan states

`generating` → `ready` (valid) | `invalid` (output unparseable or failed validation) | `failed` (agent error, cancelled, or interrupted).

### Agent states (`agents.list[].status`)

| Status | Meaning |
|---|---|
| `available` | executable found, `--version` works, every required flag is present |
| `missing` | executable not found |
| `disabled` | disabled in config |
| `unsupported` | found, but `missing_flags` lists required flags the installed version lacks |
| `error` | `--version` failed or timed out |

`authenticated` is `true`/`false`/`null` (unknown), from `claude auth status` / `codex login status`. These checks make no model call and store no credentials.

### Agent job states (`agents.status.recent_jobs[].status`)

`starting`, `running`, `succeeded`, `failed`, `cancelled`, `timed_out`, `interrupted`

## 6. Events

Subscribe with `events.subscribe {"topics": [...]}`. A topic is the first segment of the event name.

| Event | Topic | `data` |
|---|---|---|
| `system.shutdown` | system | `{instance_id}` |
| `project.updated` | project | `{project_id, change: "added"\|"selected"\|"removed"\|"graph_path"}` |
| `cortex.reloaded` | cortex | `{project_id, nodes, edges}` |
| `plan.updated` | plan | `{plan_id, status, error?}`. Fetch the full plan with `plans.get` |
| `run.updated` | run | `{run_id, seq, status, workflow_id? (on queued), error?}` |
| `run.step.updated` | run | `{run_id, seq, step_id, state, previous, reason?, error?}` |
| `run.output` | run | `{run_id, seq, step_id, kind: "status"\|"message"\|"tool"\|"error", text}`. `text` is redacted and capped at 2000 chars, with at most 400 output events per agent job. **Agent text is untrusted: render it as plain text, never as markup** |
| `stream.gap` | (always sent) | `{dropped_events}` |

The `agent` topic is reserved. No events are sent on it in v1.

`contracts/examples/mock-event-stream.jsonl` holds a recorded run (`after_ms` = delay since the previous event).

## 7. Methods

Every parameter is optional unless marked **required**. `project_id` defaults to the active project. IDs are prefixed: `prj_`, `skl_`, `pln_`, `wf_`, `run_`, `job_`.
Examples: `contracts/examples/<method>.json`.

### system.health
Params: none. Result: `{status: "ok"|"degraded", version, protocol, instance_id, pid, uptime_s, database: {ok, schema_version, expected_schema_version}, active_runs, active_agent_jobs, active_project_id}`. `instance_id` changes on every server start.

### system.capabilities
Params: none. Result: `{protocol, version, features, methods, executors, limits, event_topics}`.
`features` maps a feature name to `{status: "supported"|"unavailable"|"not_implemented", reason?}`:
* `unavailable`: implemented, but its prerequisites are missing right now (for example, the CLI is not installed).
* `not_implemented`: not in this release, or excluded by design.

The UI must hide or disable anything that is not `supported`. Current feature keys: `ipc.unix_socket`, `ipc.event_subscriptions`, `projects.registry`, `cortex.queries`, `cortex.graph_update`, `skills.local_discovery`, `skills.local_import`, `skills.remote_install`, `skills.execution`, `agents.claude`, `agents.codex`, `agents.claude.write`, `agents.codex.write`, `agents.session_resume`, `agents.ollama`, `pathfinder.plans`, `hyphae.workflows`, `hyphae.parallel_steps`, `hyphae.step_approval`, `hyphae.auto_merge`, `remote_access`.
`executors`: `[{id, description, modes, inputs: {name: {type, required}}}]`.

### events.subscribe / events.unsubscribe
Params: **`topics`** (array of `system|project|cortex|agent|plan|run`). Result: `{topics}` (the connection's current set).

### projects.list
Result: `{projects: [Project], active_project_id}`.
`Project = {id, name, root, graph_path, graph_path_explicit, created_at, updated_at}`. When no graph path is set explicitly, `graph_path` is `<root>/graphify-out/graph.json`.

### projects.add
Params: **`path`** (any directory inside a git work tree; normalised to the top level), `name` (≤120), `graph_path` (`.json` file), `select` (bool, default false; the first project is always selected).
Result: `Project`. Errors: `INVALID_REPOSITORY`, `CONFLICT` (`details.project_id`), `PATH_REJECTED`.
The server never runs `git init`, and never touches remotes or branches.

### projects.select
Params: **`project_id`**. Result: `Project`.

### projects.status
Params: `project_id`. Result:
`{project, active, git: {valid, branch, detached, head, clean, changed_files, error}, graph: {path, state: "present"|"missing"|"too_large"}, capabilities: {git_status, cortex, worktrees}}`.
A deleted or moved repository shows `git.valid=false` with an `error`.

### projects.set_graph
Params: **`project_id`**, `graph_path` (`null` resets to the default). Result: `Project`.

### projects.remove
Params: **`project_id`**. Unregisters the project only; no files are touched. Result: `{removed}`.

### cortex.status
Params: `project_id`. Loads the graph on first use. Result:
* not loaded: `{project_id, graph_path, loaded: false, state: "unavailable"|"invalid"|"not_loaded", error?}`
* loaded: `{…, loaded: true, state: "loaded", nodes, edges, communities, relations: {relation: count}, directed, schema_version, graphify_version, malformed: {nodes, links, dangling_links}, freshness: {state: "fresh"|"stale"|"unknown", built_at_commit, head, commits_behind, graph_mtime, age_s, changed_on_disk}}`

`changed_on_disk: true` means the file changed after loading. The server keeps serving the loaded index until `cortex.reload`.

### cortex.reload
Params: `project_id`. Reloads from disk and emits `cortex.reloaded`. Result: like `cortex.status`.

### cortex.search
Params: **`query`** (≤200), `field` (`any`|`id`|`name`|`path`|`type`, default `any`), `file_type`, `limit` (1–200, default 25).
Result: `{results: [Node & {score}]}`, best match first.
`Node = {id, label, file_type, source_file, source_location, community, community_name}`.

### cortex.dependencies
Params: **`node_id`**, `direction` (`out` = what the node depends on, `in` = what depends on it, `both`; default `out`), `depth` (1–4, default 1), `relations` (filter, e.g. `["imports"]`), `min_confidence` (`AMBIGUOUS`|`INFERRED`|`EXTRACTED`), `limit` (1–500, default 100).
Result: `{root, direction, depth, nodes: [Node & {distance, via: {from, relation, evidence}}], edges: [Edge], truncated, relationship_kind: "static", note}`.
`Edge = {source, target, relation, confidence, confidence_score, evidence: "static_extracted"|"inferred"|"ambiguous"|"unknown", source_file}`.

### cortex.impact
Params: `node_ids` (≤50) and/or `path` (project-relative file; resolves to every node from that file), `depth` (1–4, default 2), `min_confidence`, `limit`.
Walks dependency relations backwards (who imports, calls or references the target) and `contains` upwards.
Result: `{targets, depth, impacted: [Node & {distance, via: {to, relation, evidence}, path_evidence}], files, truncated, relationship_kind: "static", note}`.
`path_evidence` is the **weakest** confidence along the path. Impact is a candidate list for review, **not proof of breakage or of runtime dependency**, and the UI must label it that way.

### cortex.node
Params: **`node_id`**. Result: `{node, outgoing: [Edge], incoming: [Edge], outgoing_total, incoming_total, relationship_kind, source: {reference, path, within_project, exists, line} | null}`.
`source.path` is `null` when the reference escapes the project root.

### cortex.communities
Params: `community_id`, `limit`. Without an id: `{communities: [{id, name, size, sample}], total, truncated}`. With an id: `{community: {id, name, size}, members: [Node], truncated}`.

### skills.list
Params: `query`, `refresh` (rescan the skill directories). Result: `{skills: [Skill], problems: [{path, problem}], scanned_at, skill_dirs}`.
`Skill = {id, name, description, source, path, version, requirements, compatibility: "compatible"|"incomplete"|"invalid", trust: "untrusted"|"reviewed", imported, has_scripts, content_sha256, provenance: {kind: "local"|"local_import", root?, original_path?}}`.
v1 has no method that marks a skill `reviewed`, so every skill is `untrusted`. Trust also resets to `untrusted` whenever `SKILL.md` content changes.

### skills.inspect
Params: **`skill_id`**, `include_instructions` (default true). Result: `Skill & {files: [{path, size, script?, symlink?, symlink_safe?}], problems, instructions, instructions_truncated, changed_since_scan, notice}`.
`instructions` is the SKILL.md body (≤64 KiB). **It is untrusted text: display it, never execute it.**

### skills.import_local
Params: **`path`** (a directory containing `SKILL.md`, inside a configured skill dir or import root (default `$HOME`), and not in a protected location). Records the skill and returns `Skill`. Nothing is executed or installed. Errors: `PATH_REJECTED`, `INVALID_PARAMS`.

### agents.list
Params: `refresh` (bypass the 120 s detection cache). Result: `{agents: [Agent]}`.
`Agent = {id: "claude"|"codex", name, status, executable, version, authenticated, auth_method, modes: ["read_only", "write"?], missing_flags, detail, checked_at, active_jobs}`.

### agents.status
Params: **`agent_id`**, `refresh`. Result: `Agent & {recent_jobs: [{id, owner_kind, owner_id, status, mode, session_id, exit_code, started_at, finished_at}], max_concurrent_processes}`.

### plans.create
Params: **`objective`** (≤4000), `project_id`, `agent_id` (`claude`|`codex`, default `claude`), `max_steps` (1–12, default 8).
Returns immediately with `Plan` in status `generating`. Watch `plan.updated`, then call `plans.get`. The planner agent runs **read-only** in the project root. Planning never executes the plan.

### plans.get
Params: **`plan_id`**. Result: `Plan = {id, project_id, objective, agent_id, status, plan, validation, error, created_at, updated_at}`.
* `ready`: `plan` is the normalised definition and `validation = {valid: true, errors: [], unsupported_steps, executable}`.
* `invalid`: `plan` is the raw proposal (or `{raw_output}`), and `validation.errors` lists the problems.

### plans.validate
Params: **`plan`** (object), `project_id`. Result: `{valid, errors: [{step, field, message}], unsupported_steps: [{step, executor, reason}], executable, plan}`. Use this after the user edits a plan.
`valid` means the plan is well-formed. `executable` means it can also run right now. A plan can be valid but not executable when an agent CLI is unavailable or the project has no Graphify graph for `cortex.*` steps; `unsupported_steps` says which steps are affected.

### workflows.create
Params: exactly one of `definition` (WorkflowDefinition) or `plan_id` (the plan must be `ready`), plus `project_id`.
Result: `Workflow & {unsupported_steps}`. Errors: `WORKFLOW_INVALID` (`details.errors`), `ILLEGAL_STATE`.

**WorkflowDefinition**: `{name|title, summary?, steps: [Step]}`, 1–20 steps (plans: ≤12).
`Step` (unknown keys are rejected):

| Key | Required | Rule |
|---|---|---|
| `id` | yes | `^[a-z0-9][a-z0-9_-]{0,47}$`, unique |
| `description` | yes | 1–1000 chars |
| `executor` | yes | one of `system.capabilities.executors[].id` |
| `inputs` | yes | object; exactly the executor's inputs (see below) |
| `depends_on` | yes | list of step ids; no cycles |
| `expected_output` | yes | ≤1000 chars |
| `approval_required` | yes | bool; must be `true` when `mode` is `write` |
| `validation` | yes | `exit_status` \| `non_empty_result` \| `json_result` |
| `mode` | no | `read_only` (default) \| `write` (only `agent.codex`) |
| `on_failure` | no | `stop` (default: start no new steps, skip the rest) \| `continue` (only dependents are skipped) |
| `timeout_s` | no | 10–7200 (default 900) |

Executors and inputs:

| Executor | Inputs | Notes |
|---|---|---|
| `agent.claude` | **`prompt`** (≤16000) | read-only tools (Read, Grep, Glob), project root |
| `agent.codex` | **`prompt`** | `read_only`: Codex read-only sandbox. `write`: Codex workspace-write sandbox inside a new git worktree `cedar-brain/<run>/<step>`, never merged or pushed |
| `cortex.search` | **`query`**, `limit` | deterministic |
| `cortex.impact` | `node_ids` and/or `path` (inside project), `depth` | deterministic |
| `git.status` | none | read-only |

Agent steps automatically receive the outputs of the steps they depend on, wrapped as untrusted data.

### workflows.get / workflows.list
`workflows.get` params: **`workflow_id`**. Result: `Workflow = {id, project_id, name, definition, source_plan_id, created_at}`.
`workflows.list` params: `project_id`, `limit`. Result: `{workflows: [{id, project_id, name, source_plan_id, created_at, step_count}]}`.

### runs.start
Params: **`workflow_id`**, **`approve`** (must be `true`; this is the user's explicit go-ahead).
The workflow is re-validated against the current state of the project and agents. Result: `Run` (status `queued` or `running`).
Errors: `APPROVAL_REQUIRED`, `WORKFLOW_INVALID`, `AGENT_UNAVAILABLE` or `GRAPH_UNAVAILABLE` (`details.unsupported_steps`: a step's agent is unavailable, or a `cortex.*` step targets a project with no graph), `CONFLICT` (`details.run_id` of the active run).

### runs.get
Params: **`run_id`**. Result:
`Run = {id, workflow_id, project_id, status, error, created_at, started_at, finished_at, last_seq, steps: [RunStep]}`
`RunStep = {step_id, state, executor, started_at, finished_at, result, error, worktree, verification: [{method, passed, detail, ts}]}`
For agent steps, `result` is `{output, job: {job_id, status, exit_code, session_id, usage, stdout_log, stderr_log, malformed_lines, duration_s}, worktree?: {path, branch, base_commit, note}}`. `output` is redacted and capped at 256 KiB; deterministic steps add `data`.

### runs.list
Params: `workflow_id`, `project_id`, `status`, `limit`. Result: `{runs: [{id, workflow_id, project_id, status, created_at, started_at, finished_at}]}`, newest first.

### runs.cancel
Params: **`run_id`**. Cancels every running step, which terminates the agent's **own process group**: SIGTERM, then SIGKILL after 5 s. Waits up to 30 s and returns the `Run`. Errors: `ILLEGAL_STATE` if the run is not active.

### runs.approve
Params: **`run_id`**, **`step_id`**, **`approve`** (bool). `true` starts the step; `false` skips it (and its dependents). Result: `{run_id, step_id, approved}`. Errors: `ILLEGAL_STATE` if the step is not `awaiting_approval`.

### runs.events
Params: **`run_id`**, `after_seq` (default 0), `limit` (1–1000, default 200). Result: `{run_id, events: [{seq, ts, event, data}], last_seq, more}`. This is the durable history: it survives restarts.

## 8. Fixture data and mock server

* `contracts/examples/*.json`: one file per method, holding `[{note, request, response}]`. `_protocol_errors.json` covers raw-line errors.
* `contracts/examples/mock-event-stream.jsonl`: a recorded run.
* `cedar-brain mock-serve [--socket PATH] [--speed N]` serves `backend/cedar_brain/fixtures/responses.json` with the same envelope validation and socket rules. After `runs.start` or `plans.create` it replays the event stream. It always returns the same object per method, so use it for layout and flow work, not for logic.

## 9. Crash recovery semantics

On startup, before any client can connect:

* Agent jobs left `starting`/`running` become `interrupted`. If a process with the recorded pid **and** the same `/proc` start time still exists, the server kills that process group. A reused pid is never touched.
* Runs left active become `interrupted`. Their `running` steps become `failed` with `INTERRUPTED`; other unfinished steps become `cancelled` (`reason: "interrupted"`). **Nothing is re-run.** A write step that was interrupted may have left a partial worktree for the user to inspect.
* Plans left `generating` become `failed` (`INTERRUPTED`).

## 10. Security model (what the UI may assume)

* The backend is the authority. The UI never decides whether something is allowed.
* Only processes running as the same Unix user can connect. **Same-user processes are not isolated from each other**: any of them could drive the backend, just as they could run `claude` or `codex` directly.
* Agent output, skill instructions and graph labels are untrusted. Render them as plain text.
* Secrets matching common token formats are redacted from output, logs and stored results. Redaction is best effort.

## 11. Limits

| Limit | Value |
|---|---|
| message line | 1 MiB |
| plan steps / workflow steps | 12 / 20 |
| traversal depth / results | 4 / 500 |
| parallel steps per run | `max_parallel_steps` (config, default 2) |
| concurrent agent processes | `max_agent_processes` (config, default 2); further jobs queue |
| step timeout | 900 s default, 10–7200 s per step |
| planner timeout | 300 s (config) |

## 12. Change log

* v1.0 (2026-10-18): initial contract.
