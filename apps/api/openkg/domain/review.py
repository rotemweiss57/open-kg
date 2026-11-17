"""Deterministic graph mutations. Suggestions become graph facts only here, in one transaction."""
from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import Claim, ClaimEvidence, Entity, Graph, Relationship, Source, Suggestion
from .canonical import norm_name
from .events import audit


class ReviewError(Exception):
    pass


def _claims(s: Session, ids: list[str]) -> list[Claim]:
    return list(s.scalars(select(Claim).where(Claim.id.in_(ids)))) if ids else []


def _accept_claims(s: Session, ids: list[str]) -> None:
    now = utcnow()
    for c in _claims(s, ids):
        if c.status == "proposed":
            c.status, c.accepted_at = "accepted", now
        elif c.status == "contested" and c.accepted_at is None:
            c.accepted_at = now  # in the graph, but still flagged as disputed


def _accept_supported_claims(s: Session, graph: Graph, sug: Suggestion) -> None:
    """Policy approval of an entity covers its existence and only those attributes that meet the
    evidence bar themselves. The rest stay provisional as their own suggestions for a person."""
    from .canonical import sha
    from .recorder import pretty_value
    owner = s.get(Entity, sug.entity_id)
    keep, spun = [], []
    for c in _claims(s, sug.claim_ids):
        if c.predicate == "exists" or _well_supported(s, [c], owner):
            keep.append(c.id)
        elif c.status == "proposed":
            spun.append(c)
    _accept_claims(s, keep)
    ent = s.get(Entity, sug.entity_id)
    for c in spun:
        idem = sha("claim", c.claim_key)
        if s.scalar(select(Suggestion.id).where(Suggestion.graph_id == graph.id, Suggestion.idempotency_key == idem)):
            continue
        s.add(Suggestion(graph_id=graph.id, run_id=sug.run_id, kind="claim", confidence=c.confidence, risk="low",
                         title=f"{ent.name}: {c.predicate.replace('_', ' ')} = {pretty_value(c.value)}", entity_id=ent.id,
                         rationale="Single-source attribute left for review when the entity was approved on evidence.",
                         claim_ids=[c.id], idempotency_key=idem,
                         payload={"predicate": c.predicate, "value": c.value, "valid_from": c.valid_from}))
    sug.claim_ids = keep
    s.flush()


def _close(sug: Suggestion, status: str, actor: str | None, note: str) -> None:
    sug.status, sug.decided_by, sug.decided_at, sug.decision_note = status, actor, utcnow(), note


def _accept_entity(s: Session, graph: Graph, ent: Entity, actor: str | None, status: str, note: str) -> None:
    if ent.status != "proposed":
        return
    ent.status = "accepted"
    for sug in s.scalars(select(Suggestion).where(
            Suggestion.graph_id == graph.id, Suggestion.kind == "entity", Suggestion.entity_id == ent.id,
            Suggestion.status == "pending")):
        _accept_claims(s, sug.claim_ids)
        _close(sug, status, actor, note)


