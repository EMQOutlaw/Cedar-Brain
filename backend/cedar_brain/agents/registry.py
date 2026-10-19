"""Agent Grove: the set of known adapters and their detected state."""

from __future__ import annotations

import asyncio

from ..config import Config
from ..protocol import BrainError, ErrorCode
from .base import AgentAdapter
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .runner import AgentRunner

ADAPTERS = {"claude": ClaudeAdapter, "codex": CodexAdapter}


class AgentGrove:
    def __init__(self, config: Config, runner: AgentRunner):
        self.runner = runner
        self.adapters: dict[str, AgentAdapter] = {aid: cls(config.agent(aid)) for aid, cls in ADAPTERS.items()}

    def get(self, agent_id: str) -> AgentAdapter:
        adapter = self.adapters.get(agent_id)
        if adapter is None:
            raise BrainError(ErrorCode.NOT_FOUND, f"unknown agent {agent_id!r}")
        return adapter

    async def list(self, refresh: bool = False) -> list[dict]:
        infos = await asyncio.gather(*(a.detect(force=refresh) for a in self.adapters.values()))
        out = []
        for info in infos:
            d = info.to_dict()
            d["active_jobs"] = self.runner.active_count(info.id)
            out.append(d)
        return out

    async def status(self, agent_id: str, refresh: bool = False) -> dict:
        info = await self.get(agent_id).detect(force=refresh)
        d = info.to_dict()
        d["active_jobs"] = self.runner.active_count(agent_id)
        rows = self.runner.db.all(
            "SELECT id, owner_kind, owner_id, status, mode, session_id, exit_code, started_at, finished_at "
            "FROM agent_jobs WHERE agent_id=? ORDER BY started_at DESC LIMIT 10", (agent_id,))
        d["recent_jobs"] = [dict(r) for r in rows]
        d["max_concurrent_processes"] = self.runner.max_processes
        return d
