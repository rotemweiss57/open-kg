"""Run lifecycle commands issued by the API. Workers observe these via the run row."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import new_id, utcnow
from ..models import FrontierQuestion, Graph, Run
from ..tavily_oauth import live_research_available
from .events import ACTIVE, audit, emit


class RunError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def default_budget() -> dict:
    st = get_settings()
    return {"max_usd": st.default_run_budget_usd, "max_tavily_calls": st.default_max_tavily_calls,
            "max_model_calls": st.default_max_model_calls, "deadline_minutes": st.default_run_deadline_minutes}


def create_run(s: Session, graph: Graph, *, engine: str = "fixture", objective: str = "", scenario: str | None = None,
               focus: str | None = None, frontier_id: str | None = None, budget: dict | None = None,
               user_id: str | None = None, confirm_spend: bool = False, step_delay: float | None = None) -> Run:
    st = get_settings()
    active = s.scalar(select(Run.id).where(Run.graph_id == graph.id, Run.kind == "research",
                                           Run.status.in_((*ACTIVE, "paused"))))
    if active:
        raise RunError("This graph already has a run in progress. Pause or cancel it first.")
    budget = {**default_budget(), **(budget or {})}
    config: dict = {}
    if engine == "deep_agent":
        if not live_research_available(s):
            raise RunError("Live research needs Tavily (Sign in with Tavily in Settings, or set TAVILY_API_KEY) "
                           "and a model provider key on the server.", 422)
        if not confirm_spend:
            raise RunError("Live research spends real API credits. Confirm the budget to start.", 422)
        if budget["max_usd"] > st.max_run_budget_usd:
            raise RunError(f"Budget exceeds the server limit of ${st.max_run_budget_usd:.2f}.", 422)
        if graph.is_synthetic:
            raise RunError("Synthetic demo graphs cannot be mixed with live web research.", 422)
    else:
        scenario = scenario or graph.scenario
        if not scenario:
            raise RunError("Choose a synthetic scenario to simulate, or configure keys for live research.", 422)
        from ..agent.fixture import get_scenario
        try:
            get_scenario(scenario)
        except ValueError as e:
            raise RunError(str(e), 422) from e
        if not graph.is_synthetic:
            if s.scalar(select(func.count()).where(Run.graph_id == graph.id, Run.engine == "deep_agent")):
                raise RunError("This graph contains live research; simulated evidence cannot be added to it.", 422)
            graph.is_synthetic, graph.scenario = True, scenario  # labelled synthetic from now on
        done = s.scalar(select(func.count()).where(
            Run.graph_id == graph.id, Run.engine == "fixture", Run.kind == "research",
            Run.status.in_(("completed", "awaiting_review")))) or 0
        config = {"scenario": scenario, "chapter": done}
        if step_delay is not None:
            config["step_delay"] = step_delay
    if frontier_id:
        fq = s.get(FrontierQuestion, frontier_id)
        if fq is None or fq.graph_id != graph.id:
            raise RunError("Unknown follow-up question.", 404)
        fq.status = "launched"
        focus = focus or fq.question
    if focus:
        config["focus"] = focus
    run = Run(id=new_id(), graph_id=graph.id, engine=engine, kind="research", status="queued",
              objective=objective or focus or graph.charter.get("question") or graph.name,
              config=config, budget=budget, created_by=user_id)
    run.thread_id = f"run-{run.id}"
    s.add(run)
    s.flush()
    emit(s, run, "run.status", {"status": "queued", "engine": engine, "budget": budget,
                                "simulated": engine == "fixture"})
    audit(s, graph_id=graph.id, workspace_id=graph.workspace_id, actor_user_id=user_id, action="run.created",
          target=f"run:{run.id}", detail={"engine": engine, "budget": budget})
    return run


def pause(s: Session, run: Run) -> Run:
    if run.status in ("queued", "planning", "researching", "verifying", "resuming"):
        run.requested_action, run.status = "pause", "pausing"
        emit(s, run, "run.status", {"status": "pausing", "note": "Stops after the step in progress."})
    elif run.status not in ("pausing", "paused"):
        raise RunError(f"A {run.status} run cannot be paused.")
    return run


def resume(s: Session, run: Run, extend: dict | None = None) -> Run:
    if run.status == "budget_exhausted":
        if not extend:
            raise RunError("Raise the budget to continue this run.", 422)
        run.budget = {**run.budget, **extend}
        run.finished_at = None
        run.summary = ""  # the stop summary no longer describes the run
        if run.started_at:  # deadline restarts from now
            run.started_at = utcnow()
    elif run.status == "failed":
        # Operator retry: continue from the last committed step / agent checkpoint.
        run.finished_at, run.error, run.attempt = None, "", 0
    elif run.status not in ("paused", "pausing"):
        raise RunError(f"A {run.status} run cannot be resumed.")
    run.requested_action, run.status, run.dispatched_at, run.not_before = "", "resuming", None, None
    emit(s, run, "run.status", {"status": "resuming", **({"budget": run.budget} if extend else {})})
    return run


def cancel(s: Session, run: Run) -> Run:
    if run.status in ("completed", "awaiting_review", "cancelled", "failed"):
        raise RunError(f"A {run.status} run cannot be cancelled.")
    if run.status in ("paused", "budget_exhausted") or run.lease_owner is None:
        run.status, run.requested_action, run.finished_at = "cancelled", "", utcnow()
        emit(s, run, "run.status", {"status": "cancelled"})
    else:
        run.requested_action, run.status = "cancel", "cancel_requested"
        emit(s, run, "run.status", {"status": "cancel_requested",
                                    "note": "No new steps will start. A provider call already in flight may finish."})
    return run