def decide(s: Session, graph: Graph, sug: Suggestion, action: str, *, actor_user_id: str | None = None,
           actor_kind: str = "user", note: str = "") -> Suggestion:
    """Apply approve/reject. Idempotent: deciding an already-decided suggestion is a no-op."""
    if sug.graph_id != graph.id:
        raise ReviewError("Suggestion does not belong to this graph")
    if action not in ("approve", "reject"):
        raise ReviewError("Unknown action")
    # Row-level claim so two concurrent reviewers cannot both apply the same suggestion.
    claimed = s.execute(update(Suggestion).where(Suggestion.id == sug.id, Suggestion.status == "pending")
                        .values(status="deciding")).rowcount
    if not claimed:
        s.refresh(sug)
        return sug
    s.refresh(sug)
    approved = "auto_approved" if actor_kind == "policy" else "approved"

    if action == "approve":
        if sug.kind == "entity":
            ent = s.get(Entity, sug.entity_id)
            if ent and ent.status == "proposed":
                ent.status = "accepted"
            if actor_kind == "policy":
                _accept_supported_claims(s, graph, sug)
            else:
                _accept_claims(s, sug.claim_ids)
        elif sug.kind == "relationship":
            rel = s.get(Relationship, sug.relationship_id)
            if rel is None:
                raise ReviewError("Relationship no longer exists")
            for eid in (rel.from_id, rel.to_id):  # an accepted edge needs accepted endpoints
                ent = s.get(Entity, eid)
                if ent.status in ("rejected", "merged"):
                    sug.status = "pending"
                    raise ReviewError(f"Endpoint '{ent.name}' is {ent.status}; cannot accept this relationship")
                _accept_entity(s, graph, ent, actor_user_id, approved, "Accepted together with a relationship.")
            rel.status = "accepted"
            _accept_claims(s, sug.claim_ids)
        elif sug.kind in ("claim", "contradiction"):
            _accept_claims(s, sug.claim_ids)
            new = _claims(s, sug.claim_ids)[:1]
            old = s.get(Claim, sug.target_claim_id) if sug.target_claim_id else None
            if old is not None and new:
                if sug.kind == "claim":  # dated change: old value becomes history
                    old.status, old.superseded_by_id = "superseded", new[0].id
                    old.valid_to = old.valid_to or new[0].valid_from
                else:  # reviewer sided with the new source
                    old.status, old.superseded_by_id = "retracted", new[0].id
        elif sug.kind == "merge":
            _merge(s, graph, sug)
        _close(sug, approved, actor_user_id, note)
    else:
        if sug.kind == "entity":
            ent = s.get(Entity, sug.entity_id)
            if ent and ent.status == "proposed":
                ent.status = "rejected"
                s.execute(update(Claim).where(Claim.entity_id == ent.id, Claim.status.in_(("proposed", "contested")))
                          .values(status="rejected"))
                rels = list(s.scalars(select(Relationship).where(
                    Relationship.graph_id == graph.id, Relationship.status == "proposed",
                    (Relationship.from_id == ent.id) | (Relationship.to_id == ent.id))))
                for rel in rels:
                    rel.status = "rejected"
                    s.execute(update(Claim).where(Claim.relationship_id == rel.id, Claim.status == "proposed")
                              .values(status="rejected"))
                dependents = select(Suggestion).where(
                    Suggestion.graph_id == graph.id, Suggestion.status == "pending", Suggestion.id != sug.id,
                    (Suggestion.entity_id == ent.id) | (Suggestion.merge_target_id == ent.id)
                    | Suggestion.relationship_id.in_([r.id for r in rels] or [""]))
                for dep in s.scalars(dependents):
                    _close(dep, "rejected", actor_user_id, f"Cascaded: entity '{ent.name}' was rejected.")
        elif sug.kind == "relationship":
            rel = s.get(Relationship, sug.relationship_id)
            if rel and rel.status == "proposed":
                rel.status = "rejected"
            s.execute(update(Claim).where(Claim.id.in_(sug.claim_ids or [""]), Claim.status == "proposed")
                      .values(status="rejected"))
        elif sug.kind in ("claim", "contradiction"):
            s.execute(update(Claim).where(Claim.id.in_(sug.claim_ids or [""]), Claim.status == "proposed")
                      .values(status="rejected"))
            old = s.get(Claim, sug.target_claim_id) if sug.target_claim_id else None
            if old is not None and old.status == "contested":
                # stays contested if another unresolved conflict targets it
                other = s.scalar(select(Suggestion.id).where(
                    Suggestion.graph_id == graph.id, Suggestion.target_claim_id == old.id,
                    Suggestion.status == "pending", Suggestion.id != sug.id))
                if not other:
                    owner = s.get(Entity, old.entity_id) if old.entity_id else None
                    old.status = "accepted" if owner is None or owner.status == "accepted" else "proposed"
        _close(sug, "rejected", actor_user_id, note)

    graph.updated_at = utcnow()
    audit(s, graph_id=graph.id, workspace_id=graph.workspace_id, actor_user_id=actor_user_id,
          actor_kind=actor_kind, action=f"suggestion.{sug.status}", target=f"suggestion:{sug.id}",
          detail={"kind": sug.kind, "title": sug.title, "note": note})
    s.flush()
    return sug


