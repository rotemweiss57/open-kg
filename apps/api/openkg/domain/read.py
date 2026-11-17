"""Read models: projections over claims/evidence for the UI. No writes happen here."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import schemas as S
from ..models import (
    AuditEvent,
    ChangeEvent,
    Claim,
    ClaimEvidence,
    Entity,
    Graph,
    Monitor,
    Notification,
    ProviderUsage,
    Relationship,
    Run,
    Source,
    Suggestion,
)

LIVE = ("proposed", "accepted")
CURRENT = ("accepted", "contested")


# --- claims -------------------------------------------------------------------------------------


def claims_out(s: Session, claims: list[Claim]) -> list[S.ClaimOut]:
    if not claims:
        return []
    ids = [c.id for c in claims]
    rows = s.execute(select(ClaimEvidence, Source).join(Source, Source.id == ClaimEvidence.source_id)
                     .where(ClaimEvidence.claim_id.in_(ids)).order_by(ClaimEvidence.created_at)).all()
    by_claim: dict[str, list[S.EvidenceOut]] = defaultdict(list)
    indep: dict[str, set] = defaultdict(set)
    for ev, src in rows:
        by_claim[ev.claim_id].append(S.EvidenceOut(
            id=ev.id, excerpt=ev.excerpt, stance=ev.stance, excerpt_verified=ev.excerpt_verified,
            source=S.SourceOut.model_validate(src)))
        if ev.stance == "supports" and ev.excerpt_verified:
            indep[ev.claim_id].add(src.syndication_key or src.domain)
    return [S.ClaimOut(
        id=c.id, predicate=c.predicate, value=c.value, valid_from=c.valid_from, valid_to=c.valid_to,
        confidence=c.confidence, is_inferred=c.is_inferred, status=c.status, observed_at=c.observed_at,
        verified_at=c.verified_at, accepted_at=c.accepted_at, independent_sources=len(indep[c.id]),
        evidence=by_claim[c.id]) for c in claims]


def suggestions_out(s: Session, sugs: list[Suggestion]) -> list[S.SuggestionOut]:
    all_ids = {cid for g in sugs for cid in (g.claim_ids or [])} | {g.target_claim_id for g in sugs if g.target_claim_id}
    claims = list(s.scalars(select(Claim).where(Claim.id.in_(all_ids)))) if all_ids else []
    cmap = {c.id: c for c in claims_out(s, claims)}
    out = []
    for g in sugs:
        o = S.SuggestionOut.model_validate(g)
        o.claims = [cmap[c] for c in (g.claim_ids or []) if c in cmap]
        o.target_claim = cmap.get(g.target_claim_id) if g.target_claim_id else None
        if g.status == "pending" and o.claims:  # corroboration since creation may have lifted confidence
            o.confidence = o.claims[0].confidence
        o.decided_by_kind = "" if g.status == "pending" else ("policy" if g.status == "auto_approved" else
                                                            ("user" if g.decided_by else "system"))
        out.append(o)
    return out


# --- graph cards --------------------------------------------------------------------------------


def graph_stats(s: Session, g: Graph) -> S.GraphStats:
    def count(stmt):
        return s.scalar(stmt) or 0

    types = dict(s.execute(select(Entity.type, func.count()).where(
        Entity.graph_id == g.id, Entity.status == "accepted").group_by(Entity.type)).all())
    return S.GraphStats(
        entities=sum(types.values()), type_counts=types,
        proposed_entities=count(select(func.count()).where(Entity.graph_id == g.id, Entity.status == "proposed")),
        relationships=count(select(func.count()).where(Relationship.graph_id == g.id, Relationship.status == "accepted")),
        pending_suggestions=count(select(func.count()).where(Suggestion.graph_id == g.id, Suggestion.status == "pending")),
        contested_claims=count(select(func.count()).where(Claim.graph_id == g.id, Claim.status == "contested")),
        accepted_claims=count(select(func.count()).where(Claim.graph_id == g.id, Claim.status == "accepted")),
        sources=count(select(func.count()).where(Source.graph_id == g.id)),
        active_monitors=count(select(func.count()).where(Monitor.graph_id == g.id, Monitor.status == "active")),
        unread_changes=count(select(func.count()).where(Notification.graph_id == g.id, Notification.read_at.is_(None))),
        spent_usd=round(count(select(func.sum(ProviderUsage.est_cost_usd)).where(
            ProviderUsage.graph_id == g.id, ProviderUsage.simulated.is_(False))), 4),
    )


def graph_out(s: Session, g: Graph) -> S.GraphOut:
    latest = s.scalar(select(Run).where(Run.graph_id == g.id, Run.kind == "research")
                      .order_by(Run.created_at.desc()).limit(1))
    recent = list(s.scalars(select(Entity.name).where(Entity.graph_id == g.id, Entity.status.in_(LIVE))
                            .order_by(Entity.first_seen_at.desc(), Entity.id).limit(4)))
    out = S.GraphOut.model_validate(g)
    out.stats = graph_stats(s, g)
    out.latest_run = S.RunBrief.model_validate(latest) if latest else None
    out.last_researched_at = (latest.finished_at or latest.started_at) if latest else None
    out.recent_discoveries = recent
    return out


# --- canvas -------------------------------------------------------------------------------------


def _nodes(s: Session, graph_id: str, ents: list[Entity], degree: Counter) -> list[S.NodeOut]:
    ids = [e.id for e in ents]
    contested = set(s.scalars(select(Claim.entity_id).where(
        Claim.graph_id == graph_id, Claim.status == "contested", Claim.entity_id.in_(ids)))) if ids else set()
    watched = set(s.scalars(select(Monitor.entity_id).where(
        Monitor.graph_id == graph_id, Monitor.status == "active", Monitor.entity_id.in_(ids)))) if ids else set()
    return [S.NodeOut(id=e.id, name=e.name, type=e.type, status=e.status, degree=degree[e.id],
                      contested=e.id in contested, watched=e.id in watched, description=e.description,
                      first_seen_at=e.first_seen_at) for e in ents]


def _edges(s: Session, rels: list[Relationship], as_of: datetime | None = None) -> list[S.EdgeOut]:
    conf = {}
    if rels:
        conf = dict(s.execute(select(Claim.relationship_id, Claim.confidence).where(
            Claim.relationship_id.in_([r.id for r in rels]))).all())
    day = (as_of.date().isoformat() if as_of else datetime.now().date().isoformat())
    return [S.EdgeOut(id=r.id, from_id=r.from_id, to_id=r.to_id, type=r.type, status=r.status,
                      valid_from=r.valid_from, valid_to=r.valid_to, confidence=conf.get(r.id, "medium"),
                      ended=bool(r.valid_to and r.valid_to <= day), first_seen_at=r.first_seen_at) for r in rels]


def snapshot(s: Session, g: Graph, *, as_of: datetime | None = None, include_proposed: bool = True,
             limit: int = 400) -> S.Snapshot:
    """Level-of-detail snapshot: at most `limit` nodes, the best-connected first.

    `as_of` is *observation time* ("the graph as it was known then"); valid-time intervals are
    reported per edge (`ended`) rather than used to hide history.
    """
    statuses = LIVE if include_proposed else ("accepted",)
    eq = select(Entity).where(Entity.graph_id == g.id, Entity.status.in_(statuses))
    rq = select(Relationship).where(Relationship.graph_id == g.id, Relationship.status.in_(statuses))
    if as_of:
        eq, rq = eq.where(Entity.first_seen_at <= as_of), rq.where(Relationship.first_seen_at <= as_of)
    ents, rels = list(s.scalars(eq)), list(s.scalars(rq))
    live = {e.id for e in ents}
    rels = [r for r in rels if r.from_id in live and r.to_id in live]
    degree: Counter = Counter()
    for r in rels:
        degree[r.from_id] += 1
        degree[r.to_id] += 1
    total = len(ents)
    if total > limit:
        ents = sorted(ents, key=lambda e: (-degree[e.id], e.first_seen_at))[:limit]
        keep = {e.id for e in ents}
        rels = [r for r in rels if r.from_id in keep and r.to_id in keep]
    return S.Snapshot(graph_id=g.id, as_of=as_of, nodes=_nodes(s, g.id, ents, degree), edges=_edges(s, rels, as_of),
                      total_nodes=total, truncated=total > limit)


def neighborhood(s: Session, g: Graph, entity_id: str, depth: int = 1, limit: int = 150) -> S.Snapshot:
    frontier, seen, rels_by_id = {entity_id}, {entity_id}, {}
    for _ in range(max(1, min(depth, 3))):
        if not frontier or len(seen) >= limit:
            break
        rels = list(s.scalars(select(Relationship).where(
            Relationship.graph_id == g.id, Relationship.status.in_(LIVE),
            or_(Relationship.from_id.in_(frontier), Relationship.to_id.in_(frontier)))))
        nxt = set()
        for r in rels:
            rels_by_id[r.id] = r
            nxt |= {r.from_id, r.to_id}
        frontier = nxt - seen
        seen |= nxt
    ents = list(s.scalars(select(Entity).where(Entity.graph_id == g.id, Entity.id.in_(seen), Entity.status.in_(LIVE))))
    keep = {e.id for e in ents}
    rels = [r for r in rels_by_id.values() if r.from_id in keep and r.to_id in keep]
    degree: Counter = Counter()
    for r in rels:
        degree[r.from_id] += 1
        degree[r.to_id] += 1
    return S.Snapshot(graph_id=g.id, nodes=_nodes(s, g.id, ents, degree), edges=_edges(s, rels), total_nodes=len(ents))


def timeline(s: Session, g: Graph) -> S.Timeline:
    ents = sorted(s.scalars(select(Entity.first_seen_at).where(Entity.graph_id == g.id, Entity.status.in_(LIVE))))
    rels = sorted(s.scalars(select(Relationship.first_seen_at).where(
        Relationship.graph_id == g.id, Relationship.status.in_(LIVE))))
    stamps = sorted(set(ents) | set(rels))
    if not stamps:
        return S.Timeline(start=None, end=None, points=[])
    step = max(1, len(stamps) // 60)
    picks = stamps[::step]
    if picks[-1] != stamps[-1]:
        picks.append(stamps[-1])
    import bisect
    points = [S.TimelinePoint(at=t, entities=bisect.bisect_right(ents, t), relationships=bisect.bisect_right(rels, t))
              for t in picks]
    return S.Timeline(start=stamps[0], end=stamps[-1], points=points)


# --- intelligence sheets --------------------------------------------------------------------------


def monitors_out(s: Session, mons: list[Monitor]) -> list[S.MonitorOut]:
    out = []
    for m in mons:
        o = S.MonitorOut.model_validate(m)
        if m.entity_id:
            o.target_name = s.scalar(select(Entity.name).where(Entity.id == m.entity_id)) or ""
        elif m.relationship_id:
            rel = s.get(Relationship, m.relationship_id)
            if rel:
                a, b = s.get(Entity, rel.from_id), s.get(Entity, rel.to_id)
                o.target_name = f"{a.name} → {b.name}"
        out.append(o)
    return out


def entity_detail(s: Session, g: Graph, ent: Entity) -> S.EntityDetail:
    claims = list(s.scalars(select(Claim).where(Claim.graph_id == g.id, Claim.entity_id == ent.id)
                            .order_by(Claim.observed_at)))
    cout = {c.id: c for c in claims_out(s, claims)}
    existence = next((cout[c.id] for c in claims if c.predicate == "exists"), None)
    by_pred: dict[str, list[Claim]] = defaultdict(list)
    for c in claims:
        if c.predicate != "exists" and c.status != "rejected":
            by_pred[c.predicate].append(c)
    attrs = []
    for pred, cs in by_pred.items():
        current = [c for c in cs if c.status in CURRENT and not c.valid_to] or [c for c in cs if c.status in CURRENT]
        cur = max(current, key=lambda c: (c.valid_from or "", c.observed_at), default=None)
        if cur is None:  # only proposed values so far
            cur = max((c for c in cs if c.status == "proposed"), key=lambda c: c.observed_at, default=None)
        hist = [cout[c.id] for c in sorted(cs, key=lambda c: (c.valid_from or "", c.observed_at), reverse=True)
                if cur is None or c.id != cur.id]
        attrs.append(S.AttributeOut(predicate=pred, current=cout[cur.id] if cur else None, history=hist))

    rels = list(s.scalars(select(Relationship).where(
        Relationship.graph_id == g.id, Relationship.status.in_(LIVE),
        or_(Relationship.from_id == ent.id, Relationship.to_id == ent.id))))
    other_ids = {r.to_id if r.from_id == ent.id else r.from_id for r in rels}
    others = {e.id: e for e in s.scalars(select(Entity).where(Entity.id.in_(other_ids)))} if other_ids else {}
    nodes = {n.id: n for n in _nodes(s, g.id, list(others.values()), Counter())}
    neighbors = [S.NeighborOut(relationship_id=r.id, type=r.type, status=r.status, valid_from=r.valid_from,
                               valid_to=r.valid_to, direction="out" if r.from_id == ent.id else "in",
                               entity=nodes[r.to_id if r.from_id == ent.id else r.from_id])
                 for r in rels if (r.to_id if r.from_id == ent.id else r.from_id) in nodes]

    known = {a.predicate for a in attrs if a.current and a.current.status in CURRENT}
    type_def = next((t for t in (g.ontology or {}).get("entity_types", []) if t.get("type") == ent.type), {})
    unknowns = [a["name"] for a in type_def.get("attributes", []) if a["name"] not in known]

    pending = list(s.scalars(select(Suggestion).where(
        Suggestion.graph_id == g.id, Suggestion.status == "pending",
        or_(Suggestion.entity_id == ent.id, Suggestion.merge_target_id == ent.id))))
    mons = list(s.scalars(select(Monitor).where(Monitor.graph_id == g.id, Monitor.entity_id == ent.id)))
    return S.EntityDetail(
        id=ent.id, graph_id=g.id, name=ent.name, type=ent.type, status=ent.status, aliases=ent.aliases or [],
        description=ent.description, canonical_key=ent.canonical_key, discovered_via=ent.discovered_via,
        discovered_run_id=ent.discovered_run_id, first_seen_at=ent.first_seen_at,
        last_verified_at=ent.last_verified_at, merged_into_id=ent.merged_into_id, existence=existence,
        attributes=sorted(attrs, key=lambda a: a.predicate), neighbors=neighbors, unknowns=unknowns,
        pending_suggestions=suggestions_out(s, pending), monitors=monitors_out(s, mons))


def relationship_detail(s: Session, g: Graph, rel: Relationship) -> S.RelationshipDetail:
    ends = {e.id: e for e in s.scalars(select(Entity).where(Entity.id.in_((rel.from_id, rel.to_id))))}
    nodes = {n.id: n for n in _nodes(s, g.id, list(ends.values()), Counter())}
    claims = list(s.scalars(select(Claim).where(Claim.graph_id == g.id, Claim.relationship_id == rel.id)))
    pending = list(s.scalars(select(Suggestion).where(
        Suggestion.graph_id == g.id, Suggestion.status == "pending", Suggestion.relationship_id == rel.id)))
    mons = list(s.scalars(select(Monitor).where(Monitor.graph_id == g.id, Monitor.relationship_id == rel.id)))
    rdef = next((t for t in (g.ontology or {}).get("relationship_types", []) if t.get("type") == rel.type), {})
    return S.RelationshipDetail(
        id=rel.id, graph_id=g.id, type=rel.type, status=rel.status, valid_from=rel.valid_from, valid_to=rel.valid_to,
        first_seen_at=rel.first_seen_at, last_verified_at=rel.last_verified_at,
        discovered_run_id=rel.discovered_run_id, source=nodes[rel.from_id], target=nodes[rel.to_id],
        description=rdef.get("description", ""), claims=claims_out(s, claims),
        pending_suggestions=suggestions_out(s, pending), monitors=monitors_out(s, mons))


# --- changes / usage ------------------------------------------------------------------------------


def changes_out(s: Session, rows: list[ChangeEvent]) -> list[S.ChangeOut]:
    out = []
    for ch in rows:
        o = S.ChangeOut.model_validate(ch)
        if ch.entity_id:
            o.entity_name = s.scalar(select(Entity.name).where(Entity.id == ch.entity_id)) or ""
        if ch.suggestion_id:
            o.suggestion_status = s.scalar(select(Suggestion.status).where(Suggestion.id == ch.suggestion_id)) or ""
        cl = {c.id: c for c in claims_out(s, list(s.scalars(select(Claim).where(
            Claim.id.in_([i for i in (ch.before_claim_id, ch.after_claim_id) if i])))))}
        if ch.before_claim_id in cl:
            o.before_evidence = [e for e in cl[ch.before_claim_id].evidence if e.stance == "supports"]
        if ch.after_claim_id in cl:
            o.after_evidence = [e for e in cl[ch.after_claim_id].evidence if e.stance == "supports"]
        out.append(o)
    return out


def usage_out(s: Session, g: Graph) -> S.UsageOut:
    rows = s.execute(select(ProviderUsage).where(ProviderUsage.graph_id == g.id)).scalars()
    agg: dict[tuple, S.UsageRow] = {}
    for r in rows:
        k = (r.provider, r.operation, r.simulated)
        row = agg.setdefault(k, S.UsageRow(provider=r.provider, operation=r.operation, calls=0, est_cost_usd=0,
                                           simulated=r.simulated))
        row.calls += 1
        row.est_cost_usd = round(row.est_cost_usd + r.est_cost_usd, 5)
        for uk, uv in (r.units or {}).items():
            if isinstance(uv, (int, float)):
                row.units[uk] = row.units.get(uk, 0) + uv
    spent = round(sum(r.est_cost_usd for r in agg.values() if not r.simulated), 4)
    accepted = s.scalar(select(func.count()).where(Claim.graph_id == g.id, Claim.status == "accepted")) or 0
    return S.UsageOut(budget_usd=g.budget_usd, spent_usd=spent, accepted_claims=accepted,
                      cost_per_accepted_claim=round(spent / accepted, 5) if accepted and spent else None,
                      rows=sorted(agg.values(), key=lambda r: (r.provider, r.operation)))


def audit_trail(s: Session, graph_id: str, limit: int = 100) -> list[AuditEvent]:
    return list(s.scalars(select(AuditEvent).where(AuditEvent.graph_id == graph_id)
                          .order_by(AuditEvent.created_at.desc()).limit(limit)))


def table(s: Session, g: Graph, entity_type: str | None = None, include_proposed: bool = True,
          limit: int = 2000) -> S.TableOut:
    """Entities as rows with their current attribute values and related names: the shape analysts
    export to a spreadsheet or CRM. Every cell keeps its trust state and source count."""
    statuses = LIVE if include_proposed else ("accepted",)
    q = select(Entity).where(Entity.graph_id == g.id, Entity.status.in_(statuses))
    if entity_type:
        q = q.where(Entity.type == entity_type)
    ents = list(s.scalars(q.order_by(Entity.name).limit(limit)))
    ids = [e.id for e in ents]
    if not ids:
        return S.TableOut(columns=[], link_columns=[], rows=[], total=0)
    claims = list(s.scalars(select(Claim).where(Claim.graph_id == g.id, Claim.entity_id.in_(ids),
                                                Claim.status.in_(("accepted", "contested", "proposed")))))
    ev = s.execute(select(ClaimEvidence.claim_id, Source.domain, Source.syndication_key, Source.id)
                   .join(Source, Source.id == ClaimEvidence.source_id)
                   .where(ClaimEvidence.graph_id == g.id, ClaimEvidence.stance == "supports",
                          ClaimEvidence.claim_id.in_([c.id for c in claims]))).all()
    indep: dict[str, set] = defaultdict(set)
    by_claim_sources: dict[str, set] = defaultdict(set)
    for cid, dom, key, sid in ev:
        indep[cid].add(key or dom)
        by_claim_sources[cid].add(sid)
    cells: dict[str, dict[str, S.TableCell]] = defaultdict(dict)
    ent_sources: dict[str, set] = defaultdict(set)
    ent_conf: dict[str, str] = {}
    rank = {"accepted": 2, "contested": 2, "proposed": 1}
    best: dict[tuple, Claim] = {}
    for c in claims:
        ent_sources[c.entity_id] |= by_claim_sources[c.id]
        if c.predicate == "exists":
            ent_conf[c.entity_id] = c.confidence
            continue
        if c.valid_to:
            continue
        k = (c.entity_id, c.predicate)
        cur = best.get(k)
        if cur is None or (rank[c.status], c.valid_from or "", c.observed_at) > (rank[cur.status], cur.valid_from or "", cur.observed_at):
            best[k] = c
    for (eid, pred), c in best.items():
        cells[eid][pred] = S.TableCell(value=c.value, status=c.status, confidence=c.confidence, valid_from=c.valid_from,
                                       sources=len(indep[c.id]))
    rels = list(s.scalars(select(Relationship).where(
        Relationship.graph_id == g.id, Relationship.status.in_(statuses),
        or_(Relationship.from_id.in_(ids), Relationship.to_id.in_(ids)))))
    other_ids = {r.from_id for r in rels} | {r.to_id for r in rels}
    names = dict(s.execute(select(Entity.id, Entity.name).where(Entity.id.in_(other_ids))).all()) if other_ids else {}
    links: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    degree: Counter = Counter()
    idset = set(ids)
    for r in rels:
        if r.valid_to and r.valid_to <= datetime.now().date().isoformat():
            label_out, label_in = f"{r.type} (past)", f"{r.type} (past) ←"
        else:
            label_out, label_in = r.type, f"{r.type} ←"
        if r.from_id in idset and r.to_id in names:
            links[r.from_id][label_out].append(names[r.to_id])
            degree[r.from_id] += 1
        if r.to_id in idset and r.from_id in names:
            links[r.to_id][label_in].append(names[r.from_id])
            degree[r.to_id] += 1
    order = [a["name"] for t in (g.ontology or {}).get("entity_types", []) if not entity_type or t["type"] == entity_type
             for a in t.get("attributes", [])]
    seen_cols = {p for d in cells.values() for p in d}
    fill = Counter(p for d in cells.values() for p in d)
    # ontology attributes first (in their declared order), then discovered ones, best-filled first
    columns = [c for c in dict.fromkeys(order) if c in seen_cols] + sorted(seen_cols - set(order), key=lambda c: (-fill[c], c))
    link_cols = [k for k, _ in Counter(k for d in links.values() for k in d).most_common(8)]
    rows = [S.TableRow(id=e.id, name=e.name, type=e.type, status=e.status, description=e.description,
                       connections=degree[e.id], sources=len(ent_sources[e.id]), confidence=ent_conf.get(e.id, "medium"),
                       first_seen_at=e.first_seen_at, last_verified_at=e.last_verified_at, cells=cells[e.id],
                       links={k: sorted(set(v)) for k, v in links[e.id].items()}) for e in ents]
    return S.TableOut(columns=columns, link_columns=link_cols, rows=rows, total=len(rows))
