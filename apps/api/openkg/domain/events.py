from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import AuditEvent, Run, RunEvent

TERMINAL = {"completed", "awaiting_review", "cancelled", "failed", "budget_exhausted"}
ACTIVE = {"queued", "planning", "researching", "verifying", "resuming", "pausing", "cancel_requested"}


def emit(s: Session, run: Run, type: str, payload: dict | None = None) -> RunEvent:
    """Append a durable, strictly ordered event. The seq is allocated atomically on the run row,
    so concurrent writers (worker + API control actions) can never collide or reorder."""
    seq = s.execute(
        update(Run).where(Run.id == run.id).values(last_event_seq=Run.last_event_seq + 1)
        .returning(Run.last_event_seq)
    ).scalar_one()
    ev = RunEvent(run_id=run.id, graph_id=run.graph_id, seq=seq, type=type, payload=payload or {})
    s.add(ev)
    s.flush()
    return ev


def events_after(s: Session, run_id: str, after: int, limit: int = 500) -> list[RunEvent]:
    return list(s.scalars(
        select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.seq > after)
        .order_by(RunEvent.seq).limit(limit)
    ))


def audit(s: Session, *, graph_id: str | None, action: str, target: str = "", detail: dict | None = None,
          actor_user_id: str | None = None, actor_kind: str = "user", workspace_id: str | None = None) -> None:
    s.add(AuditEvent(graph_id=graph_id, workspace_id=workspace_id, action=action, target=target,
                     detail=detail or {}, actor_user_id=actor_user_id, actor_kind=actor_kind))
