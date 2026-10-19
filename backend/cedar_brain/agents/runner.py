"""Supervised execution of agent CLI processes.

* argv only, never a shell; the prompt is written to stdin.
* Each process gets its own session/process group, so cancellation can
  signal the whole tree without touching unrelated CLI sessions.
* pid plus /proc start time are persisted. After a backend crash, recovery
  kills an orphan only if both still match.
* stdout/stderr are read in bounded chunks into size-capped, redacted logs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from ..config import ensure_private_dir
from ..security.permissions import child_env
from ..security.redaction import redact
from ..storage.database import Database, dumps
from ..util import new_id
from .base import AgentAdapter, AgentStatus, AgentTask, ParseState, Progress, classify_failure

log = logging.getLogger(__name__)

MAX_LINE_BYTES = 1024 * 1024
MAX_STDOUT_BYTES = 32 * 1024 * 1024
MAX_STDERR_BYTES = 4 * 1024 * 1024
MAX_LOG_BYTES = 8 * 1024 * 1024
MAX_FINAL_TEXT = 256 * 1024
MAX_PROGRESS_TEXT = 2000
MAX_PROGRESS_EVENTS = 400
TERM_GRACE_S = 5.0

ProgressCb = Callable[[Progress], Awaitable[None] | None]


@dataclass
class JobResult:
    job_id: str
    status: str  # succeeded | failed | cancelled | timed_out
    exit_code: int | None = None
    error: dict | None = None
    final_text: str | None = None
    session_id: str | None = None
    usage: dict | None = None
    stdout_log: str | None = None
    stderr_log: str | None = None
    malformed_lines: int = 0
    duration_s: float = 0.0

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class _Handle:
    job_id: str
    proc: asyncio.subprocess.Process | None = None
    cancel_requested: bool = False
    terminated: asyncio.Event = field(default_factory=asyncio.Event)


def proc_start_ticks(pid: int) -> int | None:
    """Field 22 of /proc/<pid>/stat: start time in clock ticks since boot."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            data = fh.read().decode(errors="replace")
        return int(data[data.rindex(")") + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None


class _CappedLog:
    def __init__(self, path: Path, cap: int):
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        self.fh = os.fdopen(fd, "w", encoding="utf-8")
        self.cap, self.written, self.truncated = cap, 0, False

    def write(self, text: str) -> None:
        if self.truncated:
            return
        text = redact(text)
        if self.written + len(text) > self.cap:
            text = text[: max(0, self.cap - self.written)] + "\n[log truncated]\n"
            self.truncated = True
        self.fh.write(text)
        self.written += len(text)

    def close(self) -> None:
        self.fh.close()


class AgentRunner:
    def __init__(self, db: Database, logs_dir: Path, max_processes: int):
        self.db = db
        self.logs_dir = logs_dir
        self.sem = asyncio.Semaphore(max_processes)
        self.max_processes = max_processes
        self.active: dict[str, _Handle] = {}

    # -- recovery ------------------------------------------------------------
    def recover(self) -> list[str]:
        """Mark jobs from a previous process as interrupted, killing provable orphans."""
        recovered = []
        for row in self.db.all("SELECT id, pid, proc_start_ticks FROM agent_jobs WHERE status IN ('starting','running')"):
            pid, ticks = row["pid"], row["proc_start_ticks"]
            if pid and ticks and proc_start_ticks(pid) == ticks:
                try:
                    os.killpg(pid, signal.SIGTERM)
                    deadline = time.time() + TERM_GRACE_S
                    while time.time() < deadline and proc_start_ticks(pid) == ticks:
                        time.sleep(0.1)
                    if proc_start_ticks(pid) == ticks:
                        os.killpg(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            self.db.execute(
                "UPDATE agent_jobs SET status='interrupted', finished_at=?, error=? WHERE id=?",
                (time.time(), dumps({"code": "INTERRUPTED", "message": "backend stopped while the job was running"}), row["id"]),
            )
            recovered.append(row["id"])
        return recovered

    # -- execution -----------------------------------------------------------
    def active_count(self, agent_id: str | None = None) -> int:
        if agent_id is None:
            return len(self.active)
        rows = self.db.one("SELECT COUNT(*) FROM agent_jobs WHERE agent_id=? AND status IN ('starting','running')", (agent_id,))
        return rows[0]

    async def run(self, adapter: AgentAdapter, task: AgentTask, on_progress: ProgressCb | None = None) -> JobResult:
        job_id = new_id("job")
        info = await adapter.detect()
        if info.status != AgentStatus.AVAILABLE:
            return JobResult(job_id, "failed", error={"code": "AGENT_UNAVAILABLE", "message": info.detail or info.status})
        if task.mode not in info.modes:
            return JobResult(job_id, "failed", error={"code": "AGENT_UNAVAILABLE", "message": f"{adapter.id} does not support mode {task.mode!r}"})
        argv = adapter.argv(task, info)
        # Ownership: the partial unique index rejects a second active job for the same owner.
        try:
            self.db.execute(
                "INSERT INTO agent_jobs(id, owner_kind, owner_id, agent_id, status, mode, cwd, started_at) VALUES (?,?,?,?,?,?,?,?)",
                (job_id, task.owner_kind, task.owner_id, adapter.id, "starting", task.mode, task.cwd, time.time()),
            )
        except Exception as exc:  # sqlite3.IntegrityError
            return JobResult(job_id, "failed", error={"code": "DUPLICATE_JOB", "message": f"owner already has an active job ({exc.__class__.__name__})"})
        handle = _Handle(job_id)
        self.active[job_id] = handle
        try:
            async with self.sem:
                if handle.cancel_requested:
                    return self._finish(JobResult(job_id, "cancelled", error={"code": "AGENT_CANCELLED", "message": "cancelled before start"}))
                return await self._run(adapter, task, argv, handle, on_progress)
        finally:
            self.active.pop(job_id, None)

    async def _run(self, adapter, task, argv, handle: _Handle, on_progress) -> JobResult:
        job_dir = ensure_private_dir(self.logs_dir / "jobs")
        out_path, err_path = job_dir / f"{handle.job_id}.stdout.log", job_dir / f"{handle.job_id}.stderr.log"
        out_log, err_log = _CappedLog(out_path, MAX_LOG_BYTES), _CappedLog(err_path, MAX_LOG_BYTES)
        result = JobResult(handle.job_id, "failed", stdout_log=str(out_path), stderr_log=str(err_path))
        started = time.monotonic()
        state = ParseState()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=task.cwd, env=child_env(adapter.id),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            out_log.close(), err_log.close()
            result.error = {"code": "AGENT_UNAVAILABLE", "message": f"failed to start {adapter.id}: {exc.strerror}"}
            return self._finish(result)
        handle.proc = proc
        self.db.execute("UPDATE agent_jobs SET status='running', pid=?, proc_start_ticks=?, stdout_log=?, stderr_log=? WHERE id=?",
                        (proc.pid, proc_start_ticks(proc.pid), str(out_path), str(err_path), handle.job_id))
        progress_sent = 0
        stderr_tail: list[str] = []
        flood = False

        async def emit(p: Progress) -> None:
            nonlocal progress_sent
            if on_progress is None or progress_sent >= MAX_PROGRESS_EVENTS:
                return
            progress_sent += 1
            p = Progress(p.kind, redact(p.text[:MAX_PROGRESS_TEXT]))
            r = on_progress(p)
            if asyncio.iscoroutine(r):
                await r

        async def feed_stdin() -> None:
            try:
                proc.stdin.write(task.prompt.encode())
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass

        async def read_stdout() -> None:
            nonlocal flood
            async for line in _lines(proc.stdout, MAX_STDOUT_BYTES):
                if line is None:
                    # Keep draining (and discarding) so the pipe reaches EOF;
                    # asyncio only reports exit once every pipe has closed.
                    flood = True
                    self._kill_soon(proc)
                    continue
                out_log.write(line + "\n")
                try:
                    obj = json.loads(line)
                    if not isinstance(obj, dict):
                        raise ValueError
                except ValueError:
                    state.malformed_lines += 1
                    continue
                try:
                    for p in adapter.parse_line(obj, state):
                        await emit(p)
                except Exception:  # a parser bug must not take the backend down
                    log.exception("parser failed on %s output", adapter.id)
                    state.malformed_lines += 1

        async def read_stderr() -> None:
            nonlocal flood
            async for line in _lines(proc.stderr, MAX_STDERR_BYTES):
                if line is None:
                    flood = True
                    self._kill_soon(proc)
                    continue
                err_log.write(line + "\n")
                stderr_tail.append(line)
                del stderr_tail[:-40]

        readers = asyncio.gather(feed_stdin(), read_stdout(), read_stderr())
        try:
            await asyncio.wait_for(asyncio.shield(readers), timeout=task.timeout_s)
            await proc.wait()
        except asyncio.TimeoutError:
            result.status = "timed_out"
            result.error = {"code": "AGENT_TIMEOUT", "message": f"no completion within {task.timeout_s:.0f}s"}
            await self._terminate(proc)
        except asyncio.CancelledError:
            handle.cancel_requested = True
            await self._terminate(proc)
            raise
        finally:
            if proc.returncode is None:
                await self._terminate(proc)
            if not readers.done():
                try:
                    await asyncio.wait_for(readers, 2.0)
                except BaseException:
                    readers.cancel()
                    try:
                        await readers
                    except BaseException:
                        pass
            self._reap_group(proc.pid)
            out_log.close(), err_log.close()
            result.exit_code = proc.returncode
            result.session_id = state.session_id
            result.usage = state.usage
            result.malformed_lines = state.malformed_lines
            result.duration_s = round(time.monotonic() - started, 3)
            if state.final_text is not None:
                result.final_text = redact(state.final_text[:MAX_FINAL_TEXT])
            if handle.cancel_requested:
                result.status = "cancelled"
                result.error = {"code": "AGENT_CANCELLED", "message": "cancelled"}
            elif flood:
                result.status = "failed"
                result.error = {"code": "AGENT_OUTPUT_LIMIT", "message": "agent output exceeded the backend limit"}
            elif result.status != "timed_out":
                if proc.returncode == 0 and not state.reported_error:
                    result.status = "succeeded"
                else:
                    text = "\n".join(stderr_tail) + "\n" + (state.reported_error or "")
                    code = classify_failure(text)
                    if proc.returncode is not None and proc.returncode < 0:
                        code = "AGENT_KILLED" if code == "AGENT_FAILED" else code
                    msg = (state.reported_error or (stderr_tail[-1] if stderr_tail else f"exited with status {proc.returncode}"))
                    result.error = {"code": code, "message": redact(msg[:500])}
            self._finish(result)
        return result

    def _finish(self, result: JobResult) -> JobResult:
        self.db.execute(
            "UPDATE agent_jobs SET status=?, exit_code=?, error=?, session_id=?, finished_at=? WHERE id=?",
            (result.status, result.exit_code, dumps(result.error), result.session_id, time.time(), result.job_id),
        )
        return result

    def _kill_soon(self, proc: asyncio.subprocess.Process) -> None:
        """Non-blocking SIGTERM to the job's group, with SIGKILL escalation."""
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return

        async def escalate():
            await asyncio.sleep(TERM_GRACE_S)
            if proc.returncode is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        asyncio.ensure_future(escalate())

    async def _terminate(self, proc: asyncio.subprocess.Process) -> None:
        """SIGTERM the job's process group, then SIGKILL after a grace period."""
        if proc.returncode is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), TERM_GRACE_S)
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()

    @staticmethod
    def _reap_group(pgid: int) -> None:
        # Leftover children in the job's own group (e.g. helper servers).
        # The group id stays reserved while any member lives, so this cannot
        # hit an unrelated process.
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    async def cancel(self, job_id: str) -> bool:
        handle = self.active.get(job_id)
        if not handle:
            return False
        handle.cancel_requested = True
        if handle.proc is not None:
            await self._terminate(handle.proc)
        return True


async def _lines(stream: asyncio.StreamReader, total_cap: int):
    """Yield decoded lines; oversize lines are dropped; yields None once on flood, then drains."""
    buf = bytearray()
    total = 0
    discarding = False
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            if buf and not discarding:
                yield buf.decode(errors="replace")
            return
        total += len(chunk)
        if total > total_cap:
            yield None
            while await stream.read(65536):
                pass  # discard the rest until EOF
            return
        buf += chunk
        while True:
            nl = buf.find(b"\n")
            if nl < 0:
                if len(buf) > MAX_LINE_BYTES:
                    buf.clear()
                    discarding = True
                break
            line = bytes(buf[:nl])
            del buf[: nl + 1]
            if discarding:
                discarding = False
                continue
            yield line.decode(errors="replace").rstrip("\r")
