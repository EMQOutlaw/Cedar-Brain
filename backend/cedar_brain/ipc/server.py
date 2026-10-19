"""Unix-domain-socket JSON Lines server.

* Socket directory 0700 and socket 0600; peers with a different uid are
  dropped (SO_PEERCRED). That stops other users, not same-user processes.
* One instance per socket path, enforced with flock.
* Requests on one connection are handled concurrently (bounded). Responses
  carry the request id, so clients must not rely on ordering.
* Each connection has a bounded outgoing queue. If a slow client lets
  events pile up, events are dropped and a ``stream.gap`` event tells it
  to resync (runs.events / *.get).
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import signal
import socket
import stat
from pathlib import Path

from ..app import App
from ..config import ensure_private_dir
from ..events import TOPICS
from ..protocol import (MAX_MESSAGE_BYTES, BrainError, ErrorCode, encode, err, event, ok,
                        parse_request)
from ..security.permissions import peer_credentials

log = logging.getLogger(__name__)

MAX_INFLIGHT_PER_CONN = 32
OUT_QUEUE_SIZE = 1000
MAX_CONNECTIONS = 64


class AlreadyRunning(RuntimeError):
    pass


def clear_stale_socket(path: Path) -> None:
    """Remove a dead socket file; refuse if a server answers or the path is not a socket."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(st.st_mode):
        raise RuntimeError(f"{path} exists and is not a socket; refusing to remove it")
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.connect(str(path))
        raise AlreadyRunning(f"a server is already answering on {path}")
    except (ConnectionRefusedError, FileNotFoundError):
        os.unlink(path)
    finally:
        probe.close()


class Connection:
    def __init__(self, server: "BrainServer", reader, writer, peer):
        self.server, self.reader, self.writer, self.peer = server, reader, writer, peer
        self.topics: set[str] = set()
        self.out: asyncio.Queue = asyncio.Queue(OUT_QUEUE_SIZE)
        self.dropped = 0
        self.inflight = asyncio.Semaphore(MAX_INFLIGHT_PER_CONN)
        self.tasks: set[asyncio.Task] = set()
        self.closed = False

    def send_event(self, name: str, data: dict) -> None:
        if self.closed or name.split(".", 1)[0] not in self.topics:
            return
        try:
            self.out.put_nowait(encode(event(name, data)))
        except asyncio.QueueFull:
            self.dropped += 1

    async def send(self, msg: dict) -> None:
        if not self.closed:
            await self.out.put(encode(msg))

    async def writer_loop(self) -> None:
        try:
            while True:
                data = await self.out.get()
                self.writer.write(data)
                if self.dropped and self.out.empty():
                    n, self.dropped = self.dropped, 0
                    self.writer.write(encode(event("stream.gap", {"dropped_events": n})))
                await self.writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass

    async def handle(self, line: bytes) -> None:
        async with self.inflight:
            req_id = None
            try:
                req = parse_request(line)
                req_id = req.id
                result = await self.server.app.dispatch(req, self)
                await self.send(ok(req.id, result))
            except BrainError as e:
                rid = req_id or (e.details or {}).get("id")
                if e.details and "id" in e.details and len(e.details) == 1:
                    e = BrainError(e.code, e.message)
                await self.send(err(rid, e))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("unhandled error in request")
                await self.send(err(req_id, BrainError(ErrorCode.INTERNAL, "internal error; see backend log")))