def _merge(s: Session, graph: Graph, sug: Suggestion) -> None:
    dup, target = s.get(Entity, sug.entity_id), s.get(Entity, sug.merge_target_id)
    if not dup or not target or dup.status in ("merged", "rejected") or target.status in ("merged", "rejected"):
        raise ReviewError("Merge is no longer applicable")
    if dup.status == "accepted" and target.status == "proposed":
        dup, target = target, dup  # keep the accepted node as the survivor
    # claims: move unless the target already holds the identical proposition
    for c in list(s.scalars(select(Claim).where(Claim.entity_id == dup.id))):
        twin = s.scalar(select(Claim).where(
            Claim.entity_id == target.id, Claim.predicate == c.predicate, Claim.value_norm == c.value_norm,
            Claim.valid_from.is_(c.valid_from) if c.valid_from is None else Claim.valid_from == c.valid_from))
        if twin:
            for ev in list(s.scalars(select(ClaimEvidence).where(ClaimEvidence.claim_id == c.id))):
                clash = s.scalar(select(ClaimEvidence.id).where(
                    ClaimEvidence.claim_id == twin.id, ClaimEvidence.source_id == ev.source_id,
                    ClaimEvidence.stance == ev.stance))
                if clash:
                    s.delete(ev)
                else:
                    ev.claim_id = twin.id
            c.status, c.superseded_by_id = "superseded", twin.id
        else:
            c.entity_id = target.id
    for rel in list(s.scalars(select(Relationship).where(
            Relationship.graph_id == graph.id, (Relationship.from_id == dup.id) | (Relationship.to_id == dup.id)))):
        nf = target.id if rel.from_id == dup.id else rel.from_id
        nt = target.id if rel.to_id == dup.id else rel.to_id
        twin = s.scalar(select(Relationship).where(
            Relationship.graph_id == graph.id, Relationship.id != rel.id, Relationship.type == rel.type,
            Relationship.from_id == nf, Relationship.to_id == nt, Relationship.status != "rejected"))
        if twin or nf == nt:
            rel.status = "rejected"
        else:
            rel.from_id, rel.to_id = nf, nt
    target.aliases = sorted((set(target.aliases or []) | set(dup.aliases or []) | {dup.name}) - {target.name})
    if not target.description:
        target.description = dup.description
    dup.status, dup.merged_into_id = "merged", target.id
    for other in s.scalars(select(Suggestion).where(
            Suggestion.graph_id == graph.id, Suggestion.status == "pending", Suggestion.id != sug.id,
            Suggestion.kind == "entity", Suggestion.entity_id == dup.id)):
        if target.status == "accepted":
            _accept_claims(s, other.claim_ids)
        _close(other, "superseded", None, f"Merged into '{target.name}'.")
    s.flush()


# --- evidence-gated auto-approval ----------------------------------------------------------------

POLICY_NOTE = ("Auto-approved on evidence: two or more independent sources with verified quotes, or the "
               "organisation's own site for a fact about itself.")


def _well_supported(s: Session, claims: list[Claim], entity: Entity | None = None) -> bool:
    """Evidence bar: two independent sources, or (for facts about an organisation itself) a verified
    quote from that organisation's own site."""
    from .evidence import MIN_INDEPENDENT_SOURCES, first_party_support, independent_support
    if not claims:
        return False
    for c in claims:
        if c.is_inferred:
            return False
        if independent_support(s, c.id) >= MIN_INDEPENDENT_SOURCES:
            continue
        if entity is not None and first_party_support(s, c.id, entity):
            continue
        return False
    return True


def qualifies_for_auto_approval(s: Session, sug: Suggestion) -> bool:
    """Only low-risk additions qualify. Changes, conflicts, merges and inferred links always wait
    for a person, however many sources they have."""
    if sug.status != "pending" or sug.risk != "low" or sug.kind not in ("entity", "relationship", "claim"):
        return False
    claims = _claims(s, sug.claim_ids)
    by_id = {c.id: c for c in claims}
    ordered = [by_id[i] for i in sug.claim_ids if i in by_id]
    if sug.kind == "entity":
        ent = s.get(Entity, sug.entity_id)
        return _well_supported(s, ordered[:1], ent)  # existence; attributes are judged one by one
    if sug.kind == "claim":
        ent = s.get(Entity, sug.entity_id) if sug.entity_id else None
        return ent is not None and ent.status == "accepted" and _well_supported(s, ordered, ent)
    if not _well_supported(s, ordered):  # relationships involve a second party: independent sources only
        return False
    rel = s.get(Relationship, sug.relationship_id)
    if rel is None:
        return False
    for eid in (rel.from_id, rel.to_id):  # an edge is only as trustworthy as its endpoints
        ent = s.get(Entity, eid)
        if ent.status == "accepted":
            continue
        exists = s.scalar(select(Claim).where(Claim.entity_id == eid, Claim.predicate == "exists"))
        if ent.status != "proposed" or exists is None or not _well_supported(s, [exists], ent):
            return False
    return True


