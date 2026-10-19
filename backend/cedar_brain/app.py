"""Application composition and the protocol method table."""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from . import __version__
from .agents.registry import AgentGrove
from .agents.runner import AgentRunner
from .config import Config, ensure_private_dir
from .cortex.service import Cortex
from .events import TOPICS, EventBus
from .hyphae.executor import Hyphae
from .hyphae.journal import Journal
from .hyphae.models import RUN_STATUSES, executor_catalog
from .pathfinder.planner import Pathfinder
from .pathfinder.validation import MAX_PLAN_STEPS
from .projects.manager import ProjectManager
from .protocol import (MAX_MESSAGE_BYTES, PROTOCOL_VERSION, BrainError, ErrorCode, P, Request,
                       validate_params)
from .spores.registry import SporeLibrary
from .storage.database import Database
from .storage.migrations import SCHEMA_VERSION

log = logging.getLogger(__name__)

Handler = Callable[[dict, Any], Awaitable[Any]]


@dataclass
class Method:
    spec: dict[str, P]
    handler: Handler
    doc: str


def _id_param(prefix: str, required: bool = True) -> P:
    return P(str, required=required, max_len=64,
             check=lambda v: None if v.startswith(prefix + "_") else f"must be a {prefix}_… id")


class App:
    def __init__(self, config: Config):
        self.config = config
        self.started = time.time()
        self.instance_id = uuid.uuid4().hex[:12]
        ensure_private_dir(config.state_dir)
        ensure_private_dir(config.logs_dir)
        self.db = Database(config.db_path)
        self.db.migrate()
        self.bus = EventBus()
        self.projects = ProjectManager(self.db, self.bus)
        self.cortex = Cortex(self.bus, config.graphify_executable)
        self.spores = SporeLibrary(self.db, config.skill_dirs, config.import_roots)
        self.runner = AgentRunner(self.db, config.logs_dir, config.max_agent_processes)
        self.grove = AgentGrove(config, self.runner)
        self.journal = Journal(self.db, self.bus)
        self.hyphae = Hyphae(self.db, self.journal, self.projects, self.grove, self.cortex, config)
        self.pathfinder = Pathfinder(self.db, self.bus, self.projects, self.cortex, self.spores, self.grove, config)
        self.methods: dict[str, Method] = {}
        self._register()

    def recover(self) -> dict:
        """Startup recovery: never re-runs anything, only records what was interrupted."""
        return {
            "agent_jobs": self.runner.recover(),
            "runs": self.journal.recover(),
            "plans": self.pathfinder.recover(),
        }

    async def shutdown(self) -> None:
        await self.pathfinder.shutdown()
        await self.hyphae.shutdown()
        self.db.close()

    async def call(self, method: str, params: dict | None = None, conn=None) -> Any:
        m = self.methods.get(method)
        if m is None:
            raise BrainError(ErrorCode.UNKNOWN_METHOD, f"unknown method {method!r}")
        args = validate_params(m.spec, params or {})
        return await m.handler(args, conn)

    async def dispatch(self, req: Request, conn=None) -> Any:
        return await self.call(req.method, req.params, conn)

    # ------------------------------------------------------------------------
    def _register(self) -> None:
        def method(method_name: str, doc: str, /, **spec: P):
            def wrap(fn: Handler) -> Handler:
                self.methods[method_name] = Method(spec, fn, doc)
                return fn
            return wrap

        proj_opt = _id_param("prj", required=False)

        # -- system --
        @method("system.health", "Liveness and basic state")
        async def _(a, c):
            return await self.health()

        @method("system.capabilities", "What this backend supports right now")
        async def _(a, c):
            return await self.capabilities()

        @method("events.subscribe", "Subscribe this connection to event topics",
                topics=P(list, required=True, item_type=str, max_items=len(TOPICS),
                         check=lambda v: None if set(v) <= set(TOPICS) else f"topics must be from {list(TOPICS)}"))
        async def _(a, c):
            if c is None:
                raise BrainError(ErrorCode.INVALID_REQUEST, "subscriptions need a socket connection")
            c.topics |= set(a["topics"])
            return {"topics": sorted(c.topics)}

        @method("events.unsubscribe", "Remove event topics from this connection",
                topics=P(list, required=True, item_type=str, max_items=len(TOPICS)))
        async def _(a, c):
            if c is None:
                raise BrainError(ErrorCode.INVALID_REQUEST, "subscriptions need a socket connection")
            c.topics -= set(a["topics"])
            return {"topics": sorted(c.topics)}

        # -- projects --
        @method("projects.list", "Registered projects")
        async def _(a, c):
            return {"projects": self.projects.list(), "active_project_id": self.projects.active_id}

        @method("projects.add", "Register an existing local git repository",
                path=P(str, required=True, max_len=4096), name=P(str, max_len=120),
                graph_path=P(str, max_len=4096), select=P(bool, default=False))
        async def _(a, c):
            return await self.projects.add(a["path"], a["name"], a["graph_path"], a["select"])

        @method("projects.select", "Make a project active", project_id=_id_param("prj"))
        async def _(a, c):
            return self.projects.select(a["project_id"])

        @method("projects.status", "Git and graph status of a project (default: active)", project_id=proj_opt)
        async def _(a, c):
            return await self.projects.status(a["project_id"])

        @method("projects.set_graph", "Associate a Graphify graph.json with a project (null = default location)",
                project_id=_id_param("prj"), graph_path=P(str, max_len=4096))
        async def _(a, c):
            self.cortex.forget(a["project_id"])
            return self.projects.set_graph_path(a["project_id"], a["graph_path"])

        @method("projects.remove", "Unregister a project (files are untouched)", project_id=_id_param("prj"))
        async def _(a, c):
            self.projects.remove(a["project_id"])
            self.cortex.forget(a["project_id"])
            return {"removed": a["project_id"]}

        # -- cortex --
        @method("cortex.status", "Graph load state, counts and freshness", project_id=proj_opt)
        async def _(a, c):
            return await self.cortex.status(self.projects.resolve(a["project_id"]))

        @method("cortex.reload", "Explicitly reload the graph from disk", project_id=proj_opt)
        async def _(a, c):
            project = self.projects.resolve(a["project_id"])
            await self.cortex.reload(project)
            return await self.cortex.status(project, load=False)

        @method("cortex.search", "Search nodes by id, name, path or type",
                project_id=proj_opt, query=P(str, required=True, max_len=200),
                field=P(str, default="any", choices=("any", "id", "name", "path", "type")),
                file_type=P(str, max_len=64), limit=P(int, default=25, min=1, max=200))
        async def _(a, c):
            project = self.projects.resolve(a.pop("project_id"))
            return await self.cortex.search(project, **a)

        @method("cortex.dependencies", "Bounded traversal from a node",
                project_id=proj_opt, node_id=P(str, required=True, max_len=1000),
                direction=P(str, default="out", choices=("out", "in", "both")),
                depth=P(int, default=1, min=1, max=4), relations=P(list, item_type=str, max_items=20),
                min_confidence=P(str, default="AMBIGUOUS", choices=("AMBIGUOUS", "INFERRED", "EXTRACTED")),
                limit=P(int, default=100, min=1, max=500))
        async def _(a, c):
            project = self.projects.resolve(a.pop("project_id"))
            return await self.cortex.dependencies(project, **a)

        @method("cortex.impact", "Components that may be affected by changing nodes or a file",
                project_id=proj_opt, node_ids=P(list, item_type=str, max_items=50),
                path=P(str, max_len=4096), depth=P(int, default=2, min=1, max=4),
                min_confidence=P(str, default="AMBIGUOUS", choices=("AMBIGUOUS", "INFERRED", "EXTRACTED")),
                limit=P(int, default=100, min=1, max=500))
        async def _(a, c):
            project = self.projects.resolve(a.pop("project_id"))
            return await self.cortex.impact(project, a.pop("node_ids"), a.pop("path"), **a)

        @method("cortex.node", "One node with its direct relationships and resolved source file",
                project_id=proj_opt, node_id=P(str, required=True, max_len=1000))
        async def _(a, c):
            return await self.cortex.node(self.projects.resolve(a["project_id"]), a["node_id"])

        @method("cortex.communities", "Community list, or members of one community",
                project_id=proj_opt, community_id=P(int, min=0), limit=P(int, default=50, min=1, max=500))
        async def _(a, c):
            project = self.projects.resolve(a.pop("project_id"))
            return await self.cortex.communities(project, **a)

        # -- skills --
        @method("skills.list", "Discovered and imported skills", query=P(str, max_len=200), refresh=P(bool, default=False))
        async def _(a, c):
            if a["refresh"]:
                self.spores.refresh()
            return self.spores.list(a["query"])

        @method("skills.inspect", "Full skill record including instructions (as untrusted text)",
                skill_id=_id_param("skl"), include_instructions=P(bool, default=True))
        async def _(a, c):
            return self.spores.inspect(a["skill_id"], a["include_instructions"])

        @method("skills.import_local", "Record a local skill directory. Never executes or installs anything",
                path=P(str, required=True, max_len=4096))
        async def _(a, c):
            return self.spores.import_local(a["path"])

        # -- agents --
        @method("agents.list", "Agent CLIs and their detected state", refresh=P(bool, default=False))
        async def _(a, c):
            return {"agents": await self.grove.list(a["refresh"])}

        @method("agents.status", "Detailed state for one agent", agent_id=P(str, required=True, choices=("claude", "codex")),
                refresh=P(bool, default=False))
        async def _(a, c):
            return await self.grove.status(a["agent_id"], a["refresh"])

        # -- plans --
        @method("plans.create", "Ask an agent for a proposed workflow (asynchronous; watch plan.updated)",
                objective=P(str, required=True, max_len=4000), project_id=proj_opt,
                agent_id=P(str, default="claude", choices=("claude", "codex")),
                max_steps=P(int, default=8, min=1, max=MAX_PLAN_STEPS))
        async def _(a, c):
            if not a["objective"].strip():
                raise BrainError(ErrorCode.INVALID_PARAMS, "'objective' must not be blank")
            return await self.pathfinder.create(a["objective"], a["project_id"], a["agent_id"], a["max_steps"])

        @method("plans.get", "A plan and its validation result", plan_id=_id_param("pln"))
        async def _(a, c):
            return self.pathfinder.get(a["plan_id"])

        @method("plans.validate", "Validate a plan body (e.g. after the user edits it)",
                plan=P(dict, required=True), project_id=proj_opt)
        async def _(a, c):
            return await self.pathfinder.validate(a["plan"], a["project_id"])

        # -- workflows --
        @method("workflows.list", "Saved workflows", project_id=proj_opt, limit=P(int, default=50, min=1, max=500))
        async def _(a, c):
            return {"workflows": self.hyphae.list_workflows(a["project_id"], a["limit"])}

        @method("workflows.create", "Save a validated workflow from a definition or a ready plan",
                project_id=proj_opt, definition=P(dict), plan_id=_id_param("pln", required=False))
        async def _(a, c):
            if (a["definition"] is None) == (a["plan_id"] is None):
                raise BrainError(ErrorCode.INVALID_PARAMS, "provide exactly one of 'definition' or 'plan_id'")
            if a["plan_id"]:
                plan = self.pathfinder.get(a["plan_id"])
                if plan["status"] != "ready":
                    raise BrainError(ErrorCode.ILLEGAL_STATE, f"plan is {plan['status']}, not ready")
                return await self.hyphae.create_workflow(plan["project_id"], plan["plan"], source_plan_id=plan["id"])
            return await self.hyphae.create_workflow(a["project_id"], a["definition"])

        @method("workflows.get", "One workflow", workflow_id=_id_param("wf"))
        async def _(a, c):
            return self.hyphae.get_workflow(a["workflow_id"])

        # -- runs --
        @method("runs.start", "Start a workflow run. approve=true is the user's explicit go-ahead",
                workflow_id=_id_param("wf"), approve=P(bool, required=True))
        async def _(a, c):
            if a["approve"] is not True:
                raise BrainError(ErrorCode.APPROVAL_REQUIRED, "runs.start requires approve=true")
            return await self.hyphae.start_run(a["workflow_id"])

        @method("runs.get", "Run with per-step state, results and verification", run_id=_id_param("run"))
        async def _(a, c):
            return self.hyphae.get_run(a["run_id"])

        @method("runs.list", "Recent runs", workflow_id=_id_param("wf", required=False), project_id=proj_opt,
                status=P(str, choices=RUN_STATUSES), limit=P(int, default=50, min=1, max=500))
        async def _(a, c):
            return {"runs": self.journal.list_runs(a["workflow_id"], a["project_id"], a["status"], a["limit"])}

        @method("runs.cancel", "Cancel an active run (terminates its agent processes)", run_id=_id_param("run"))
        async def _(a, c):
            return await self.hyphae.cancel_run(a["run_id"])

        @method("runs.approve", "Approve or reject a step that is awaiting approval",
                run_id=_id_param("run"), step_id=P(str, required=True, max_len=48), approve=P(bool, required=True))
        async def _(a, c):
            return self.hyphae.approve(a["run_id"], a["step_id"], a["approve"])

        @method("runs.events", "Durable, ordered event history of a run (for replay after reconnect)",
                run_id=_id_param("run"), after_seq=P(int, default=0, min=0), limit=P(int, default=200, min=1, max=1000))
        async def _(a, c):
            self.hyphae.get_run(a["run_id"])
            return self.journal.events(a["run_id"], a["after_seq"], a["limit"])

    # ------------------------------------------------------------------------
    async def health(self) -> dict:
        db_ok = self.db.ok()
        active_runs = self.db.one("SELECT COUNT(*) FROM runs WHERE status IN ('queued','running','awaiting_approval')")[0]
        return {
            "status": "ok" if db_ok else "degraded",
            "version": __version__, "protocol": PROTOCOL_VERSION, "instance_id": self.instance_id,
            "pid": os.getpid(), "uptime_s": round(time.time() - self.started, 1),
            "database": {"ok": db_ok, "schema_version": self.db.schema_version, "expected_schema_version": SCHEMA_VERSION},
            "active_runs": active_runs, "active_agent_jobs": len(self.runner.active),
            "active_project_id": self.projects.active_id,
        }

    async def capabilities(self) -> dict:
        agents = {a["id"]: a for a in await self.grove.list()}

        def feat(status: str, reason: str | None = None) -> dict:
            return {"status": status, **({"reason": reason} if reason else {})}

        def agent_feat(aid: str) -> dict:
            a = agents[aid]
            return feat("supported") if a["status"] == "available" else feat("unavailable", a["detail"] or a["status"])

        any_agent = any(a["status"] == "available" for a in agents.values())
        codex_write = agents["codex"]["status"] == "available" and "write" in agents["codex"]["modes"]
        graphify = self.cortex.graphify_path()
        features = {
            "ipc.unix_socket": feat("supported"),
            "ipc.event_subscriptions": feat("supported"),
            "projects.registry": feat("supported"),
            "cortex.queries": feat("supported"),
            "cortex.graph_update": feat("supported", "CLI only: cedar-brain graph update") if graphify
            else feat("unavailable", "graphify executable not found"),
            "skills.local_discovery": feat("supported"),
            "skills.local_import": feat("supported"),
            "skills.remote_install": feat("not_implemented", "planned after local discovery is proven"),
            "skills.execution": feat("not_implemented", "skills are inspected, never executed, in this release"),
            "agents.claude": agent_feat("claude"),
            "agents.codex": agent_feat("codex"),
            "agents.claude.write": feat("unavailable", "claude -p has no OS-level sandbox; Claude steps are read-only"),
            "agents.codex.write": feat("supported") if codex_write else feat("unavailable", "Codex CLI not available"),
            "agents.session_resume": feat("not_implemented", "sessions are recorded, never resumed"),
            "agents.ollama": feat("not_implemented", "planned for a later stage"),
            "pathfinder.plans": feat("supported") if any_agent else feat("unavailable", "no agent CLI available"),
            "hyphae.workflows": feat("supported"),
            "hyphae.parallel_steps": feat("supported"),
            "hyphae.step_approval": feat("supported"),
            "hyphae.auto_merge": feat("not_implemented", "by design: never merges or pushes"),
            "remote_access": feat("not_implemented", "by design: local Unix socket only"),
        }
        return {
            "protocol": PROTOCOL_VERSION, "version": __version__,
            "features": features,
            "methods": sorted(self.methods),
            "executors": executor_catalog(),
            "limits": {
                "max_message_bytes": MAX_MESSAGE_BYTES, "max_plan_steps": MAX_PLAN_STEPS,
                "max_parallel_steps": self.config.max_parallel_steps,
                "max_agent_processes": self.config.max_agent_processes,
                "default_step_timeout_s": self.config.default_step_timeout_s,
            },
            "event_topics": list(TOPICS),
        }
