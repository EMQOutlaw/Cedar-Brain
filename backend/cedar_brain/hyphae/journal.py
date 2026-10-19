"""Durable run journal: run/step rows, ordered per-run events, verification records."""

from __future__ import annotations

import time

from ..events import EventBus
from ..security.redaction import redact_obj
from ..storage.database import Database, dumps, loads
from .models import ACTIVE_RUN_STATUSES, TERMINAL_STEP_STATES, RunStatus, StepState, check_transition

MAX_RESULT_TEXT = 64 * 1024


class Journal:
    def __init__(self, db: Database, bus: EventBus):
        self.db = db
        self.bus = bus
        self._seq: dict[str, int] = {}

    # -- events --------------------------------------------------------------
    def emit(self, run_id: str, name: str, data: dict) -> int:
        if run_id not in self._seq:
            row = self.db.one("SELECT COALESCE(MAX(seq), 0) FROM run_events WHERE run_id=?", (run_id,))
            self._seq[run_id] = row[0]
        self._seq[run_id] += 1
        seq = self._seq[run_id]
        data = redact_obj(data)
        payload = {"run_id": run_id, "seq": seq, **data}
        self.db.execute("INSERT INTO run_events(run_id, seq, ts, event, data) VALUES (?,?,?,?,?)",
                        (run_id, seq, time.time(), name, dumps(payload)))
        self.bus.emit(name, payload)
        return seq

    def events(self, run_id: str, after_seq: int = 0, limit: int = 200) -> dict:
        rows = self.db.all("SELECT seq, ts, event, data FROM run_events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?",
                           (run_id, after_seq, limit + 1))
        more = len(rows) > limit
        events = [{"seq": r["seq"], "ts": r["ts"], "event": r["event"], "data": loads(r["data"])} for r in rows[:limit]]
        last = events[-1]["seq"] if events else after_seq
        return {"run_id": run_id, "events": events, "last_seq": last, "more": more}

    # -- runs ----------------------------------------------------------------
    def create_run(self, run_id: str, workflow: dict) -> None:
        ts = time.time()
        with self.db.transaction():
            self.db.execute("INSERT INTO runs(id, workflow_id, project_id, status, created_at) VALUES (?,?,?,?,?)",
                            (run_id, workflow["id"], workflow["project_id"], RunStatus.QUEUED, ts))
            for pos, step in enumerate(workflow["definition"]["steps"]):
                self.db.execute("INSERT INTO run_steps(run_id, step_id, position, state, executor) VALUES (?,?,?,?,?)",
                                (run_id, step["id"], pos, StepState.PENDING, step["executor"]))
        self.emit(run_id, "run.updated", {"status": RunStatus.QUEUED, "workflow_id": workflow["id"]})

    def set_run_status(self, run_id: str, status: str, error: dict | None = None) -> None:
        ts = time.time()
        if status == RunStatus.RUNNING:
            self.db.execute("UPDATE runs SET status=?, started_at=COALESCE(started_at, ?) WHERE id=?", (status, ts, run_id))
        elif status in ACTIVE_RUN_STATUSES:
            self.db.execute("UPDATE runs SET status=? WHERE id=?", (status, run_id))
        else:
            self.db.execute("UPDATE runs SET status=?, error=?, finished_at=? WHERE id=?", (status, dumps(error), ts, run_id))
        data = {"status": status}
        if error:
            data["error"] = error
        self.emit(run_id, "run.updated", data)

    def step_state(self, run_id: str, step_id: str) -> str:
        return self.db.one("SELECT state FROM run_steps WHERE run_id=? AND step_id=?", (run_id, step_id))["state"]

    def transition(self, run_id: str, step_id: str, new: str, *, error: dict | None = None,
                   result: dict | None = None, worktree: str | None = None, reason: str | None = None) -> None:
        old = self.step_state(run_id, step_id)
        check_transition(old, new)
        ts = time.time()
        sets, args = ["state=?"], [new]
        if new == StepState.RUNNING:
            sets.append("started_at=?"), args.append(ts)
        if new in TERMINAL_STEP_STATES:
            sets.append("finished_at=?"), args.append(ts)
        if error is not None:
            sets.append("error=?"), args.append(dumps(redact_obj(error)))
        if result is not None:
            sets.append("result=?"), args.append(dumps(redact_obj(result)))
        if worktree is not None:
            sets.append("worktree=?"), args.append(worktree)
        self.db.execute(f"UPDATE run_steps SET {', '.join(sets)} WHERE run_id=? AND step_id=?", (*args, run_id, step_id))
        data = {"step_id": step_id, "state": new, "previous": old}
        if error:
            data["error"] = error
        if reason:
            data["reason"] = reason
        self.emit(run_id, "run.step.updated", data)

    def record_verification(self, run_id: str, step_id: str, method: str, passed: bool, detail: dict) -> None:
        self.db.execute("INSERT INTO verifications(run_id, step_id, method, passed, detail, ts) VALUES (?,?,?,?,?,?)",
                        (run_id, step_id, method, int(passed), dumps(detail), time.time()))

    def get_run(self, run_id: str) -> dict | None:
        row = self.db.one("SELECT * FROM runs WHERE id=?", (run_id,))
        if not row:
            return None
        steps = []
        for s in self.db.all("SELECT * FROM run_steps WHERE run_id=? ORDER BY position", (run_id,)):
            ver = self.db.all("SELECT method, passed, detail, ts FROM verifications WHERE run_id=? AND step_id=? ORDER BY ts",
                              (run_id, s["step_id"]))
            steps.append({
                "step_id": s["step_id"], "state": s["state"], "executor": s["executor"],
                "started_at": s["started_at"], "finished_at": s["finished_at"],
                "result": loads(s["result"]), "error": loads(s["error"]), "worktree": s["worktree"],
                "verification": [{"method": v["method"], "passed": bool(v["passed"]), "detail": loads(v["detail"]), "ts": v["ts"]} for v in ver],
            })
        last = self.db.one("SELECT COALESCE(MAX(seq),0) FROM run_events WHERE run_id=?", (run_id,))[0]
        return {
            "id": row["id"], "workflow_id": row["workflow_id"], "project_id": row["project_id"],
            "status": row["status"], "error": loads(row["error"]), "created_at": row["created_at"],
            "started_at": row["started_at"], "finished_at": row["finished_at"], "steps": steps, "last_seq": last,
        }

    def list_runs(self, workflow_id: str | None, project_id: str | None, status: str | None, limit: int) -> list[dict]:
        where, args = [], []
        for col, val in (("workflow_id", workflow_id), ("project_id", project_id), ("status", status)):
            if val:
                where.append(f"{col}=?"), args.append(val)
        sql = "SELECT id, workflow_id, project_id, status, created_at, started_at, finished_at FROM runs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT ?"
        return [dict(r) for r in self.db.all(sql, (*args, limit))]

    # -- recovery ------------------------------------------------------------
    def recover(self) -> list[str]:
        """Mark runs that were active when the backend stopped as interrupted.

        Nothing is re-run: a step that was running may have mutated a
        worktree, so it is recorded as failed/INTERRUPTED for a human to review.
        """
        ids = [r["id"] for r in self.db.all(
            f"SELECT id FROM runs WHERE status IN ({','.join('?' * len(ACTIVE_RUN_STATUSES))})", tuple(ACTIVE_RUN_STATUSES))]
        for run_id in ids:
            for s in self.db.all("SELECT step_id, state FROM run_steps WHERE run_id=?", (run_id,)):
                if s["state"] == StepState.RUNNING:
                    self.transition(run_id, s["step_id"], StepState.FAILED,
                                    error={"code": "INTERRUPTED", "message": "backend stopped while this step was running; not re-run automatically"})
                elif s["state"] not in TERMINAL_STEP_STATES:
                    self.transition(run_id, s["step_id"], StepState.CANCELLED, reason="interrupted")
            self.set_run_status(run_id, RunStatus.INTERRUPTED,
                                {"code": "INTERRUPTED", "message": "backend stopped during this run"})
        return ids