def resolve_refinement_conflicts(s: Session, graph: Graph) -> int:
    """Close "conflicts" that are not conflicts.

    Same fact at two levels of detail ("Berlin" vs "Berlin, Germany", "CEO" vs "CEO & Co-Founder",
    "2012" vs "2012-02-09"): the claims are merged, keeping the more specific wording and all evidence.
    Two values that are both true (several degrees, founder and CEO): both stand, and the conflict
    becomes an ordinary suggestion to add the second value.
    """
    from .canonical import coexist, is_stage_predicate, is_title_predicate, same_fact, stage_rank, title_roles
    from .recorder import multi_valued, pretty_value
    n = 0
    for sug in list(s.scalars(select(Suggestion).where(
            Suggestion.graph_id == graph.id, Suggestion.kind == "contradiction", Suggestion.status == "pending"))):
        old = s.get(Claim, sug.target_claim_id) if sug.target_claim_id else None
        new = next(iter(_claims(s, sug.claim_ids)), None)
        if old is None or new is None:
            continue
        owner = s.get(Entity, old.entity_id) if old.entity_id else None
        settled = "accepted" if owner is None or owner.status == "accepted" else "proposed"
        better = same_fact(old.value_norm, new.value_norm, old.predicate)
        if better is None and is_title_predicate(old.predicate):
            ra, rb = title_roles(old.value_norm), title_roles(new.value_norm)
            better = "b" if ra < rb else "a" if rb <= ra else None
        ranks = (stage_rank(old.value_norm), stage_rank(new.value_norm)) if is_stage_predicate(old.predicate) else (None, None)
        staged = None not in ranks and ranks[0] != ranks[1]
        if better is None and not multi_valued(graph, old.predicate) and (
                staged or (old.valid_from and new.valid_from and old.valid_from != new.valid_from)):
            # Both values are now dated: this is a change over time, not a disagreement. The newer one
            # is proposed as the current value (still for a person to approve); the older is history.
            if staged:  # financing stages only move forward: the later stage is the current one
                newer, older = (new, old) if ranks[1] > ranks[0] else (old, new)
            else:
                newer, older = (new, old) if new.valid_from > old.valid_from else (old, new)
            for ev in list(s.scalars(select(ClaimEvidence).where(
                    ClaimEvidence.claim_id == old.id, ClaimEvidence.stance == "contradicts"))):
                s.delete(ev)
            if old.status == "contested":
                old.status = settled
            name = owner.name if owner else "Entity"
            label = old.predicate.replace("_", " ")
            if newer is new:
                sug.kind, sug.risk, sug.target_claim_id, sug.claim_ids = "claim", "elevated", older.id, [newer.id]
                sug.title = f"{name}: {label} changed to {pretty_value(newer.value)}"
                sug.rationale = (f"A later stage, “{newer.value}”, follows “{older.value}”." if staged else
                                 f"A newer source says “{newer.value}” (from {newer.valid_from}). The graph has “{older.value}”.")
                sug.payload = {"predicate": old.predicate, "change": "supersede",
                               "before": {"value": older.value, "valid_from": older.valid_from, "claim_id": older.id},
                               "after": {"value": newer.value, "valid_from": newer.valid_from, "claim_id": newer.id}}
            else:  # the value recorded second turned out to be the earlier one: keep it as history
                new.status, new.superseded_by_id = "superseded", old.id
                new.valid_to = new.valid_to or old.valid_from
                _close(sug, "superseded", None, "Not a conflict: this is an earlier dated value, kept as history.")
            audit(s, graph_id=graph.id, workspace_id=graph.workspace_id, actor_kind="system", action="suggestion.reclassified",
                  target=f"suggestion:{sug.id}", detail={"title": sug.title, "note": "Dated values are a change over time."})
            n += 1
            continue
        if better is None:
            if not (multi_valued(graph, old.predicate) or coexist(old.value_norm, new.value_norm, old.predicate)):
                continue  # a real disagreement: leave it for a person
            for ev in list(s.scalars(select(ClaimEvidence).where(
                    ClaimEvidence.claim_id == old.id, ClaimEvidence.stance == "contradicts"))):
                s.delete(ev)
            if old.status == "contested":
                others = s.scalar(select(Suggestion.id).where(
                    Suggestion.graph_id == graph.id, Suggestion.kind == "contradiction", Suggestion.status == "pending",
                    Suggestion.target_claim_id == old.id, Suggestion.id != sug.id))
                if not others:
                    old.status = settled
            name = owner.name if owner else "Entity"
            sug.kind, sug.risk, sug.target_claim_id = "claim", "low", None
            sug.title = f"{name}: {new.predicate.replace('_', ' ')} = {pretty_value(new.value)}"
            sug.rationale = ""
            sug.payload = {"predicate": new.predicate, "value": new.value, "valid_from": new.valid_from}
            audit(s, graph_id=graph.id, workspace_id=graph.workspace_id, actor_kind="system", action="suggestion.reclassified",
                  target=f"suggestion:{sug.id}", detail={"title": sug.title, "note": "Not a conflict: both values can be true."})
            n += 1
            continue
        if better == "b":
            taken = s.scalar(select(Claim.id).where(Claim.graph_id == graph.id, Claim.claim_key == new.claim_key, Claim.id != new.id))
            if not taken:
                old.value, old.value_norm = new.value, new.value_norm
        for ev in list(s.scalars(select(ClaimEvidence).where(ClaimEvidence.claim_id.in_((new.id, old.id))))):
            if ev.claim_id == old.id and ev.stance == "contradicts":
                s.delete(ev)  # it was never a contradiction
            elif ev.claim_id == new.id:
                clash = s.scalar(select(ClaimEvidence.id).where(
                    ClaimEvidence.claim_id == old.id, ClaimEvidence.source_id == ev.source_id, ClaimEvidence.stance == "supports"))
                if clash:
                    s.delete(ev)
                else:
                    ev.claim_id = old.id
        s.flush()
        new.status, new.superseded_by_id = "superseded", old.id
        if old.status == "contested":
            old.status = settled
        _close(sug, "superseded", None, "Not a conflict: one value only adds detail to the other. Merged automatically.")
        audit(s, graph_id=graph.id, workspace_id=graph.workspace_id, actor_kind="system", action="suggestion.superseded",
              target=f"suggestion:{sug.id}", detail={"kind": sug.kind, "title": sug.title, "note": sug.decision_note})
        n += 1
    return n


