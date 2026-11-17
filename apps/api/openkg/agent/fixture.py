"""Deterministic simulation engine.

Plays a scripted scenario through the *same* recorder, ledger, event log and step-idempotency
machinery as live research, so the demo exercises real persistence and review behaviour.
All sources are `fixture://` identifiers and every usage row is flagged simulated.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select

from ..domain.canonical import sha
from ..domain.events import emit
from ..domain.recorder import (
    CandidateClaim,
    CandidateEntity,
    CandidateRelationship,
    Recorder,
    register_source,
)
from ..models import Entity, FrontierQuestion, ResearchNote
from ..worker.context import BudgetExhausted, RunContext, budget_block


@dataclass
class Step:
    key: str
    kind: str
    data: dict = field(default_factory=dict)
    pause: float = 1.0  # multiplier on FIXTURE_STEP_DELAY


@dataclass
class Chapter:
    title: str
    objective: str
    steps: list[Step]


@dataclass
class Scenario:
    id: str
    name: str
    description: str
    prompt: str
    charter: dict
    ontology: dict
    sources: dict[str, dict]
    chapters: list[Chapter]
    monitor_scripts: dict[str, list[dict]] = field(default_factory=dict)
    seed: dict = field(default_factory=dict)

    def source_url(self, key: str) -> str:
        return self.sources[key]["url"]


def get_scenario(scenario_id: str) -> Scenario:
    from .scenarios import SCENARIOS
    if scenario_id not in SCENARIOS:
        raise ValueError(f"Unknown synthetic scenario '{scenario_id}'")
    return SCENARIOS[scenario_id]


def list_scenarios() -> list[dict[str, str]]:
    from .scenarios import SCENARIOS
    return [{"id": s.id, "name": s.name, "description": s.description, "prompt": s.prompt} for s in SCENARIOS.values()]


SATURATED = Chapter("Saturation check", "Confirm nothing new remains in the synthetic corpus", [
    Step("sat:status", "status", {"status": "researching"}),
    Step("sat:note", "note", {"kind": "decision", "text": "Every document in this synthetic corpus has already "
         "been read. Re-reading unchanged sources would spend budget without new evidence, so the run stops "
         "here. Connect Tavily and a model provider to research the live web."}),
    Step("sat:finish", "finish", {"text": "No new evidence available in the synthetic corpus; saturation reached."}),
])


class FixtureEngine:
    def run(self, ctx: RunContext) -> None:
        with ctx.tx() as (_, run, graph):
            scenario = get_scenario(run.config.get("scenario") or graph.scenario or "")
            idx = int(run.config.get("chapter", 0))
            start = int((run.cursor or {}).get("step", 0))
            delay = float(run.config.get("step_delay", ctx.settings.fixture_step_delay))
        chapter = scenario.chapters[idx] if idx < len(scenario.chapters) else SATURATED
        for i in range(start, len(chapter.steps)):
            ctx.checkpoint()
            step = chapter.steps[i]
            with ctx.tx() as (s, run, graph):
                if step.kind in ("search", "read"):
                    reason = budget_block(run, need_tavily=1)
                    if reason:
                        raise BudgetExhausted(reason)
                # Step effects + completion marker + cursor commit atomically: a crash anywhere
                # inside rolls the whole step back, and a redelivery skips completed steps.
                if ctx.mark_step(s, step.key, step.kind):
                    self.apply(ctx, s, run, graph, scenario, step)
                run.cursor = {"step": i + 1}
            if delay:
                ctx.sleep(delay * step.pause)

    # ------------------------------------------------------------------------------------------

    def apply(self, ctx: RunContext, s, run, graph, scenario: Scenario, step: Step) -> None:
        d, kind = step.data, step.kind
        if kind == "status":
            if run.status in ("queued", "planning", "researching", "verifying", "resuming"):
                run.status = d["status"]
                emit(s, run, "run.status", {"status": d["status"]})
        elif kind == "plan":
            emit(s, run, "plan.created", {**d, "simulated": True})
        elif kind == "slice":
            run.current_focus = d["title"]
            emit(s, run, "slice.started", d)
        elif kind == "search":
            results = [{k: scenario.sources[key].get(k) for k in ("title", "url", "domain", "published_at")}
                       for key in d["results"]]
            credits = 2 if d.get("depth") == "advanced" else 1
            ctx.charge_tavily(s, run, "search", credits, simulated=True, units={"results": len(results)})
            emit(s, run, "search.performed", {"agent": d.get("agent", "scout"), "query": d["query"],
                                              "depth": d.get("depth", "basic"), "results": results,
                                              "simulated": True})
        elif kind == "read":
            out = []
            for key in d["sources"]:
                src = scenario.sources[key]
                row = register_source(s, graph.id, url=src["url"], title=src["title"], content=src["text"],
                                      published_at=src.get("published_at"), synthetic=True,
                                      meta={"fixture": key, "scenario": scenario.id})
                out.append({"id": row.id, "title": row.title, "url": row.url, "domain": row.domain,
                            "published_at": row.published_at})
            ctx.charge_tavily(s, run, "extract", max(1, -(-len(out) // 5)), simulated=True, units={"urls": len(out)})
            emit(s, run, "source.read", {"agent": d.get("agent", "investigator"), "sources": out, "simulated": True})
        elif kind == "record":
            rec = Recorder(s, graph, run.id)
            focus: list[str] = []
            for raw in d.get("entities", []):
                res = rec.record_entity(CandidateEntity.model_validate(raw), discovered_via=d.get("via", ""))
                self._flush(s, run, res, d.get("agent", "investigator"))
                if res.entity_id:
                    focus.append(res.entity_id)
            for raw in d.get("relationships", []):
                res = rec.record_relationship(CandidateRelationship.model_validate(raw))
                self._flush(s, run, res, d.get("agent", "investigator"))
            for raw in d.get("claims", []):
                ent = rec.find_entity(CandidateEntity.model_construct(
                    type=raw["entity"]["type"], name=raw["entity"]["name"],
                    identifiers=raw["entity"].get("identifiers", {}), disambiguator=raw["entity"].get("disambiguator")))
                if ent is None:
                    continue
                res = rec.record_claim(ent, CandidateClaim.model_validate(raw["claim"]))
                self._flush(s, run, res, d.get("agent", "investigator"))
                focus.append(ent.id)
            if focus:
                emit(s, run, "focus", {"entity_ids": list(dict.fromkeys(focus))[:12]})
        elif kind == "verify":
            if run.status in ("researching", "planning"):
                run.status = "verifying"
                emit(s, run, "run.status", {"status": "verifying"})
            emit(s, run, "verify.result", {"agent": "verifier", **d})
        elif kind == "note":
            s.add(ResearchNote(graph_id=graph.id, run_id=run.id, kind=d.get("kind", "note"), content=d["text"]))
            emit(s, run, "note", d)
        elif kind == "frontier":
            added = []
            for q in d["questions"]:
                key = sha(q["question"].lower())
                if s.scalar(select(FrontierQuestion.id).where(
                        FrontierQuestion.graph_id == graph.id, FrontierQuestion.question_key == key)):
                    continue
                ent_id = None
                if q.get("entity"):
                    ent_id = s.scalar(select(Entity.id).where(Entity.graph_id == graph.id, Entity.name == q["entity"]))
                fq = FrontierQuestion(graph_id=graph.id, run_id=run.id, question=q["question"], question_key=key,
                                      rationale=q.get("rationale", ""), info_gain=q.get("info_gain", 0.5),
                                      est_calls=q.get("est_calls", 3), entity_id=ent_id)
                s.add(fq)
                s.flush()
                added.append({"id": fq.id, "question": fq.question, "info_gain": fq.info_gain})
            if added:
                emit(s, run, "frontier.added", {"questions": added})
        elif kind == "finish":
            run.summary = d["text"]
            s.add(ResearchNote(graph_id=graph.id, run_id=run.id, kind="dossier", content=d["text"]))
            emit(s, run, "run.summary", {"text": d["text"], "simulated": True})
        else:  # pragma: no cover
            raise ValueError(f"unknown step kind {kind}")

    @staticmethod
    def _flush(s, run, res, agent: str) -> None:
        for etype, payload in res.events:
            emit(s, run, etype, {"agent": agent, **payload})
