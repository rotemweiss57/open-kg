"""Monitoring: material changes only, before/after evidence, no alert for unchanged sources."""
from datetime import timedelta

from sqlalchemy import func, select

from openkg.db import session_scope, utcnow
from openkg.domain.monitors import Observation, due_monitors, run_monitor_check
from openkg.domain.recorder import CandidateClaim
from openkg.domain.review import decide
from openkg.models import (
    ChangeEvent,
    Claim,
    Entity,
    Graph,
    Monitor,
    MonitorRun,
    Notification,
    ProviderUsage,
    Suggestion,
)
from openkg.seed import seed_graph


def seeded(workspace, scenario="series-b-product-leaders"):
    return seed_graph(workspace["workspace_id"], workspace["user_id"], scenario)


def monitor_for(gid, name, **kw) -> str:
    with session_scope() as s:
        m = s.scalar(select(Monitor).join(Entity, Entity.id == Monitor.entity_id).where(Monitor.graph_id == gid, Entity.name == name))
        for k, v in kw.items():
            setattr(m, k, v)
        return m.id


def n(model, gid):
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(model).where(model.graph_id == gid))


def test_unchanged_source_is_ignored_then_real_change_alerts_with_before_after(workspace):
    gid = seeded(workspace)
    mid = monitor_for(gid, "Fennec Security")  # seeded with exactly one (no-change) check
    with session_scope() as s:
        first = s.scalar(select(MonitorRun).where(MonitorRun.monitor_id == mid))
        assert first.outcome == "no_change" and first.sources_new == 0
    base_changes, base_notes = n(ChangeEvent, gid), n(Notification, gid)

    run_id = run_monitor_check(mid)  # the scripted world now contains a Series C announcement
    with session_scope() as s:
        run = s.get(MonitorRun, run_id)
        assert run.outcome == "changed" and run.sources_new == 2
        ch = s.scalar(select(ChangeEvent).where(ChangeEvent.monitor_run_id == run_id))
        assert (ch.kind, ch.predicate, ch.before["value"], ch.after["value"]) == ("changed", "funding_stage", "Series B", "Series C")
        assert s.get(Claim, ch.before_claim_id).status == "accepted"  # graph unchanged until a reviewer accepts
        assert s.get(Claim, ch.after_claim_id).confidence == "high"  # two independent outlets
        note = s.scalar(select(Notification).where(Notification.change_event_id == ch.id))
        assert "Series B" in note.title and "Series C" in note.title and note.read_at is None
        sug = s.get(Suggestion, ch.suggestion_id)
        decide(s, s.get(Graph, gid), sug, "approve")
        assert s.get(Claim, ch.before_claim_id).status == "superseded"  # old value kept as history
    assert (n(ChangeEvent, gid), n(Notification, gid)) == (base_changes + 1, base_notes + 1)

    run_monitor_check(mid)  # same world again: the pages are unchanged, so nothing is re-read or re-alerted
    with session_scope() as s:
        last = s.scalar(select(MonitorRun).where(MonitorRun.monitor_id == mid).order_by(MonitorRun.started_at.desc()))
        assert last.outcome == "no_change" and last.sources_new == 0
        m = s.get(Monitor, mid)
        assert m.check_count == 3 and m.last_changed_at is not None and m.next_run_at > utcnow()
    assert (n(ChangeEvent, gid), n(Notification, gid)) == (base_changes + 1, base_notes + 1)


def test_muted_monitor_records_change_without_notifying(workspace):
    gid = seeded(workspace, "solid-state-battery-supply-chain")
    mid = monitor_for(gid, "Altamira Motors", status="muted")
    notes = n(Notification, gid)
    run_monitor_check(mid)  # frame 0: unchanged
    run_monitor_check(mid)  # frame 1: programme delayed
    with session_scope() as s:
        ch = s.scalar(select(ChangeEvent).where(ChangeEvent.monitor_id == mid))
        assert ch.after["value"] == "2028" and ch.before["value"] == "2027"
    assert n(Notification, gid) == notes


class StubObserver:
    def __init__(self, obs):
        self.obs, self.calls = obs, 0

    def observe(self, graph, monitor, entity, known):
        self.calls += 1
        return self.obs