def reverify_evidence(s: Session, graph: Graph) -> int:
    """Re-check quotes that an earlier, stricter matcher marked as not found. A quote that is on the
    page word for word becomes verified, and confidence is recomputed from the evidence."""
    from .canonical import plain_words, quote_found
    from .evidence import independent_support
    rows = list(s.scalars(select(ClaimEvidence).where(
        ClaimEvidence.graph_id == graph.id, ClaimEvidence.excerpt_verified.is_(False))))
    pages: dict[str, str] = {}
    touched: set[str] = set()
    for ev in rows:
        if ev.source_id not in pages:
            src = s.get(Source, ev.source_id)
            pages[ev.source_id] = plain_words(f"{src.title}\n{src.content}") if src else ""
        if quote_found(pages[ev.source_id], ev.excerpt):
            ev.excerpt_verified, ev.strength = True, "strong"
            touched.add(ev.claim_id)
    s.flush()
    for claim in _claims(s, list(touched)):
        if claim.is_inferred or claim.confidence != "low":
            continue
        claim.confidence = "high" if independent_support(s, claim.id) >= 2 else "medium"
    s.flush()
    if touched:
        for sug in s.scalars(select(Suggestion).where(Suggestion.graph_id == graph.id, Suggestion.status == "pending",
                                                      Suggestion.confidence == "low")):
            head = s.get(Claim, sug.claim_ids[0]) if sug.claim_ids else None
            if head is not None and head.id in touched and not head.is_inferred:
                sug.confidence = head.confidence
    return len(touched)


