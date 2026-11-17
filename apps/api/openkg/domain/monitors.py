"""Focused, incremental monitoring.

A check never re-researches the graph. It gathers evidence for one target, skips documents
whose content hash is unchanged, pushes any candidate claims through the normal recorder and
raises a change + notification only when a watched fact materially differs.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Protocol

from sqlalchemy import or_, select, update

from ..db import session_scope, utcnow
from ..models import ChangeEvent, Entity, Graph, Monitor, MonitorRun, Notification, Source, Suggestion
from .canonical import canonical_url, norm_text, sha
from .recorder import CandidateClaim, Recorder, register_source

log = logging.getLogger("openkg.monitors")


@dataclass
class Observation:
    sources: list[dict] = field(default_factory=list)  # {url, title, content, published_at}
    claims: list[CandidateClaim] = field(default_factory=list)
    simulated: bool = False


class Observer(Protocol):
    def observe(self, graph: Graph, monitor: Monitor, entity: Entity, known_hashes: dict[str, str]) -> Observation: ...


class FixtureObserver:
    """Replays a scripted sequence of 'what the web looks like' for synthetic graphs."""

    def observe(self, graph, monitor, entity, known_hashes) -> Observation:
        from ..agent.fixture import get_scenario
        scenario = get_scenario(graph.scenario or "")
        script = scenario.monitor_scripts.get(entity.name)
        if not script:
            return Observation(simulated=True)
        frame = script[min(monitor.check_count, len(script) - 1)]
        sources = [{"url": scenario.sources[k]["url"], "title": scenario.sources[k]["title"],
                    "content": scenario.sources[k]["text"], "published_at": scenario.sources[k].get("published_at")}
                   for k in frame["sources"]]
        return Observation(sources=sources, claims=[CandidateClaim.model_validate(c) for c in frame["claims"]],
                           simulated=True)


def pick_observer(graph: Graph) -> Observer:
    if graph.is_synthetic:
        return FixtureObserver()
    from ..agent.monitor_live import LiveObserver
    return LiveObserver()


def due_monitors(limit: int = 5) -> list[str]:
    """Bounded batch: after downtime each overdue monitor runs once, not once per missed slot."""
    now = utcnow()
    with session_scope() as s:
        return list(s.scalars(select(Monitor.id).where(
            Monitor.status.in_(("active", "muted")), Monitor.next_run_at <= now,
            or_(Monitor.locked_until.is_(None), Monitor.locked_until < now))
            .order_by(Monitor.next_run_at).limit(limit)))


def run_monitor_check(monitor_id: str, observer: Observer | None = None) -> str | None:
    """Returns the MonitorRun id, or None if another worker is already checking this monitor."""
    now = utcnow()
    with session_scope() as s:
        locked = s.execute(update(Monitor).where(
            Monitor.id == monitor_id, or_(Monitor.locked_until.is_(None), Monitor.locked_until < now))
            .values(locked_until=now + timedelta(minutes=5))).rowcount
    if not locked:
        return None
    try:
        return _check(monitor_id, observer)
    except Exception as e:  # noqa: BLE001
        log.exception("monitor check failed", extra={"monitor_id": monitor_id})
        with session_scope() as s:
            m = s.get(Monitor, monitor_id)
            if m is None:
                return None
            m.failure, m.last_outcome, m.last_checked_at = f"{type(e).__name__}: {e}"[:500], "failed", utcnow()
            m.next_run_at = utcnow() + timedelta(minutes=min(m.cadence_minutes, 60))
            run = MonitorRun(monitor_id=m.id, graph_id=m.graph_id, outcome="failed", summary=m.failure,
                             finished_at=utcnow())
            s.add(run)
            s.flush()
            return run.id
    finally:
        with session_scope() as s:
            s.execute(update(Monitor).where(Monitor.id == monitor_id).values(locked_until=None))


def _check(monitor_id: str, observer: Observer | None) -> str | None:
    # 1) Short read transaction. No database transaction is held open across provider calls.
    with session_scope() as s:
        m = s.get(Monitor, monitor_id)
        if m is None:
            return None
        graph = s.get(Graph, m.graph_id)
        entity = s.get(Entity, m.entity_id) if m.entity_id else None
        if entity is None:
            raise ValueError("Monitor target no longer exists")
        while entity.status == "merged" and entity.merged_into_id:
            entity = s.get(Entity, entity.merged_into_id)
            m.entity_id = entity.id
        known = dict(s.execute(select(Source.canonical_url, Source.content_hash).where(Source.graph_id == graph.id)).all())
        s.flush()
        for obj in (m, graph, entity):
            s.expunge(obj)
    # 2) Observe the world (network / model calls happen here).
    obs = (observer or pick_observer(graph)).observe(graph, m, entity, known)
    # 3) Record the outcome atomically.
    with session_scope() as s:
        m = s.get(Monitor, monitor_id)
        if m is None:
            return None
        graph = s.get(Graph, m.graph_id)
        entity = s.get(Entity, m.entity_id)
        fresh = [src for src in obs.sources
                 if known.get(canonical_url(src["url"])) != sha(norm_text((src.get("content") or "")[:20_000]))]
        run = MonitorRun(monitor_id=m.id, graph_id=graph.id, sources_seen=len(obs.sources), sources_new=len(fresh),
                         simulated=obs.simulated)
        s.add(run)
        s.flush()
        outcome, summaries = "no_change", []
        if fresh:
            for src in obs.sources:
                register_source(s, graph.id, url=src["url"], title=src.get("title", ""), content=src.get("content", ""),
                                published_at=src.get("published_at"), synthetic=obs.simulated)
            rec = Recorder(s, graph, None)
            watched = set(m.predicates or [])
            for cand in obs.claims:
                if watched and cand.predicate not in watched:
                    continue
                res = rec.record_claim(entity, cand)
                for etype, payload in res.events:
                    if etype != "candidate.claim" or payload.get("confidence") == "low":
                        continue  # unverifiable excerpts never raise an alert
                    kind = payload.get("change", "new")
                    label = cand.predicate.replace("_", " ")
                    before, after = payload.get("before"), payload.get("after") or {"value": cand.value, "valid_from": cand.valid_from}
                    if kind == "changed":
                        text = f"{entity.name}: {label} changed from “{before['value']}” to “{after['value']}”"
                    elif kind == "contradicted":
                        text = f"{entity.name}: conflicting {label} — “{before['value']}” vs “{after['value']}”"
                    else:
                        text = f"{entity.name}: new {label} “{after['value']}”"
                    sug_id = payload.get("suggestion_id")
                    sug = s.get(Suggestion, sug_id) if sug_id else None
                    ch = ChangeEvent(graph_id=graph.id, monitor_id=m.id, monitor_run_id=run.id, kind=kind,
                                     entity_id=entity.id, predicate=cand.predicate, before=before, after=after,
                                     before_claim_id=(before or {}).get("claim_id"),
                                     after_claim_id=(sug.claim_ids[0] if sug and sug.claim_ids else None),
                                     suggestion_id=sug_id, summary=text)
                    s.add(ch)
                    s.flush()
                    if m.status != "muted":
                        s.add(Notification(workspace_id=graph.workspace_id, graph_id=graph.id, change_event_id=ch.id,
                                           title=text, body=f"Detected by monitor “{m.question or label}”. "
                                                            f"Review the before/after evidence."))
                    summaries.append(text)
                    outcome = kind if outcome == "no_change" or kind == "changed" else outcome
        if outcome == "no_change":
            if not obs.sources and entity.last_verified_at and entity.last_verified_at < utcnow() - timedelta(days=180):
                outcome = "stale"
                summary = "No current evidence found and the last verification is more than 180 days old."
                s.add(ChangeEvent(graph_id=graph.id, monitor_id=m.id, monitor_run_id=run.id, kind="stale",
                                  entity_id=entity.id, summary=f"{entity.name}: {summary}"))
            elif fresh:
                summary = f"{len(fresh)} new or updated source(s) read; watched facts are unchanged."
                entity.last_verified_at = utcnow()
            else:
                summary = (f"{len(obs.sources)} known source(s) unchanged since the last check; nothing re-read."
                           if obs.sources else "No relevant new evidence found.")
        else:
            summary = "; ".join(summaries)
            m.last_changed_at = utcnow()
        now = utcnow()
        run.outcome, run.summary, run.finished_at = outcome, summary, now
        m.check_count += 1
        m.last_checked_at, m.last_outcome, m.failure = now, outcome, ""
        m.next_run_at = now + timedelta(minutes=m.cadence_minutes)
        return run.id