def test_unverifiable_or_unwatched_claims_never_alert(workspace):
    gid = seeded(workspace)
    mid = monitor_for(gid, "Priya Raman")
    page = {"url": "https://rumours.example.com/p", "title": "Rumour", "content": "People are saying things about Priya Raman, Northwind Robotics and its leadership team this week."}
    made_up = CandidateClaim(predicate="title", value="Chief Executive Officer", valid_from="2026-11",
                             evidence=[{"url": page["url"], "excerpt": "Priya Raman was named Chief Executive Officer"}])
    unwatched = CandidateClaim(predicate="location", value="Austin", evidence=[{"url": page["url"], "excerpt": "Northwind Robotics and its leadership team"}])
    before = n(ChangeEvent, gid), n(Notification, gid)
    run_monitor_check(mid, StubObserver(Observation(sources=[page], claims=[made_up, unwatched])))
    assert (n(ChangeEvent, gid), n(Notification, gid)) == before  # quote not on the page => no alert
    with session_scope() as s:
        assert s.scalar(select(Claim).where(Claim.value_norm == "chief executive officer", Claim.predicate == "title", Claim.confidence == "low")) is not None
        assert s.scalar(select(func.count()).where(Claim.predicate == "location")) == 0  # outside the monitor's remit


def test_overlapping_checks_are_prevented_and_failures_are_recorded(workspace):
    gid = seeded(workspace)
    mid = monitor_for(gid, "Priya Raman", locked_until=utcnow() + timedelta(minutes=3))
    assert run_monitor_check(mid) is None  # another worker holds it
    monitor_for(gid, "Priya Raman", locked_until=None)

    class Boom:
        def observe(self, *a):
            raise TimeoutError("provider timed out")

    run_id = run_monitor_check(mid, Boom())
    with session_scope() as s:
        m = s.get(Monitor, mid)
        assert s.get(MonitorRun, run_id).outcome == "failed" and "timed out" in m.failure and m.locked_until is None
        assert m.next_run_at is not None  # rescheduled, not stuck


def test_catch_up_after_downtime_is_bounded(workspace):
    gid = seeded(workspace)
    with session_scope() as s:
        ent_ids = list(s.scalars(select(Entity.id).where(Entity.graph_id == gid, Entity.status == "accepted").limit(12)))
        s.query(Monitor).delete()
        for eid in ent_ids:  # all overdue by many missed slots
            s.add(Monitor(graph_id=gid, entity_id=eid, cadence_minutes=60, next_run_at=utcnow() - timedelta(days=9)))
        s.add(Monitor(graph_id=gid, entity_id=ent_ids[0], status="paused", next_run_at=utcnow() - timedelta(days=9)))
    batch = due_monitors(limit=5)
    assert len(batch) == 5
    for mid in batch:
        run_monitor_check(mid)
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(MonitorRun)) == 5  # one run each, not one per missed hour
        assert all(m.next_run_at > utcnow() for m in s.scalars(select(Monitor).where(Monitor.id.in_(batch))))
    assert len(due_monitors(limit=50)) == 7  # the paused monitor is never scheduled


def test_live_observer_skips_the_model_when_nothing_is_new(workspace, monkeypatch):
    """Contract test with fakes: unchanged search results cost one search and zero model calls."""
    import sys
    sys.path.insert(0, "tests")
    from fakes import PAGES, FakeRouter, fake_tavily

    from openkg.agent.monitor_live import LiveObserver
    from openkg.domain.recorder import register_source

    with session_scope() as s:
        g = Graph(workspace_id=workspace["workspace_id"], name="live", ontology={"entity_types": [{"type": "Company", "attributes": []}]})
        s.add(g)
        s.flush()
        ent = Entity(graph_id=g.id, type="Company", canonical_key="company:acme", name="Acme Robotics", name_norm="acme robotics", status="accepted")
        s.add(ent)
        s.flush()
        m = Monitor(graph_id=g.id, entity_id=ent.id, question="funding", predicates=["funding_stage"])
        s.add(m)
        for url, page in PAGES.items():
            register_source(s, g.id, url=url, content=page["text"][:300])
        s.flush()
        gid, mid = g.id, m.id
    calls: list = []
    router = FakeRouter()
    run_id = run_monitor_check(mid, LiveObserver(tavily=fake_tavily(calls), router=router))
    with session_scope() as s:
        assert s.get(MonitorRun, run_id).outcome == "no_change"
        assert [u.operation for u in s.scalars(select(ProviderUsage).where(ProviderUsage.graph_id == gid))] == ["monitor.search"]
    # the first check looks back a month and names the entity exactly; no model call when nothing is new
    assert len(calls) == 1 and calls[0]["body"]["time_range"] == "month" and router.llm.calls == []
    assert calls[0]["body"]["query"] == '"Acme Robotics" funding stage'
    run_monitor_check(mid, LiveObserver(tavily=fake_tavily(calls), router=router))
    assert calls[1]["body"]["time_range"] == "day"  # later checks only need what is new