class BrainServer:
    def __init__(self, app: App, socket_path: Path):
        self.app = app
        self.socket_path = Path(socket_path)
        self.conns: set[Connection] = set()
        self._server: asyncio.base_events.Server | None = None
        self._lock_fd: int | None = None
        self._stopped = asyncio.Event()
        self._unsub = None

    # -- lifecycle -------------------------------------------------------------
    def _acquire_lock(self) -> None:
        ensure_private_dir(self.socket_path.parent)
        lock_path = self.socket_path.with_name(self.socket_path.name + ".lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise AlreadyRunning(f"a cedar-brain server is already running (lock held: {lock_path})")
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        self._lock_fd = fd

    def _clear_stale_socket(self) -> None:
        clear_stale_socket(self.socket_path)

    async def start(self) -> None:
        self._acquire_lock()
        self._clear_stale_socket()
        old_umask = os.umask(0o177)
        try:
            self._server = await asyncio.start_unix_server(self._on_client, path=str(self.socket_path),
                                                           limit=MAX_MESSAGE_BYTES + 1)
        finally:
            os.umask(old_umask)
        os.chmod(self.socket_path, 0o600)
        self._unsub = self.app.bus.subscribe(self._broadcast)
        log.info("listening on %s", self.socket_path)

    def _broadcast(self, name: str, data: dict) -> None:
        for c in list(self.conns):
            c.send_event(name, data)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        self.app.bus.emit("system.shutdown", {"instance_id": self.app.instance_id})
        await asyncio.sleep(0.05)  # let subscribers receive it
        for c in list(self.conns):
            for t in c.tasks:
                t.cancel()
        await self.app.shutdown()
        for c in list(self.conns):
            c.closed = True
            c.writer.close()
        try:
            await asyncio.wait_for(self._server.wait_closed(), 5)
        except asyncio.TimeoutError:
            pass
        if self._unsub:
            self._unsub()
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass
        if self._lock_fd is not None:
            os.close(self._lock_fd)
        self._server = None
        self._stopped.set()

    async def serve_forever(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.ensure_future(self.stop()))
        await self._stopped.wait()

    # -- connections -------------------------------------------------------------
    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        sock = writer.get_extra_info("socket")
        creds = peer_credentials(sock) if sock is not None else None
        if creds is None or creds[1] != os.getuid():
            log.warning("rejected peer %s (uid mismatch or unknown)", creds)
            writer.close()
            return
        if len(self.conns) >= MAX_CONNECTIONS:
            writer.write(encode(err(None, BrainError(ErrorCode.BUSY, "too many connections"))))
            writer.close()
            return
        conn = Connection(self, reader, writer, creds)
        self.conns.add(conn)
        wtask = asyncio.create_task(conn.writer_loop())
        try:
            while True:
                try:
                    line = await reader.readuntil(b"\n")
                except asyncio.IncompleteReadError as e:
                    if e.partial.strip():
                        await self._spawn(conn, e.partial)
                    break
                except asyncio.LimitOverrunError:
                    # Cannot resynchronise reliably inside an oversized line: report and close.
                    await conn.send(err(None, BrainError(ErrorCode.MESSAGE_TOO_LARGE,
                                                         f"message exceeds {MAX_MESSAGE_BYTES} bytes; closing connection")))
                    break
                except (ConnectionError, ValueError):
                    break
                if line.strip():
                    await self._spawn(conn, line)
        finally:
            if conn.tasks:
                await asyncio.gather(*conn.tasks, return_exceptions=True)
            # let queued responses flush
            for _ in range(50):
                if conn.out.empty():
                    break
                await asyncio.sleep(0.01)
            conn.closed = True
            wtask.cancel()
            self.conns.discard(conn)
            try:
                writer.close()
            except Exception:
                pass

    async def _spawn(self, conn: Connection, line: bytes) -> None:
        t = asyncio.create_task(conn.handle(line))
        conn.tasks.add(t)
        t.add_done_callback(conn.tasks.discard)


async def run_server(app: App, socket_path: Path, ready: asyncio.Event | None = None) -> None:
    recovered = app.recover()
    if any(recovered.values()):
        log.warning("recovered interrupted work: %s", {k: len(v) for k, v in recovered.items()})
    server = BrainServer(app, socket_path)
    await server.start()
    if ready:
        ready.set()
    await server.serve_forever()


__all__ = ["BrainServer", "AlreadyRunning", "run_server", "TOPICS"]
