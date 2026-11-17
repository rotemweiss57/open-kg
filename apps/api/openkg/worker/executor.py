"""Durable run execution.

Postgres (the `runs` row) is the source of truth; queue messages are only hints. A worker
must win a time-boxed lease before touching a run, so duplicate deliveries and restarted
workers cannot execute the same run concurrently. A dead worker simply stops heartbeating
and the reconciler re-dispatches the run, which resumes from its last committed step.
"""
from __future__ import annotations

import logging
import os
import random
import socket
import threading
import uuid
from datetime import timedelta

from sqlalchemy import func, or_, select, update

from ..config import get_settings
from ..db import session_scope, utcnow
from ..domain.events import emit
from ..models import Run, Suggestion
from .context import (
    BudgetExhausted,
    CancelRequested,
    LeaseLost,
    PauseRequested,
    RunContext,
    TransientError,
)

log = logging.getLogger("openkg.worker")
RUNNABLE = ("queued", "planning", "researching", "verifying", "resuming")


def worker_identity() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def claim(run_id: str, worker_id: str) -> bool:
    st = get_settings()
    now = utcnow()
    with session_scope() as s:
        won = s.execute(
            update(Run).where(
                Run.id == run_id, Run.status.in_(RUNNABLE),
                or_(Run.lease_owner.is_(None), Run.lease_expires_at < now),
                or_(Run.not_before.is_(None), Run.not_before <= now))
            .values(lease_owner=worker_id, lease_expires_at=now + timedelta(seconds=st.lease_seconds))
        ).rowcount
        return bool(won)


def _release(run_id: str, worker_id: str, **values) -> None:
    with session_scope() as s:
        s.execute(update(Run).where(Run.id == run_id, Run.lease_owner == worker_id)
                  .values(lease_owner=None, lease_expires_at=None, **values))


class _Heartbeat(threading.Thread):
    """Keeps the lease alive during long model/provider calls. Dies with the process."""

    def __init__(self, run_id: str, worker_id: str):
        super().__init__(daemon=True)
        self.run_id, self.worker_id, self.stop_event = run_id, worker_id, threading.Event()

    def run(self) -> None:
        st = get_settings()
        while not self.stop_event.wait(st.lease_seconds / 3):
            try:
                with session_scope() as s:
                    s.execute(update(Run).where(Run.id == self.run_id, Run.lease_owner == self.worker_id)
                              .values(lease_expires_at=utcnow() + timedelta(seconds=st.lease_seconds)))
            except Exception:  # pragma: no cover - best effort
                log.exception("heartbeat failed", extra={"run_id": self.run_id})


def get_engine(name: str):
    if name == "fixture":
        from ..agent.fixture import FixtureEngine
        return FixtureEngine()
    if name == "deep_agent":
        from ..agent.deep import DeepAgentEngine
        return DeepAgentEngine()
    raise ValueError(f"unknown engine {name}")


def factual_summary(s, run: Run, status: str, reason: str = "") -> str:
    """A plain account of what a run did, for runs that end without the agent's own dossier."""
    from ..models import RunEvent
    kinds = dict(s.execute(select(Suggestion.kind, func.count()).where(Suggestion.run_id == run.id)
                           .group_by(Suggestion.kind)).all())
    searches = s.scalar(select(func.count()).where(RunEvent.run_id == run.id, RunEvent.type == "search.performed")) or 0
    reads = s.scalar(select(func.count()).where(RunEvent.run_id == run.id, RunEvent.type == "source.read")) or 0
    how = {"budget_exhausted": f"Stopped: {(reason or 'limit reached')[0].lower()}{(reason or 'limit reached')[1:]}", "cancelled": "Cancelled",
           "failed": "Stopped after an error"}.get(status, "Finished")
    names = {"entity": ("entity", "entities"), "relationship": ("link", "links"), "claim": ("fact", "facts"),
             "contradiction": ("conflict", "conflicts"), "merge": ("possible duplicate", "possible duplicates")}
    found = ", ".join(f"{n} {names.get(k, (k, k + 's'))[0 if n == 1 else 1]}"
                      for k, n in sorted(kinds.items(), key=lambda kv: -kv[1])) or "nothing"
    return (f"{how}. {searches} search{'es' if searches != 1 else ''}, {reads} page read{'s' if reads != 1 else ''}. "
            f"Found {found}.")


def _finish(ctx: RunContext, status: str, *, error: str = "", extra: dict | None = None) -> None:
    with ctx.tx() as (s, run, _):
        if not run.summary and status in ("budget_exhausted", "cancelled", "failed"):
            run.summary = factual_summary(s, run, status, (extra or {}).get("reason", ""))
        run.status, run.finished_at, run.requested_action = status, utcnow(), ""
        run.error = error
        run.current_focus = ""
        emit(s, run, "run.status", {"status": status, **(extra or {})})


