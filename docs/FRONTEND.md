# Mycelium frontend

The standalone Quickshell application lives in `cockpit/`. It uses a native
`FloatingWindow`, requires no CEDAR Shell process, and consumes protocol v1.
No Python backend or contract files were changed for this frontend.

## Launch

Requires Quickshell 0.3.1 (the version used for verification), Qt Quick Controls,
Qt Quick Shapes and QtCore. QtTest is needed only for the frontend test harness.

From this checkout:

```sh
cedar-brain serve              # only if the backend service is not already running
scripts/launch-cockpit.sh      # separate terminal, or launch while the service runs
```

The launcher resolves the checkout relative to itself and defaults controls to
Qt's Basic style. Alternatively: `qs -p /path/to/cedar-brain/cockpit`.
The socket follows the complete environment/fallback rules in the contract.
`CEDAR_BRAIN_SOCKET=/absolute/path/to/brain.sock` overrides it.

For the backend's recorded fixture server, in separate terminals:

```sh
cedar-brain --socket /tmp/mycelium-mock.sock mock-serve --speed 1
CEDAR_BRAIN_SOCKET=/tmp/mycelium-mock.sock CEDAR_BRAIN_FIXTURES=1 scripts/launch-cockpit.sh
```

Always set `CEDAR_BRAIN_FIXTURES=1` for mock sessions: the application displays
an unmistakable fixture banner. The mock returns fixed objects, including IDs;
it is a rendering/transport aid rather than a stateful execution simulator.
There is no automatic fallback to fixture data when the real backend disconnects.

## Implemented interface

- Native resizable window, 720×560 minimum, compact navigation below 1000 logical
  pixels, six persistent-once-visited pages, keyboard shortcuts Ctrl+1…6,
  keyboard focus indicators and Escape to close the inspector.
- Project registry selection and addition; per-project Git/graph diagnostics;
  optional locally saved default project.
- Pathfinder: multiline objective, preferred planning CLI, bounded recent
  requests, structured plan review, advanced JSON editing/reordering, server
  validation, and explicit approval followed by workflow creation/run start.
  Generated plans never execute automatically. The request surface contracts
  to a summary when a valid plan arrives.
- Cortex: search and communities, static dependency/impact queries, evidence
  labels, source details, graph freshness, graph path registration/reload,
  mouse/keyboard pan and zoom, reset/center, relation filters, and keyboard
  accessible node list. The graph area retains a minimum height on short
  windows; the page scrolls to its secondary controls.
- Spore Library: local filtering, compatibility/import filters, rescanning,
  declared requirements, trust/provenance/instruction inspection, and local
  import through the backend. Untrusted instructions remain plain text.
- Hyphae: run history, connected vertical steps, actual backend statuses,
  elapsed/finished durations, per-step approval/rejection, results/errors,
  validation/worktree/log details, cancellation request/confirmation distinction.
- Agent Grove: actual detection/authentication state, versions, capabilities,
  active job counts, recent session/job inspection and preferred planner.
  Job output and cancellation are exposed through the owning run in Hyphae;
  v1 has no independent job-output/cancel API.
- Settings: local theme sync, reduced motion, planning/default-project
  preferences, graph/project registration, read-only backend execution limits,
  discovery locations, and inspectable health/storage/graph diagnostics.

Unsupported protocol capabilities have no fake action buttons: remote skill
installation, skill execution/attachment to a plan, global coding-agent settings,
backend configuration writes, pause/resume/retry, direct standalone job control,
and Ollama are not implemented by v1. Per-step executors/timeouts are editable
and validated. Recent requests persist within this application session.

## Architecture and CEDAR reuse

`core/BrainConnection.qml` owns one `Quickshell.Io.Socket` with `SplitParser`,
version checks, request correlation, pending-request/time limits, subscriptions,
250 ms–5 s reconnect backoff and error presentation. It never replays mutations.
`core/BrainStore.qml` owns backend snapshots, scoped refreshes and event handling;
`NavigationState.qml` owns page/inspector state. Pages do not parse CLI output or
spawn subprocesses. `Models.js` contains formatting and stable graph geometry.

Reused and adapted from the inspected CEDAR Shell/distribution sources:

- `components/ChamferFrame.qml`: CEDAR's asymmetrical short/long cut geometry
  and top/foot edge lines, with shell configuration dependencies removed.
