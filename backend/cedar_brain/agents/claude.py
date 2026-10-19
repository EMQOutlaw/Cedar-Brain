"""Claude Code CLI adapter (``claude -p --output-format stream-json``).

Read-only only: ``claude -p`` has no OS-level sandbox, so write steps are
never offered for Claude (see docs/SECURITY.md).
"""

from __future__ import annotations

import json

from ..util import run_cmd
from .base import AgentAdapter, AgentInfo, AgentTask, ParseState, Progress

READ_ONLY_TOOLS = "Read,Grep,Glob"


class ClaudeAdapter(AgentAdapter):
    id = "claude"
    name = "Claude Code"
    required_flags = ("--print", "--output-format", "--verbose", "--permission-mode", "--tools",
                      "--setting-sources")
    write_capable = False

    async def auth_status(self, exe, env):
        try:
            code, out, _ = await run_cmd([exe, "auth", "status"], timeout=10, env=env)
            data = json.loads(out) if code == 0 else None
        except Exception:
            return None, None
        if not isinstance(data, dict):
            return (False if code != 0 else None), None
        method = data.get("authMethod")
        return bool(data.get("loggedIn")), method if isinstance(method, str) else None

    def build_argv(self, task: AgentTask, info: AgentInfo) -> list[str]:
        if task.mode != "read_only":
            raise ValueError("Claude steps are read-only in this release")
        argv = [info.executable, "--print", "--output-format", "stream-json", "--verbose",
                "--permission-mode", "dontAsk", "--tools", READ_ONLY_TOOLS,
                # -p skips the workspace-trust dialog, so project/local settings (and their
                # hooks) would load from the untrusted repo. Load user settings only.
                "--setting-sources", "user"]
        if "--permission-prompts" in info.flags:
            argv += ["--permission-prompts", "none"]
        if "--strict-mcp-config" in info.flags:
            argv.append("--strict-mcp-config")  # no MCP servers: smaller attack surface
        if self.cfg.model:
            argv += ["--model", self.cfg.model]
        return argv  # prompt is sent on stdin

    def parse_line(self, obj: dict, state: ParseState) -> list[Progress]:
        kind = obj.get("type")
        out: list[Progress] = []
        if isinstance(obj.get("session_id"), str):
            state.session_id = obj["session_id"][:100]
        if kind == "system" and obj.get("subtype") == "init":
            out.append(Progress("status", "session started"))
        elif kind == "assistant":
            content = (obj.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    out.append(Progress("message", block["text"]))
                elif block.get("type") == "tool_use":
                    out.append(Progress("tool", str(block.get("name", "tool"))[:80]))
        elif kind == "result":
            if isinstance(obj.get("result"), str):
                state.final_text = obj["result"]
            if obj.get("is_error") or obj.get("subtype") not in (None, "success"):
                state.reported_error = (obj.get("result") or obj.get("subtype") or "error")
                if not isinstance(state.reported_error, str):
                    state.reported_error = str(state.reported_error)
            if isinstance(obj.get("usage"), dict):
                state.usage = {k: v for k, v in obj["usage"].items() if isinstance(v, (int, float))}
        return out