def execute_run(run_id: str, worker_id: str | None = None, engine=None) -> str:
    """Run (or resume) a run until it finishes, pauses, or must be retried. Safe to call
    any number of times for the same run."""
    worker_id = worker_id or worker_identity()
    if not claim(run_id, worker_id):
        return "skipped"
    st = get_settings()
    with session_scope() as s:
        run = s.get(Run, run_id)
        ctx = RunContext(run_id, run.graph_id, worker_id)
        engine_name = run.engine
        if run.requested_action == "cancel":
            pre = "cancel"
        elif run.requested_action == "pause":
            pre = "pause"
        else:
            pre = ""
            if run.started_at is None:
                run.started_at = utcnow()
            elif run.status != "queued":
                emit(s, run, "run.resumed", {"attempt": run.attempt, "worker": worker_id.split(":")[0],
                                             "note": "Resumed from the last committed step."})
    hb = _Heartbeat(run_id, worker_id)
    hb.start()
    outcome = "done"
    try:
        if pre == "cancel":
            raise CancelRequested()
        if pre == "pause":
            raise PauseRequested()
        (engine or get_engine(engine_name)).run(ctx)
        with ctx.tx() as (s, run, _):
            pending = s.scalar(select(func.count()).where(
                Suggestion.run_id == run_id, Suggestion.status == "pending")) or 0
        _finish(ctx, "awaiting_review" if pending else "completed", extra={"pending_suggestions": pending})
    except PauseRequested:
        with ctx.tx() as (s, run, _):
            run.status, run.requested_action = "paused", ""
            emit(s, run, "run.status", {"status": "paused"})
        outcome = "paused"
    except CancelRequested:
        _finish(ctx, "cancelled")
        outcome = "cancelled"
    except BudgetExhausted as e:
        _finish(ctx, "budget_exhausted", extra={"reason": e.reason})
        outcome = "budget_exhausted"
    except LeaseLost:
        log.warning("lease lost; another worker owns the run", extra={"run_id": run_id})
        outcome = "lease_lost"
    except TransientError as e:
        with ctx.tx() as (s, run, _):
            run.attempt += 1
            if run.attempt >= st.max_run_attempts:
                run.status, run.finished_at, run.error = "failed", utcnow(), f"Gave up after {run.attempt} attempts: {e}"
                emit(s, run, "run.status", {"status": "failed", "error": run.error})
                outcome = "failed"
            else:
                delay = min(60.0, 2 ** run.attempt) * (0.5 + random.random() / 2)  # backoff with jitter
                run.not_before = utcnow() + timedelta(seconds=delay)
                run.status = "resuming"
                emit(s, run, "run.retry", {"attempt": run.attempt, "error": str(e)[:300],
                                           "retry_in_seconds": round(delay, 1)})
                outcome = "retry"
    except Exception as e:  # noqa: BLE001 - last-resort boundary; the run must not vanish silently
        log.exception("run failed", extra={"run_id": run_id})
        try:
            _finish(ctx, "failed", error=f"{type(e).__name__}: {e}"[:1000])
        except Exception:  # pragma: no cover
            log.exception("could not persist failure")
        outcome = "failed"
    finally:
        hb.stop_event.set()
        _release(run_id, worker_id)
    return outcome


# --- reconciliation ("the runs table is the outbox") ------------------------------------------


def due_runs(limit: int = 20, redispatch_after: float = 20.0) -> list[str]:
    """Runs that should be executing but have no live lease: never-published runs, runs whose
    task message was lost, and runs whose worker died."""
    now = utcnow()
    with session_scope() as s:
        return list(s.scalars(
            select(Run.id).where(
                Run.status.in_(RUNNABLE),
                or_(Run.lease_owner.is_(None), Run.lease_expires_at < now),
                or_(Run.not_before.is_(None), Run.not_before <= now),
                or_(Run.dispatched_at.is_(None), Run.dispatched_at < now - timedelta(seconds=redispatch_after)))
            .order_by(Run.created_at).limit(limit)))


def mark_dispatched(run_id: str) -> None:
    with session_scope() as s:
        s.execute(update(Run).where(Run.id == run_id).values(dispatched_at=utcnow()))


def settle_control_requests() -> int:
    """Pause/cancel requests on runs that no worker holds are applied directly."""
    now = utcnow()
    n = 0
    with session_scope() as s:
        idle = s.scalars(select(Run).where(
            Run.status.in_(("pausing", "cancel_requested")),
            or_(Run.lease_owner.is_(None), Run.lease_expires_at < now)))
        for run in idle:
            run.status = "cancelled" if run.status == "cancel_requested" else "paused"
            run.requested_action = ""
            if run.status == "cancelled":
                run.finished_at = now
            emit(s, run, "run.status", {"status": run.status})
            n += 1
    return n