- `components/CedarMark.qml`: the existing native tree mark.
- `core/Theme.qml`: CEDAR palette, Strata accent derivation, logical-pixel sizes,
  180 ms transitions and 320/200 ms surface motion.
- StrataButton/StrataField interaction patterns, static grain, meaning-triggered
  heading resolution, and integrated inspector growth with stable control
  bounds. Complex live shell services/components were not copied.

Theme synchronization reads `$XDG_CONFIG_HOME/cedar/theme.json` (default
`~/.config/cedar/theme.json`); disabling it uses the packaged palette.
Frontend preferences use `$XDG_CONFIG_HOME/cedar-brain/frontend.ini`.

There was no existing Brain cockpit or graph renderer in this checkout or the
inspected shell directories. The backend handoff corroborates that absence.
Cortex therefore uses real query results instead of claiming to preserve an
unavailable prototype or trimmed export.

## Performance boundaries

Production graph queries request at most 200 nodes (dependency views include the
root). A Canvas uses cached geometry, viewport culling and low-zoom label
suppression; it creates no QML item per graph node. There is no force simulation
or continuous graph animation. Hidden graph painting is disabled. Search and
community results contain no invented edges; edges appear only after querying
actual dependencies.

Run logs are batched every 120 ms while events arrive. The pending output queue
is capped at 1000 messages and retained history at 400. The inspector shows at
most 120 messages/64 KiB of log text and 64 KiB of result text, with backend log
paths where supplied. Durable history replays page by page with sequence
tracking; duplicates are removed. Run state is reconciled on events, reconnect,
or reported stream gaps. There is no continuous workflow-list polling.

A synthetic 5000-node software-rendering test verified viewport culling and
unchanged cached geometry during pan. This is not a 60 FPS benchmark. Native
Wayland compositor/GPU frame timing and physical multi-monitor behavior still
need verification on an unrestricted desktop.

## Tests and visual evidence

```sh
python3 tests/frontend/check_frontend.py
QT_SCALE_FACTOR=2 MYCELIUM_EVIDENCE_OUT="$PWD/build/frontend-evidence-hidpi" \
  python3 tests/frontend/check_frontend.py
python3 tests/frontend/check_socket.py
```

The first test launches the installed Quickshell renderer using the published
backend fixtures injected at the transport boundary. It checks mouse/keyboard
navigation, all pages, explicit approval, plan completion reconciliation,
request correlation, project-scoped resets, log bursts, reduced motion,
720×560/1920×800 native window resizing, density/culling, hidden rendering,
invalid skill metadata, disconnection presentation and incompatible protocols.
The cancellation scenario normalizes a recorded fixture's fixed run ID to test
request versus confirmation state; this is not live cancellation evidence.

`build/frontend-evidence/` contains rendered PNGs and the runtime transcript.
The screenshots are marked as fixtures, not live project evidence. The runner
uses an isolated configuration directory and leaves desktop preferences alone.

`check_socket.py` uses the real QML Socket against the published mock server,
including a server restart and subscription restoration. It exits 77 when Unix
sockets are forbidden. In the implementation environment, both socket binding
and connecting to `/run/user/1000/cedar-brain/brain.sock` returned EPERM. Therefore
actual socket transport, live planning/execution/cancellation and real Graphify
integration have **not** been verified here. No claim of acceptance against the
live backend is made.

Static verification:

```sh
/usr/lib/qt6/bin/qmllint --signal-handler-parameters disable -I /usr/lib/qt6/qml \
  cockpit/shell.qml cockpit/AppWindow.qml cockpit/core/*.qml \
  cockpit/pages/*.qml cockpit/components/*.qml cockpit/graph/*.qml \
  cockpit/workflows/*.qml cockpit/agents/*.qml
```

Only `signal-handler-parameters` is disabled: installed Quickshell qmltypes omit
`QLocalSocket::LocalSocketError` metadata even though its Socket error signal
works at runtime. The offscreen platform also reports unsupported window masks;
the sandbox reports denied IPC sockets. These are distinguished from QML
import/binding/runtime warnings, which the fixture runner treats as failures.

Remaining acceptance work: run the socket/reconnect test outside the sandbox,
then exercise a real project/Graphify graph and a real agent plan, approval and
cancellation; verify physical monitor movement/maximization and profile GPU
frame pacing on the target desktop. Backend protocol extensions are needed for
the unsupported capabilities listed above.
