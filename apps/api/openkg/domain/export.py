"""Exports with provenance. Synthetic graphs are labelled as such inside the file."""
from __future__ import annotations

import csv
import io
import json
from xml.sax.saxutils import escape, quoteattr

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Claim, Entity, Graph, Relationship
from .read import claims_out
from .recorder import pretty_value


def _load(s: Session, g: Graph, include_proposed: bool = False):
    """Accepted facts by default. With include_proposed, unreviewed suggestions are added and
    every row says which is which, so nothing unreviewed can pass as a reviewed fact."""
    live = ("accepted", "proposed") if include_proposed else ("accepted",)
    ents = list(s.scalars(select(Entity).where(Entity.graph_id == g.id, Entity.status.in_(live)).order_by(Entity.name)))
    ids = {e.id for e in ents}
    rels = [r for r in s.scalars(select(Relationship).where(Relationship.graph_id == g.id, Relationship.status.in_(live)))
            if r.from_id in ids and r.to_id in ids]
    claims = claims_out(s, list(s.scalars(select(Claim).where(
        Claim.graph_id == g.id, Claim.status.in_(("accepted", "contested", "superseded", *live[1:]))))))
    raw = {c.id: c for c in s.scalars(select(Claim).where(Claim.graph_id == g.id))}
    return ents, rels, claims, raw


def _csv_safe(v) -> str:
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@") else v  # neutralise spreadsheet formulas


def _markdown(g: Graph, ents, rels, claims, raw, include_proposed: bool) -> str:
    """A cited report: one section per entity, every fact followed by its numbered sources."""
    refs: dict[str, int] = {}
    titles: dict[str, str] = {}

    def cite(c) -> str:
        nums = []
        for e in c.evidence:
            if e.stance != "supports":
                continue
            titles.setdefault(e.source.url, e.source.title or e.source.domain)
            nums.append(refs.setdefault(e.source.url, len(refs) + 1))
        return "".join(f"[{n}]" for n in sorted(set(nums)))

    def tag(status: str, confidence: str) -> str:
        bits = [] if status == "accepted" else [status]
        if confidence != "high":
            bits.append(f"{confidence} confidence")
        return f" *({', '.join(bits)})*" if bits else ""

    def when(a, b) -> str:
        return f" ({a or '?'} to {b or 'present'})" if a or b else ""

    by_entity: dict[str, list] = {}
    rel_claim: dict[str, object] = {}
    for c in claims:
        rc = raw[c.id]
        if rc.entity_id:
            by_entity.setdefault(rc.entity_id, []).append(c)
        elif rc.relationship_id:
            rel_claim.setdefault(rc.relationship_id, c)
    names = {e.id: e.name for e in ents}
    out = [f"# {g.name}", ""]
    if g.is_synthetic:
        out += ["> **SYNTHETIC DEMO DATA.** Fictional entities and fixture sources.", ""]
    question = (g.charter or {}).get("question") or g.description
    if question:
        out += [f"**Question:** {question}", ""]
    out += [f"{len(ents)} entities, {len(rels)} relationships. " + (
        "Includes suggestions that nobody has reviewed yet; each is marked *proposed*."
        if include_proposed else "Accepted facts only."),
        "Claims reflect what the cited sources stated when they were read. Coverage is a sample, not a complete list.", ""]
    for etype in sorted({e.type for e in ents}):
        out += [f"## {etype}", ""]
        bare = []
        for e in (x for x in ents if x.type == etype):
            exists = next((c for c in by_entity.get(e.id, []) if c.predicate == "exists"), None)
            facts = [c for c in by_entity.get(e.id, []) if c.predicate != "exists" and c.status != "superseded"]
            links = [r for r in rels if r.from_id == e.id]
            if not (e.description or facts or links):
                bare.append(e.name + ("" if e.status == "accepted" else " *(proposed)*"))
                continue
            out += [f"### {e.name}" + ("" if e.status == "accepted" else " *(proposed)*"), ""]
            if e.description:
                out += [e.description + (" " + cite(exists) if exists else ""), ""]
            for c in facts:
                out.append(f"- **{c.predicate.replace('_', ' ')}:** {pretty_value(c.value)}{when(c.valid_from, c.valid_to)}"
                           f"{tag(c.status, c.confidence)} {cite(c)}".rstrip())
            for r in links:
                c = rel_claim.get(r.id)
                out.append(f"- {r.type.replace('_', ' ').lower()} → **{names[r.to_id]}**{when(r.valid_from, r.valid_to)}"
                           f"{tag(r.status, c.confidence if c else 'low')} {cite(c) if c else ''}".rstrip())
            out.append("")
        if bare:
            out += [f"Named in sources, no further facts yet: {', '.join(bare)}.", ""]
    if refs:
        out += ["## Sources", ""] + [f"{n}. [{titles[u].strip() or u}]({u})" for u, n in sorted(refs.items(), key=lambda x: x[1])]
    return "\n".join(out) + "\n"