def test_watched_attribute_matches_however_the_model_names_it(workspace):
    """Regression from a live check: the model answered with predicate "has_title", which did not
    match the watched "title", so a real change was dropped without a trace."""
    gid = seeded(workspace)
    mid = monitor_for(gid, "Priya Raman")
    page = {"url": "https://news.example.com/priya", "title": "News",
            "content": "Priya Raman was named Chief Product Officer of Northwind Robotics in November 2026."}
    claim = CandidateClaim(predicate="has_title", value="Chief Product Officer", valid_from="2026-11",
                           evidence=[{"url": page["url"], "excerpt": "Priya Raman was named Chief Product Officer of Northwind Robotics"}])
    before = n(ChangeEvent, gid)
    run_monitor_check(mid, StubObserver(Observation(sources=[page], claims=[claim])))
    assert n(ChangeEvent, gid) == before + 1
    with session_scope() as s:
        ch = s.scalar(select(ChangeEvent).where(ChangeEvent.graph_id == gid).order_by(ChangeEvent.created_at.desc()))
        assert ch.kind == "changed" and ch.predicate == "title" and "Chief Product Officer" in ch.summary


def test_structured_output_accepts_json_text_and_never_hides_a_parsing_failure():
    """Regression from a live check: the provider returned the JSON as text after a reasoning block;
    the parser gave up and the monitor reported "no change"."""
    import pytest
    from langchain_core.messages import AIMessage
    from pydantic import BaseModel

    from openkg.agent.models import structured

    class Shape(BaseModel):
        items: list[str] = []

    class Fake:
        def __init__(self, content):
            self.content = content

        def with_structured_output(self, schema, include_raw=False):
            return self

        def invoke(self, prompt):
            return {"raw": AIMessage(content=self.content), "parsed": None, "parsing_error": "no parsed field"}

    blocks = [{"type": "reasoning", "summary": []}, {"type": "text", "text": '```json\n{"items": ["a", "b"]}\n```'}]
    parsed, _ = structured(Fake(blocks), Shape, "x")
    assert parsed.items == ["a", "b"]
    assert structured(Fake('Here you go: {"items": []} done'), Shape, "x")[0].items == []
    with pytest.raises(ValueError, match="did not return structured output"):
        structured(Fake("I could not find anything."), Shape, "x")
    with pytest.raises(ValueError, match="does not match"):
        structured(Fake('{"items": "not-a-list"}'), Shape, "x")


def test_an_undated_remark_does_not_alert_against_a_dated_fact(workspace):
    """Regression from a live check: a tweet saying someone was "running YC" raised a change alert
    against a title dated years later."""
    gid = seeded(workspace)
    mid = monitor_for(gid, "Priya Raman")
    page = {"url": "https://social.example.com/post", "title": "Post", "content": "Back when Priya Raman was running product at Brightloom, things were different."}
    remark = CandidateClaim(predicate="title", value="running product at Brightloom",
                            evidence=[{"url": page["url"], "excerpt": "Priya Raman was running product at Brightloom"}])
    before = n(ChangeEvent, gid), n(Notification, gid)
    run_monitor_check(mid, StubObserver(Observation(sources=[page], claims=[remark])))
    assert (n(ChangeEvent, gid), n(Notification, gid)) == before
    with session_scope() as s:  # still visible to a reviewer as a conflict
        assert s.scalar(select(Suggestion.id).where(Suggestion.graph_id == gid, Suggestion.kind == "contradiction", Suggestion.title.like("%Priya Raman%")))
