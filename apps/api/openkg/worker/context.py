"""Per-run execution context: the narrow, instrumented surface engines use to touch the world."""
from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import TAVILY_USD_PER_CREDIT, get_settings
from ..db import session_scope, utcnow
from ..domain.events import emit
from ..models import Graph, ProviderUsage, Run, RunStep


class ControlSignal(Exception):
    """Base for cooperative stop conditions; never treated as failures."""


class PauseRequested(ControlSignal):
    pass


class CancelRequested(ControlSignal):
    pass


class BudgetExhausted(ControlSignal):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class LeaseLost(ControlSignal):
    pass


class TransientError(Exception):
    """Retryable infrastructure/provider failure (429, 5xx, timeouts)."""


class RunContext:
    def __init__(self, run_id: str, graph_id: str, worker_id: str):
        self.run_id, self.graph_id, self.worker_id = run_id, graph_id, worker_id
        self.settings = get_settings()

    @contextmanager
    def tx(self) -> Iterator[tuple[Session, Run, Graph]]:
        with session_scope() as s:
            run = s.get(Run, self.run_id)
            graph = s.get(Graph, self.graph_id)
            if run is None or graph is None:
                raise CancelRequested("run or graph was deleted")
            yield s, run, graph

    def emit(self, type: str, payload: dict | None = None) -> None:
        with self.tx() as (s, run, _):
            emit(s, run, type, payload)

    def set_status(self, status: str, focus: str | None = None) -> None:
        with self.tx() as (s, run, _):
            if run.status != status and run.status in ("queued", "planning", "researching", "verifying", "resuming"):
                run.status = status
                emit(s, run, "run.status", {"status": status})
            if focus is not None:
                run.current_focus = focus

    # --- control & budget: called before every expensive operation --------------------------

    def checkpoint(self) -> None:
        """Raise if the user asked to stop, the lease moved, or a cap is reached."""
        with self.tx() as (s, run, _):
            if run.lease_owner != self.worker_id:
                raise LeaseLost(f"lease now held by {run.lease_owner}")
            if run.requested_action == "cancel":
                raise CancelRequested()
            if run.requested_action == "pause":
                raise PauseRequested()
            reason = budget_block(run)
            if reason:
                raise BudgetExhausted(reason)

    def control_check(self) -> None:
        """Cheap pause/cancel/lease check, safe to call frequently (e.g. while a reply streams)."""
        with self.tx() as (_, run, __):
            if run.lease_owner != self.worker_id:
                raise LeaseLost(f"lease now held by {run.lease_owner}")
            if run.requested_action == "cancel":
                raise CancelRequested()
            if run.requested_action == "pause":
                raise PauseRequested()

    def remaining(self) -> dict:
        with self.tx() as (_, run, __):
            return remaining_budget(run)

    def sleep(self, seconds: float) -> None:
        """Interruptible wait so pause/cancel land within ~250ms even between slow steps."""
        deadline = time.monotonic() + seconds
        while (left := deadline - time.monotonic()) > 0:
            time.sleep(min(0.25, left))
            with self.tx() as (_, run, __):
                if run.requested_action == "cancel":
                    raise CancelRequested()
                if run.requested_action == "pause":
                    raise PauseRequested()

    # --- idempotent steps -------------------------------------------------------------------

    def step_done(self, s: Session, key: str) -> bool:
        return s.scalar(select(RunStep.id).where(RunStep.run_id == self.run_id, RunStep.key == key)) is not None

    def mark_step(self, s: Session, key: str, kind: str = "", result: dict | None = None) -> bool:
        """Record completion inside the caller's transaction. False => another delivery already did it."""
        try:
            with s.begin_nested():
                s.add(RunStep(run_id=self.run_id, key=key, kind=kind, result=result or {}))
                s.flush()
            return True
        except IntegrityError:
            return False

    # --- cost ledger ------------------------------------------------------------------------

    def charge_tavily(self, s: Session, run: Run, operation: str, credits: float, *, simulated: bool = False,
                      units: dict | None = None) -> None:
        cost = round(credits * TAVILY_USD_PER_CREDIT, 5)
        s.execute(update(Run).where(Run.id == run.id).values(
            tavily_calls=Run.tavily_calls + 1, tavily_credits=Run.tavily_credits + credits,
            est_cost_usd=Run.est_cost_usd + cost))
        s.add(ProviderUsage(graph_id=run.graph_id, run_id=run.id, provider="tavily", operation=operation,
                            units={"credits": credits, **(units or {})}, est_cost_usd=cost, simulated=simulated))

    def charge_model(self, s: Session, run: Run, provider: str, model: str, role: str, input_tokens: int,
                     output_tokens: int, *, simulated: bool = False) -> None:
        from ..agent.pricing import estimate
        cost, source = estimate(provider, model, input_tokens, output_tokens)
        s.execute(update(Run).where(Run.id == run.id).values(
            model_calls=Run.model_calls + 1, input_tokens=Run.input_tokens + input_tokens,
            output_tokens=Run.output_tokens + output_tokens, est_cost_usd=Run.est_cost_usd + cost))
        s.add(ProviderUsage(graph_id=run.graph_id, run_id=run.id, provider=provider, operation=f"chat:{role}",
                            units={"input_tokens": input_tokens, "output_tokens": output_tokens, "model": model,
                                   "rate_source": source},
                            est_cost_usd=cost, simulated=simulated))


def remaining_budget(run: Run) -> dict:
    b = run.budget or {}
    started = run.started_at or utcnow()
    deadline = started + timedelta(minutes=b.get("deadline_minutes", 30))
    return {
        "usd": round(max(0.0, b.get("max_usd", 0) - run.est_cost_usd), 4),
        "tavily_calls": max(0, b.get("max_tavily_calls", 0) - run.tavily_calls),
        "model_calls": max(0, b.get("max_model_calls", 0) - run.model_calls),
        "seconds": max(0, int((deadline - utcnow()).total_seconds())),
    }


def budget_block(run: Run, *, need_tavily: int = 0) -> str:
    """Reason a new expensive step may not start, or ''."""
    r = remaining_budget(run)
    b = run.budget or {}
    if b.get("max_usd") is not None and r["usd"] <= 0:
        return f"Cost cap of ${b['max_usd']:.2f} reached"
    # Only a step that needs web calls is blocked by the web cap. Parallel tool calls can overshoot the
    # cap by one; that must not turn into a hard stop for the model calls that write the summary.
    if need_tavily and r["tavily_calls"] < need_tavily:
        return f"Web call cap of {b.get('max_tavily_calls')} reached"
    if b.get("max_model_calls") is not None and run.model_calls >= b["max_model_calls"] > 0:
        return f"Model call cap of {b['max_model_calls']} reached"
    if r["seconds"] <= 0:
        return f"Deadline of {b.get('deadline_minutes')} minutes reached"
    return ""