def export_graph(s: Session, g: Graph, fmt: str, include_proposed: bool = False) -> tuple[str, str, str]:
    ents, rels, claims, raw = _load(s, g, include_proposed)
    if fmt == "markdown":
        slug = "".join(c if c.isalnum() else "-" for c in g.name.lower()).strip("-")[:40] or "graph"
        return _markdown(g, ents, rels, claims, raw, include_proposed), "text/markdown", f"{slug}.md"
    names = {e.id: e.name for e in ents}
    slug = "".join(c if c.isalnum() else "-" for c in g.name.lower()).strip("-")[:40] or "graph"
    if fmt == "json":
        def cl(c):
            d = c.model_dump(mode="json")
            d["subject"] = {"entity_id": raw[c.id].entity_id, "relationship_id": raw[c.id].relationship_id}
            return d
        body = json.dumps({
            "openkg_export": 1, "graph": {"id": g.id, "name": g.name, "description": g.description,
                                          "synthetic": g.is_synthetic, "charter": g.charter, "ontology": g.ontology},
            "notice": "SYNTHETIC DEMO DATA — fictional entities and fixture sources." if g.is_synthetic else
                      "Claims reflect what cited sources stated at observation time; coverage is not complete.",
            "entities": [{"id": e.id, "type": e.type, "name": e.name, "aliases": e.aliases, "description": e.description,
                          "first_seen_at": e.first_seen_at.isoformat()} for e in ents],
            "relationships": [{"id": r.id, "type": r.type, "from": r.from_id, "to": r.to_id, "valid_from": r.valid_from,
                               "valid_to": r.valid_to} for r in rels],
            "claims": [cl(c) for c in claims]}, indent=1)
        return body, "application/json", f"{slug}.json"
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["subject", "subject_type", "predicate", "value", "object", "valid_from", "valid_to", "status",
                    "confidence", "independent_sources", "source_urls", "synthetic"])
        types = {e.id: e.type for e in ents}
        rel_by = {r.id: r for r in rels}
        for c in claims:
            rc = raw[c.id]
            urls = " | ".join(sorted({e.source.url for e in c.evidence if e.stance == "supports"}))
            if rc.entity_id in names:
                w.writerow([_csv_safe(x) for x in (names[rc.entity_id], types[rc.entity_id], c.predicate, c.value, "",
                                                   c.valid_from, c.valid_to, c.status, c.confidence,
                                                   c.independent_sources, urls, g.is_synthetic)])
            elif rc.relationship_id in rel_by:
                r = rel_by[rc.relationship_id]
                w.writerow([_csv_safe(x) for x in (names[r.from_id], types[r.from_id], r.type, "", names[r.to_id],
                                                   r.valid_from, r.valid_to, c.status, c.confidence,
                                                   c.independent_sources, urls, g.is_synthetic)])
        return buf.getvalue(), "text/csv", f"{slug}.csv"
    if fmt == "graphml":
        out = ['<?xml version="1.0" encoding="UTF-8"?>', '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
               '<key id="name" for="node" attr.name="name" attr.type="string"/>',
               '<key id="type" for="node" attr.name="type" attr.type="string"/>',
               '<key id="rel" for="edge" attr.name="type" attr.type="string"/>',
               '<key id="from" for="edge" attr.name="valid_from" attr.type="string"/>',
               '<key id="to" for="edge" attr.name="valid_to" attr.type="string"/>',
               '<key id="src" for="edge" attr.name="sources" attr.type="string"/>',
               f'<graph id={quoteattr(g.name)} edgedefault="directed">']
        ev = {raw[c.id].relationship_id: " | ".join(sorted({e.source.url for e in c.evidence})) for c in claims
              if raw[c.id].relationship_id}
        for e in ents:
            out.append(f'<node id="{e.id}"><data key="name">{escape(e.name)}</data><data key="type">{escape(e.type)}</data></node>')
        for r in rels:
            out.append(f'<edge id="{r.id}" source="{r.from_id}" target="{r.to_id}"><data key="rel">{escape(r.type)}</data>'
                       f'<data key="from">{escape(r.valid_from or "")}</data><data key="to">{escape(r.valid_to or "")}</data>'
                       f'<data key="src">{escape(ev.get(r.id, ""))}</data></edge>')
        out += ["</graph>", "</graphml>"]
        return "\n".join(out), "application/graphml+xml", f"{slug}.graphml"
    raise ValueError("format must be json, csv, graphml or markdown")
