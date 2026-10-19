"""`cedar-brain` command line."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .config import load_config


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def _client(sock: Path):
    from .ipc.client import Client
    try:
        return Client(sock, timeout=120)
    except OSError:
        return None


async def _direct(config, method: str, params: dict):
    from .app import App
    app = App(config)
    try:
        return await app.call(method, params)
    finally:
        await app.shutdown()


def invoke(config, method: str, params: dict | None = None):
    """Call a method on the running server, or in-process if no server is up."""
    from .ipc.client import ClientError
    from .protocol import BrainError
    params = params or {}
    client = _client(config.socket_path)
    try:
        if client is not None:
            with client:
                return client.call(method, params)
        return asyncio.run(_direct(config, method, params))
    except ClientError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    except BrainError as e:
        print(f"error: {e.code}: {e.message}", file=sys.stderr)
        sys.exit(1)


def cmd_serve(config, args) -> int:
    from .app import App
    from .ipc.server import AlreadyRunning, run_server
    from .storage.database import SchemaTooNew
    try:
        app = App(config)
    except SchemaTooNew as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    try:
        asyncio.run(run_server(app, config.socket_path))
    except AlreadyRunning as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    return 0


def cmd_doctor(config, args) -> int:
    from .diagnostics.health import doctor
    checks = asyncio.run(doctor(config))
    if args.json:
        _print(checks)
    else:
        for c in checks:
            print(f"[{'ok' if c['ok'] else '!!'}] {c['check']}: {c['detail']}")
    required = {"python>=3.12", "git on PATH"}
    return 0 if all(c["ok"] for c in checks if c["check"] in required) else 1


def cmd_graph(config, args) -> int:
    params = {"project_id": args.project} if args.project else {}
    if args.action == "status":
        _print(invoke(config, "cortex.status", params))
        return 0
    if args.action == "reload":
        _print(invoke(config, "cortex.reload", params))
        return 0
    # update: run graphify in this process (it can take minutes), then tell a running server to reload.
    from .app import App
    from .protocol import BrainError

    async def run():
        app = App(config)
        try:
            project = app.projects.resolve(args.project)
            return await app.cortex.update(project)
        finally:
            await app.shutdown()
    try:
        result = asyncio.run(run())
    except BrainError as e:
        print(f"error: {e.code}: {e.message}", file=sys.stderr)
        return 1
    client = _client(config.socket_path)
    if client is not None:
        with client:
            client.call("cortex.reload", params)
        result["server_reloaded"] = True
    _print(result)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cedar-brain", description="CEDAR Brain — Mycelium backend")
    parser.add_argument("--version", action="version", version=f"cedar-brain {__version__}")
    parser.add_argument("--socket", help="override the socket path")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve", help="run the backend server")
    sub.add_parser("brain", help="alias for serve")
    d = sub.add_parser("doctor", help="check the environment")
    d.add_argument("--json", action="store_true")

    c = sub.add_parser("call", help="call any protocol method: call METHOD '{json params}'")
    c.add_argument("method")
    c.add_argument("params", nargs="?", default="{}")

    p = sub.add_parser("projects", help="manage projects")
    psub = p.add_subparsers(dest="action", required=True)
    psub.add_parser("list")
    pa = psub.add_parser("add")
    pa.add_argument("path")
    pa.add_argument("--name")
    pa.add_argument("--graph")
    pa.add_argument("--select", action="store_true")
    ps = psub.add_parser("select")
    ps.add_argument("project_id")
    pst = psub.add_parser("status")
    pst.add_argument("project_id", nargs="?")

    s = sub.add_parser("skills", help="skill library")
    ssub = s.add_subparsers(dest="action", required=True)
    sl = ssub.add_parser("list")
    sl.add_argument("--refresh", action="store_true")
    si = ssub.add_parser("inspect")
    si.add_argument("skill_id")
    sim = ssub.add_parser("import")
    sim.add_argument("path")

    a = sub.add_parser("agents", help="agent CLIs")
    asub = a.add_subparsers(dest="action", required=True)
    al = asub.add_parser("list")
    al.add_argument("--refresh", action="store_true")

    g = sub.add_parser("graph", help="Graphify graph")
    g.add_argument("action", choices=("status", "update", "reload"))
    g.add_argument("--project")

    m = sub.add_parser("mock-serve", help="serve canned fixtures for frontend development")
    m.add_argument("--fixtures", help="fixture directory (default: bundled)")
    m.add_argument("--speed", type=float, default=1.0, help="event stream speed multiplier")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    if args.socket:
        os.environ["CEDAR_BRAIN_SOCKET"] = os.path.abspath(args.socket)
    config = load_config()

    if args.cmd in ("serve", "brain"):
        return cmd_serve(config, args)
    if args.cmd == "doctor":
        return cmd_doctor(config, args)
    if args.cmd == "mock-serve":
        from .ipc.server import AlreadyRunning
        from .mock import serve_mock
        try:
            asyncio.run(serve_mock(config.socket_path, Path(args.fixtures) if args.fixtures else None, args.speed))
        except AlreadyRunning as e:
            print(f"error: {e}. Point the mock at another path: cedar-brain --socket /path/mock.sock mock-serve", file=sys.stderr)
            return 3
        except KeyboardInterrupt:
            pass
        return 0
    if args.cmd == "call":
        try:
            params = json.loads(args.params)
        except ValueError:
            print("error: params must be JSON", file=sys.stderr)
            return 2
        _print(invoke(config, args.method, params))
        return 0
    if args.cmd == "projects":
        if args.action == "list":
            _print(invoke(config, "projects.list"))
        elif args.action == "add":
            _print(invoke(config, "projects.add", {"path": os.path.abspath(args.path), "name": args.name,
                                                   "graph_path": os.path.abspath(args.graph) if args.graph else None,
                                                   "select": args.select}))
        elif args.action == "select":
            _print(invoke(config, "projects.select", {"project_id": args.project_id}))
        elif args.action == "status":
            _print(invoke(config, "projects.status", {"project_id": args.project_id}))
        return 0
    if args.cmd == "skills":
        if args.action == "list":
            _print(invoke(config, "skills.list", {"refresh": args.refresh}))
        elif args.action == "inspect":
            _print(invoke(config, "skills.inspect", {"skill_id": args.skill_id}))
        elif args.action == "import":
            _print(invoke(config, "skills.import_local", {"path": os.path.abspath(args.path)}))
        return 0
    if args.cmd == "agents":
        _print(invoke(config, "agents.list", {"refresh": args.refresh}))
        return 0
    if args.cmd == "graph":
        return cmd_graph(config, args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
