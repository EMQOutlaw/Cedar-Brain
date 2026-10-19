"""Mock server for frontend development.

Speaks the real protocol (same envelope validation, same socket rules) but
answers from fixture files and replays a scripted event stream. No agents,
git or database are involved.

Fixture directory layout:
  responses.json      {"method.name": <result object> | {"$error": {code, message}}}
  event_stream.jsonl  one {"after_ms": int, "event": str, "data": {...}} per line,
                      replayed to subscribers after runs.start or plans.create
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from .config import ensure_private_dir
from .ipc.server import clear_stale_socket
from .protocol import BrainError, ErrorCode, encode, err, event, ok, parse_request
from .security.permissions import peer_credentials

log = logging.getLogger(__name__)
BUNDLED = Path(__file__).parent / "fixtures"
TRIGGERS = {"runs.start", "plans.create"}


async def serve_mock(socket_path: Path, fixtures: Path | None = None, speed: float = 1.0) -> None:
    fixtures = fixtures or BUNDLED
    responses = json.loads((fixtures / "responses.json").read_text())
    stream = [json.loads(l) for l in (fixtures / "event_stream.jsonl").read_text().splitlines() if l.strip()]
    ensure_private_dir(socket_path.parent)
    clear_stale_socket(socket_path)  # never hijack a live backend's socket
    conns: dict = {}  # writer -> subscribed topics

    async def replay():
        for item in stream:
            await asyncio.sleep(item.get("after_ms", 0) / 1000 / max(speed, 0.01))
            for w, topics in list(conns.items()):
                if item["event"].split(".")[0] in topics:
                    w.write(encode(event(item["event"], item["data"])))

    async def on_client(reader, writer):
        creds = peer_credentials(writer.get_extra_info("socket"))
        if creds is None or creds[1] != os.getuid():
            writer.close()
            return
        topics: set = set()
        conns[writer] = topics
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    req = parse_request(line)
                except BrainError as e:
                    writer.write(encode(err((e.details or {}).get("id"), BrainError(e.code, e.message))))
                    continue
                if req.method == "events.subscribe":
                    topics |= set(req.params.get("topics", []))
                    writer.write(encode(ok(req.id, {"topics": sorted(topics)})))
                    continue
                if req.method not in responses:
                    writer.write(encode(err(req.id, BrainError(ErrorCode.UNKNOWN_METHOD, f"unknown method {req.method!r}"))))
                    continue
                res = responses[req.method]
                if isinstance(res, dict) and "$error" in res:
                    writer.write(encode(err(req.id, BrainError(res["$error"]["code"], res["$error"]["message"]))))
                else:
                    writer.write(encode(ok(req.id, res)))
                if req.method in TRIGGERS:
                    asyncio.create_task(replay())
                await writer.drain()
        finally:
            conns.pop(writer, None)
            writer.close()

    old = os.umask(0o177)
    try:
        server = await asyncio.start_unix_server(on_client, path=str(socket_path))
    finally:
        os.umask(old)
    log.info("mock server on %s (fixtures: %s)", socket_path, fixtures)
    async with server:
        await server.serve_forever()