def drop_off_subject_evidence(s: Session, graph: Graph) -> int:
    """Remove attribute evidence whose page never names the entity it was filed under (a model
    batching mistake). An attribute left with no evidence is retracted and leaves the review queue."""
    from .canonical import mentions, plain_words
    from .evidence import is_own_domain
    pages: dict[str, str] = {}
    retracted = 0
    rows = s.execute(select(ClaimEvidence, Claim, Entity).join(Claim, Claim.id == ClaimEvidence.claim_id)
                     .join(Entity, Entity.id == Claim.entity_id)
                     .where(ClaimEvidence.graph_id == graph.id, Claim.predicate != "exists",
                            Claim.status.in_(("proposed", "contested")), ClaimEvidence.stance == "supports")).all()
    hit: dict[str, Claim] = {}
    for ev, claim, ent in rows:
        src = s.get(Source, ev.source_id)
        if src is None:
            continue
        if src.id not in pages:
            pages[src.id] = plain_words(f"{src.title}\n{src.content}") + plain_words(src.url)
        if (mentions(pages[src.id], ent.name, tuple(ent.aliases or ()), person=ent.type.lower() == "person")
                or is_own_domain(src.domain, ent.canonical_key, ent.name, ent.type)):
            continue
        s.delete(ev)
        hit[claim.id] = claim
    s.flush()
    for claim in hit.values():
        if s.scalar(select(ClaimEvidence.id).where(ClaimEvidence.claim_id == claim.id, ClaimEvidence.stance == "supports")):
            continue
        claim.status = "retracted"
        retracted += 1
        for sug in s.scalars(select(Suggestion).where(Suggestion.graph_id == graph.id, Suggestion.status == "pending")):
            if claim.id not in (sug.claim_ids or []):
                continue
            if sug.kind == "entity":
                sug.claim_ids = [c for c in sug.claim_ids if c != claim.id]
            else:
                _close(sug, "superseded", None, "Withdrawn: the cited page never mentions this entity.")
                audit(s, graph_id=graph.id, workspace_id=graph.workspace_id, actor_kind="system",
                      action="suggestion.superseded", target=f"suggestion:{sug.id}",
                      detail={"kind": sug.kind, "title": sug.title, "note": sug.decision_note})
    return retracted


def merge_duplicate_organisations(s: Session, graph: Graph) -> int:
    """Repair for graphs recorded before identity resolution improved: a pending "possible duplicate"
    between two non-person entities with the same name and type is the same entity, unless each
    carries a different official domain. Two people with one name are merged only when they share
    context: cited from the same page, or tied to the same organisation."""
    n = 0
    for sug in list(s.scalars(select(Suggestion).where(
            Suggestion.graph_id == graph.id, Suggestion.kind == "merge", Suggestion.status == "pending"))):
        a = s.get(Entity, sug.entity_id) if sug.entity_id else None
        b = s.get(Entity, sug.merge_target_id) if sug.merge_target_id else None
        if a is None or b is None:
            continue
        if not all(e.status in ("proposed", "accepted") for e in (a, b)):
            _close(sug, "superseded", None, "One side was already merged or rejected.")
            continue
        if a.type != b.type or a.name_norm != b.name_norm:
            continue
        if a.type.lower() == "person":
            # People merge only on shared context: both cited from one page, tied to one organisation,
            # or one is cited from a page that names the other's organisation.
            from .canonical import mentions, plain_words
            from .recorder import Recorder
            rec = Recorder(s, graph)
            sa, sb = rec._evidence_sources(a.id), rec._evidence_sources(b.id)
            ca, cb = rec._context_names(a), rec._context_names(b)
            same_org = {norm_name(n, "company") for n in ca} & {norm_name(n, "company") for n in cb}

            def named_on(pages: set[str], context: set[str]) -> bool:
                for sid in pages:
                    src = s.get(Source, sid)
                    words = plain_words(f"{src.title}\n{src.content}") if src else ""
                    if any(mentions(words, name) for name in context):
                        return True
                return False

            if not (sa & sb or same_org or named_on(sa, cb) or named_on(sb, ca)):
                continue
        elif ":name:" not in a.canonical_key and ":name:" not in b.canonical_key:
            continue  # two different official identifiers: genuinely ambiguous, leave it to a person
        decide(s, graph, sug, "approve", actor_kind="system",
               note="Same name and shared context, recorded twice. Merged automatically.")
        n += 1
    return n


def apply_policy(s: Session, graph: Graph) -> list[Suggestion]:
    """Approve everything that currently meets the evidence bar. Explicit (graph setting), logged
    (audit actor = policy) and repeatable; accepting an entity can make its edges qualify."""
    resolve_refinement_conflicts(s, graph)
    if graph.autonomy_mode != "auto_accept_high":
        return []
    decided: list[Suggestion] = []
    for _ in range(6):
        pending = list(s.scalars(select(Suggestion).where(
            Suggestion.graph_id == graph.id, Suggestion.status == "pending", Suggestion.risk == "low",
            Suggestion.kind.in_(("entity", "relationship", "claim"))).order_by(Suggestion.created_at)))
        batch = [g for g in pending if qualifies_for_auto_approval(s, g)]
        if not batch:
            break
        for sug in batch:
            s.refresh(sug)
            if sug.status == "pending":
                decide(s, graph, sug, "approve", actor_kind="policy", note=POLICY_NOTE)
                decided.append(sug)
    return decided
