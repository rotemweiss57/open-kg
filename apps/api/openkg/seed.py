"""Seeds the clearly-labelled synthetic demo workspace by *running* the fixture pipeline.

Nothing is inserted as a finished graph: each demo graph is produced by executing a scripted
run through the real executor, recorder and review services, then reviewing part of the queue.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update

from .agent.fixture import Scenario, get_scenario
from .db import Base, get_engine, session_scope, utcnow
from .domain.monitors import run_monitor_check
from .domain.review import ReviewError, decide
from .domain.runs import create_run
from .models import (
    Claim,
    Entity,
    Graph,
    Membership,
    Monitor,
    Relationship,
    Run,
    RunEvent,
    Source,
    Suggestion,
    User,
)
from .security import create_user
from .worker.executor import execute_run

DEMO_EMAIL = "demo@openkg.local"


def init_db() -> None:
    Base.metadata.create_all(get_engine())


def graph_from_scenario(s, workspace_id: str, sc: Scenario, name: str | None = None) -> Graph:
    g = Graph(workspace_id=workspace_id, name=name or sc.name, description=sc.description, charter=sc.charter,
              ontology=sc.ontology, is_synthetic=True, scenario=sc.id, budget_usd=5.0)
    s.add(g)
    s.flush()
    return g


def ensure_demo_user() -> tuple[str, str]:
    with session_scope() as s:
        user = s.scalar(select(User).where(User.email == DEMO_EMAIL))
        if user is None:
            user, ws = create_user(s, DEMO_EMAIL, None, name="Demo analyst", is_demo=True)
            ws.name = "Demo workspace"
            return user.id, ws.id
        ws_id = s.scalar(select(Membership.workspace_id).where(Membership.user_id == user.id))
        return user.id, ws_id


def _backdate(graph_id: str, run_id: str, start_days: float, end_days: float) -> None:
    """Spread a seed run over a past window so history, the timeline and Time Machine have
    something truthful to show (observation order is preserved)."""
    now = utcnow()
    t0, t1 = now - timedelta(days=start_days), now - timedelta(days=end_days)
    with session_scope() as s:
        events = list(s.scalars(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)))
        n = max(1, len(events) - 1)
        span = (t1 - t0)

        def at(i: int):
            return t0 + span * (i / n)

        stamp: dict[str, object] = {}
        for i, ev in enumerate(events):
            ev.created_at = at(i)
            p = ev.payload or {}
            for key in ("entity", "relationship"):
                if isinstance(p.get(key), dict) and p[key].get("id"):
                    stamp.setdefault(p[key]["id"], at(i))
            for src in p.get("sources", []) if ev.type == "source.read" else []:
                stamp.setdefault(src["id"], at(i))
        for ent in s.scalars(select(Entity).where(Entity.graph_id == graph_id)):
            ent.first_seen_at = stamp.get(ent.id, t0)
            ent.last_verified_at = t1
        for rel in s.scalars(select(Relationship).where(Relationship.graph_id == graph_id)):
            rel.first_seen_at = stamp.get(rel.id, t1)
            rel.last_verified_at = t1
        ents = {e.id: e.first_seen_at for e in s.scalars(select(Entity).where(Entity.graph_id == graph_id))}
        rels = {r.id: r.first_seen_at for r in s.scalars(select(Relationship).where(Relationship.graph_id == graph_id))}
        for c in s.scalars(select(Claim).where(Claim.graph_id == graph_id)):
            c.observed_at = ents.get(c.entity_id) or rels.get(c.relationship_id) or t0
            c.verified_at = t1
            if c.accepted_at:
                c.accepted_at = t1 + timedelta(hours=6)
        for src in s.scalars(select(Source).where(Source.graph_id == graph_id)):
            src.fetched_at = stamp.get(src.id, t0)
        for sug in s.scalars(select(Suggestion).where(Suggestion.graph_id == graph_id)):
            sug.created_at = t1
            if sug.decided_at:
                sug.decided_at = t1 + timedelta(hours=6)
        run = s.get(Run, run_id)
        run.created_at, run.started_at, run.finished_at = t0, t0, t1
        s.execute(update(Graph).where(Graph.id == graph_id).values(created_at=t0 - timedelta(hours=1)))


def seed_graph(workspace_id: str, user_id: str, scenario_id: str) -> str:
    sc = get_scenario(scenario_id)
    with session_scope() as s:
        g = graph_from_scenario(s, workspace_id, sc)
        run = create_run(s, g, engine="fixture", user_id=user_id, step_delay=0)
        gid, rid = g.id, run.id
    execute_run(rid)

    with session_scope() as s:
        g = s.get(Graph, gid)
        keep, rejects = sc.seed.get("keep_pending", []), dict(sc.seed.get("reject", []))
        pending = list(s.scalars(select(Suggestion).where(Suggestion.graph_id == gid, Suggestion.status == "pending")
                                 .order_by(Suggestion.created_at, Suggestion.id)))
        for sug in pending:  # reject first so dependants cascade
            for name, note in rejects.items():
                if sug.kind == "entity" and name in sug.title:
                    decide(s, g, sug, "reject", actor_user_id=user_id, note=note)
        for sug in pending:
            s.refresh(sug)
            if sug.status != "pending" or sug.kind in ("merge", "contradiction") or sug.confidence == "low":
                continue
            if any(k in sug.title for k in keep):
                continue
            try:
                decide(s, g, sug, "approve", actor_user_id=user_id, note="Reviewed during demo seeding.")
            except ReviewError:
                pass
        run = s.get(Run, rid)
        left = s.scalar(select(Suggestion.id).where(Suggestion.run_id == rid, Suggestion.status == "pending"))
        run.status = "awaiting_review" if left else "completed"
    _backdate(gid, rid, start_days=23, end_days=4)

    checks: list[tuple[str, int]] = []
    with session_scope() as s:
        for name, question, preds, n in sc.seed.get("monitors", []):
            ent = s.scalar(select(Entity).where(Entity.graph_id == gid, Entity.name == name, Entity.status == "accepted"))
            if ent is None:
                continue
            m = Monitor(graph_id=gid, entity_id=ent.id, question=question, predicates=preds, cadence_minutes=1440,
                        next_run_at=utcnow() + timedelta(hours=20), created_at=utcnow() - timedelta(days=3))
            s.add(m)
            s.flush()
            checks.append((m.id, n))
    for mid, n in checks:
        for _ in range(n):
            run_monitor_check(mid)
    return gid


def seed_demo(force: bool = False) -> list[str]:
    init_db()
    user_id, ws_id = ensure_demo_user()
    with session_scope() as s:
        existing = list(s.scalars(select(Graph.id).where(Graph.workspace_id == ws_id)))
        if existing and not force:
            return existing
        for gid in existing:
            s.delete(s.get(Graph, gid))
    from .agent.scenarios import SCENARIOS
    return [seed_graph(ws_id, user_id, sid) for sid in SCENARIOS]
