"""XDG path resolution and user configuration.

Configuration lives in ``$XDG_CONFIG_HOME/cedar-brain/config.json`` and is
optional. Unknown keys are ignored with a warning so that an older backend
keeps working with a newer config file.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

APP = "cedar-brain"


def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var, "")
    # The XDG spec says relative values must be ignored.
    if value and os.path.isabs(value):
        return Path(value)
    return Path.home() / fallback


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / APP


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / APP


def runtime_dir() -> Path:
    value = os.environ.get("XDG_RUNTIME_DIR", "")
    if value and os.path.isabs(value):
        return Path(value) / APP
    # No runtime dir (e.g. a bare ssh session): fall back to a per-user
    # directory under the state dir, which is still owner-only.
    return state_dir() / "run"


def socket_path() -> Path:
    override = os.environ.get("CEDAR_BRAIN_SOCKET", "")
    if override and os.path.isabs(override):
        return Path(override)
    return runtime_dir() / "brain.sock"


@dataclass
class AgentConfig:
    executable: str | None = None  # None → look up on PATH
    enabled: bool = True
    model: str | None = None


@dataclass
class Config:
    skill_dirs: list[Path] = field(default_factory=list)
    import_roots: list[Path] = field(default_factory=list)
    agents: dict[str, AgentConfig] = field(default_factory=dict)
    max_parallel_steps: int = 2
    max_agent_processes: int = 2
    default_step_timeout_s: int = 900
    planner_timeout_s: int = 300
    graphify_executable: str | None = None
    state_dir: Path = field(default_factory=state_dir)
    socket_path: Path = field(default_factory=socket_path)

    @property
    def db_path(self) -> Path:
        return self.state_dir / "brain.db"

    @property
    def logs_dir(self) -> Path:
        return self.state_dir / "logs"

    @property
    def worktrees_dir(self) -> Path:
        return self.state_dir / "worktrees"

    def agent(self, agent_id: str) -> AgentConfig:
        return self.agents.get(agent_id) or AgentConfig()


def default_skill_dirs() -> list[Path]:
    home = Path.home()
    return [
        config_dir() / "skills",
        home / ".claude" / "skills",
        home / ".codex" / "skills",
        home / ".agents" / "skills",
    ]


def _clamp(value, lo: int, hi: int, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi:
        return value
    return default


def load_config(path: Path | None = None) -> Config:
    path = path or (config_dir() / "config.json")
    raw: dict = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("top level must be an object")
        except (OSError, ValueError) as exc:
            log.warning("ignoring unreadable config %s: %s", path, exc)
            raw = {}
    known = {
        "skill_dirs", "import_roots", "agents", "max_parallel_steps", "max_agent_processes",
        "default_step_timeout_s", "planner_timeout_s", "graphify_executable",
    }
    for key in raw.keys() - known:
        log.warning("unknown config key %r ignored", key)

    def paths(key: str, default: list[Path]) -> list[Path]:
        value = raw.get(key)
        if not isinstance(value, list):
            return default
        return [Path(os.path.expanduser(p)) for p in value if isinstance(p, str) and p]

    agents: dict[str, AgentConfig] = {}
    for agent_id, spec in (raw.get("agents") or {}).items():
        if isinstance(spec, dict):
            exe = spec.get("executable")
            agents[agent_id] = AgentConfig(
                executable=os.path.expanduser(exe) if isinstance(exe, str) and exe else None,
                enabled=spec.get("enabled", True) is not False,
                model=spec.get("model") if isinstance(spec.get("model"), str) else None,
            )
    gx = raw.get("graphify_executable")
    return Config(
        skill_dirs=paths("skill_dirs", default_skill_dirs()),
        import_roots=paths("import_roots", [Path.home()]),
        agents=agents,
        max_parallel_steps=_clamp(raw.get("max_parallel_steps"), 1, 8, 2),
        max_agent_processes=_clamp(raw.get("max_agent_processes"), 1, 8, 2),
        default_step_timeout_s=_clamp(raw.get("default_step_timeout_s"), 10, 7200, 900),
        planner_timeout_s=_clamp(raw.get("planner_timeout_s"), 10, 3600, 300),
        graphify_executable=os.path.expanduser(gx) if isinstance(gx, str) and gx else None,
    )


def ensure_private_dir(path: Path) -> Path:
    """Create ``path`` (and parents) and force mode 0700 on the leaf."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path
