"""Durability: leases, idempotent steps, retries, pause/resume/cancel, budgets, reconciliation."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from openkg.agent.fixture import FixtureEngine
from openkg.db import session_scope, utcnow
from openkg.domain import runs as run_cmd
from openkg.domain.events import events_after
from openkg.models import (
    Claim,
    ClaimEvidence,
    Entity,
    Graph,
    ProviderUsage,
    Relationship,
    Run,
    RunEvent,
    RunStep,
    Source,
    Suggestion,
)
from openkg.worker import executor
from openkg.worker.context import TransientError
from openkg.worker.executor import claim, due_runs, execute_run, settle_control_requests


def start(gid, **kw) -> str:
    with session_scope() as s:
        return run_cmd.create_run(s, s.get(Graph, gid), engine="fixture", step_delay=0, **kw).id


def counts(gid) -> dict:
    with session_scope() as s:
        return {m.__tablename__: s.scalar(select(func.count()).select_from(m).where(m.graph_id == gid))
                for m in (Entity, Relationship, Claim, ClaimEvidence, Source, Suggestion, ProviderUsage)}


def status(rid) -> str:
    with session_scope() as s:
        return s.get(Run, rid).status


def test_fixture_run_produces_typed_sourced_suggestions_and_ordered_events(make_graph):
    gid = make_graph()
    rid = start(gid)
    assert execute_run(rid) == "done"
    assert status(rid) == "awaiting_review"
    with session_scope() as s:
        seqs = [e.seq for e in events_after(s, rid, 0, limit=1000)]
        assert seqs == list(range(1, len(seqs) + 1))  # gapless, strictly ordered
        sugs = list(s.scalars(select(Suggestion).where(Suggestion.run_id == rid)))
        assert {x.kind for x in sugs} >= {"entity", "relationship", "contradiction"}
        assert all(x.status == "pending" for x in sugs)  # review-first: nothing auto-committed
        for sug in sugs:  # every suggestion traces to evidence with a verified excerpt
            if sug.kind == "merge":
                continue
            n = s.scalar(select(func.count()).where(ClaimEvidence.claim_id.in_(sug.claim_ids), ClaimEvidence.excerpt_verified.is_(True)))
            assert n >= 1, sug.title
        assert all(u.simulated for u in s.scalars(select(ProviderUsage)))  # never reported as real spend
        assert all(src.is_synthetic and src.url.startswith("fixture://") for src in s.scalars(select(Source)))


def test_duplicate_delivery_and_rerun_do_not_duplicate_facts(make_graph):
    gid = make_graph()
    rid = start(gid)
    execute_run(rid)
    before = counts(gid)
    assert execute_run(rid) == "skipped"  # finished run: repeated queue delivery is a no-op
    with session_scope() as s:  # simulate a redelivery that sneaks past status (e.g. operator requeue)
        run = s.get(Run, rid)
        run.status, run.cursor = "researching", {"step": 0}
    assert execute_run(rid) == "done"
    assert counts(gid) == before


def test_lease_prevents_concurrent_execution_and_expires(make_graph):
    rid = start(make_graph())
    assert claim(rid, "worker-a") is True
    assert claim(rid, "worker-b") is False
    assert execute_run(rid, "worker-b") == "skipped"
    with session_scope() as s:
        s.get(Run, rid).lease_expires_at = utcnow() - timedelta(seconds=1)  # worker-a died
    assert execute_run(rid, "worker-b") == "done"


class CrashAt(FixtureEngine):
    """Fails inside a step, after some of its effects were written in the open transaction."""

    def __init__(self, key: str, exc: Exception, times: int = 1):
        self.key, self.exc, self.left = key, exc, times

    def apply(self, ctx, s, run, graph, scenario, step):
        super().apply(ctx, s, run, graph, scenario, step)
        if step.key == self.key and self.left > 0:
            self.left -= 1
            raise self.exc


def test_worker_crash_mid_step_resumes_without_duplicates_or_gaps(make_graph):
    reference = make_graph()
    execute_run(start(reference))
    gid = make_graph()
    rid = start(gid)
    assert execute_run(rid, engine=CrashAt("c0s3:ent0", RuntimeError("worker killed"))) == "failed"
    with session_scope() as s:
        run = s.get(Run, rid)
        partial = s.scalar(select(func.count()).where(Entity.graph_id == gid))
        assert 0 < partial < 23 and run.cursor["step"] > 0
        assert not s.scalar(select(RunStep.id).where(RunStep.run_id == rid, RunStep.key == "c0s3:ent0"))  # rolled back
        run.status, run.error, run.finished_at = "resuming", "", None  # what the reconciler does for a dead worker
    assert execute_run(rid) == "done"
    assert counts(gid) == counts(reference)  # identical to an uninterrupted run
    with session_scope() as s:
        seqs = [e.seq for e in events_after(s, rid, 0, limit=1000)]
        assert seqs == list(range(1, len(seqs) + 1))
        assert s.scalar(select(func.count()).where(RunEvent.run_id == rid, RunEvent.type == "run.resumed")) == 1


def test_transient_errors_back_off_then_succeed(make_graph, monkeypatch):
    rid = start(make_graph())
    engine = CrashAt("c0s1:search", TransientError("HTTP 429"), times=2)
    assert execute_run(rid, engine=engine) == "retry"
    with session_scope() as s:
        run = s.get(Run, rid)
        assert run.status == "resuming" and run.attempt == 1 and run.not_before > utcnow()
    assert rid not in due_runs(redispatch_after=0)  # backoff is honoured by the reconciler
    with session_scope() as s:
        s.get(Run, rid).not_before = utcnow()
    assert execute_run(rid, engine=engine) == "retry"
    with session_scope() as s:
        s.get(Run, rid).not_before = None
    assert execute_run(rid, engine=engine) == "done"
    with session_scope() as s:
        assert s.scalar(select(func.count()).where(RunEvent.run_id == rid, RunEvent.type == "run.retry")) == 2
        assert s.scalar(select(func.count()).where(ProviderUsage.run_id == rid, ProviderUsage.operation == "search")) == 6  # no double charge


def test_retries_are_bounded(make_graph):
    rid = start(make_graph())
    engine = CrashAt("c0s1:search", TransientError("HTTP 503"), times=99)
    outcomes = []
    for _ in range(6):
        with session_scope() as s:
            s.get(Run, rid).not_before = None
        outcomes.append(execute_run(rid, engine=engine))
    assert outcomes[:4] == ["retry", "retry", "retry", "failed"] and status(rid) == "failed"
    with session_scope() as s:
        assert "Gave up" in s.get(Run, rid).error


class StopAfter(FixtureEngine):
    """Issues a control request from 'the API' while the run is mid-flight."""

    def __init__(self, key: str, action):
        self.key, self.action = key, action

    def apply(self, ctx, s, run, graph, scenario, step):
        super().apply(ctx, s, run, graph, scenario, step)
        if step.key == self.key:
            self.action(s, run)


def test_pause_then_resume_continues_from_the_next_step(make_graph):
    gid = make_graph()
    rid = start(gid)
    assert execute_run(rid, engine=StopAfter("c0s2:read", run_cmd.pause)) == "paused"
    with session_scope() as s:
        run = s.get(Run, rid)
        assert run.status == "paused" and run.lease_owner is None
        paused_at = run.cursor["step"]
    assert execute_run(rid) == "skipped"  # a paused run is not runnable
    assert rid not in due_runs(redispatch_after=0)
    with session_scope() as s:
        run_cmd.resume(s, s.get(Run, rid))
    assert execute_run(rid) == "done"
    with session_scope() as s:
        steps = s.scalar(select(func.count()).where(RunStep.run_id == rid))
    assert steps > paused_at and status(rid) == "awaiting_review"
    reference = make_graph()
    execute_run(start(reference))
    assert counts(gid) == counts(reference)


def test_cancel_stops_before_the_next_expensive_step(make_graph):
    gid = make_graph()
    rid = start(gid)
    assert execute_run(rid, engine=StopAfter("c0s1:read", run_cmd.cancel)) == "cancelled"
    with session_scope() as s:
        run = s.get(Run, rid)
        assert run.status == "cancelled" and run.finished_at is not None
        assert run.tavily_calls == 2  # the search + read that had started; nothing after the request
        assert s.scalar(select(func.count()).where(Entity.graph_id == gid)) == 0
        with pytest.raises(run_cmd.RunError):
            run_cmd.resume(s, run)
    assert execute_run(rid) == "skipped"


def test_control_requests_on_unleased_runs_are_settled(make_graph):
    rid = start(make_graph())
    with session_scope() as s:
        run_cmd.pause(s, s.get(Run, rid))
    assert status(rid) == "pausing"
    assert settle_control_requests() == 1 and status(rid) == "paused"
    with session_scope() as s:
        run_cmd.cancel(s, s.get(Run, rid))
    assert status(rid) == "cancelled"


def test_call_cap_blocks_new_paid_steps_and_budget_can_be_extended(make_graph):
    gid = make_graph()
    rid = start(gid, budget={"max_tavily_calls": 3})
    assert execute_run(rid) == "budget_exhausted"
    with session_scope() as s:
        run = s.get(Run, rid)
        assert run.status == "budget_exhausted" and run.tavily_calls == 3  # never exceeds the cap
        with pytest.raises(run_cmd.RunError):
            run_cmd.resume(s, run)  # needs an explicit new budget
        run_cmd.resume(s, run, extend={"max_tavily_calls": 100})
    assert execute_run(rid) == "done"
    assert status(rid) == "awaiting_review"


def test_cost_cap_and_deadline(make_graph):
    rid = start(make_graph(), budget={"max_usd": 0.02})
    assert execute_run(rid) == "budget_exhausted"
    with session_scope() as s:
        ev = s.scalar(select(RunEvent).where(RunEvent.run_id == rid, RunEvent.type == "run.status").order_by(RunEvent.seq.desc()))
        assert "Cost cap" in ev.payload["reason"]
    rid2 = start(make_graph(), budget={"deadline_minutes": 1})
    with session_scope() as s:
        s.get(Run, rid2).started_at = utcnow() - timedelta(minutes=5)
    assert execute_run(rid2) == "budget_exhausted"


def test_reconciler_finds_unpublished_and_orphaned_runs(make_graph):
    rid = start(make_graph())  # committed but its task message was never published
    assert rid in due_runs()
    executor.mark_dispatched(rid)
    assert rid not in due_runs(redispatch_after=20)  # freshly dispatched: give the worker time
    assert rid in due_runs(redispatch_after=0)  # message lost: re-dispatch
    assert claim(rid, "dead-worker")
    assert rid not in due_runs(redispatch_after=0)  # held by a live lease
    with session_scope() as s:
        s.get(Run, rid).lease_expires_at = utcnow() - timedelta(seconds=1)
    assert rid in due_runs(redispatch_after=0)  # lease expired: worker presumed dead


def test_one_active_run_per_graph_and_chapters_advance(make_graph):
    gid = make_graph()
    rid = start(gid)
    with session_scope() as s, pytest.raises(run_cmd.RunError):
        run_cmd.create_run(s, s.get(Graph, gid), engine="fixture")
    execute_run(rid)
    first = counts(gid)
    rid2 = start(gid)
    execute_run(rid2)
    second = counts(gid)
    assert second["entities"] > first["entities"]  # chapter 2 adds to, never replaces, the graph
    rid3 = start(gid)
    execute_run(rid3)
    assert counts(gid)["entities"] == second["entities"] and status(rid3) == "completed"  # saturation


def test_live_engine_requires_keys_and_explicit_spend_confirmation(make_graph):
    gid = make_graph(None)
    with session_scope() as s:
        with pytest.raises(run_cmd.RunError, match="TAVILY_API_KEY"):
            run_cmd.create_run(s, s.get(Graph, gid), engine="deep_agent", confirm_spend=True)


def test_a_failed_run_can_be_retried_from_where_it_stopped(make_graph):
    gid = make_graph()
    rid = start(gid)
    assert execute_run(rid, engine=CrashAt("c0s4:read", RuntimeError("boom"))) == "failed"
    with session_scope() as s:
        run_cmd.resume(s, s.get(Run, rid))
    assert execute_run(rid) == "done" and status(rid) == "awaiting_review"
    reference = make_graph()
    execute_run(start(reference))
    assert counts(gid) == counts(reference)


def test_runs_that_stop_early_still_explain_what_they_did(make_graph):
    rid = start(make_graph(), budget={"max_tavily_calls": 5})
    execute_run(rid)
    with session_scope() as s:
        summary = s.get(Run, rid).summary
    assert summary.startswith("Stopped: web call cap of 5 reached.") and "Found" in summary and "entities" in summary
