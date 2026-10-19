"""Codex CLI adapter (``codex exec --json``).

Read-only steps use ``--sandbox read-only``. Write steps use
``--sandbox workspace-write`` with ``-C`` pointing at a backend-created git
worktree. On Linux, Codex enforces that sandbox at the OS level.
"""

from __future__ import annotations

from ..util import run_cmd
from .base import AgentAdapter, AgentInfo, AgentTask, ParseState, Progress


class CodexAdapter(AgentAdapter):
    id = "codex"
    name = "Codex CLI"
    required_flags = ("--json", "--sandbox", "--cd")
    write_capable = True

    def help_args(self) -> list[str]:
        return ["exec", "--help"]

    async def auth_status(self, exe, env):
        try:
            code, out, err = await run_cmd([exe, "login", "status"], timeout=10, env=env)
        except Exception:
            return None, None
        text = (out + err).lower()
        if code == 0 and "logged in" in text:
            method = "chatgpt" if "chatgpt" in text else ("api_key" if "api key" in text else None)
            return True, method
        return (False if "not logged in" in text or code != 0 else None), None

    def write_supported(self, info: AgentInfo) -> bool:
        return "--sandbox" in info.flags

    def build_argv(self, task: AgentTask, info: AgentInfo) -> list[str]:
        sandbox = {"read_only": "read-only", "write": "workspace-write"}[task.mode]
        argv = [info.executable, "exec", "--json", "--sandbox", sandbox, "--cd", task.cwd]
        if "--color" in info.flags:
            argv += ["--color", "never"]
        if task.output_schema_path and "--output-schema" in info.flags:
            argv += ["--output-schema", task.output_schema_path]
        if self.cfg.model:
            argv += ["--model", self.cfg.model]
        argv.append("-")  # read the prompt from stdin
        return argv

    def parse_line(self, obj: dict, state: ParseState) -> list[Progress]:
        kind = obj.get("type")
        out: list[Progress] = []
        if kind == "thread.started" and isinstance(obj.get("thread_id"), str):
            state.session_id = obj["thread_id"][:100]
            out.append(Progress("status", "session started"))
        elif kind in ("item.completed", "item.started"):
            item = obj.get("item") or {}
            itype = item.get("type")
            if itype == "agent_message" and isinstance(item.get("text"), str) and kind == "item.completed":
                state.final_text = item["text"]
                out.append(Progress("message", item["text"]))
            elif itype in ("command_execution", "file_change", "mcp_tool_call", "web_search") and kind == "item.started":
                label = item.get("command") or itype
                out.append(Progress("tool", str(label)[:200]))
            elif itype == "error" and isinstance(item.get("message"), str):
                out.append(Progress("error", item["message"]))
        elif kind == "turn.completed":
            usage = obj.get("usage")
            if isinstance(usage, dict):
                state.usage = {k: v for k, v in usage.items() if isinstance(v, (int, float))}
        elif kind in ("turn.failed", "error"):
            err = obj.get("error") if isinstance(obj.get("error"), dict) else obj
            msg = err.get("message") if isinstance(err.get("message"), str) else kind
            state.reported_error = msg
            out.append(Progress("error", msg))
        return out
