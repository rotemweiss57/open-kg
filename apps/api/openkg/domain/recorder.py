"""The only write path research engines have: validated candidates -> reviewable suggestions.

Engines (fixture or Deep Agent) hand over Pydantic-validated candidates. This module resolves
identity deterministically, verifies that cited excerpts really occur in retrieved content,
derives confidence from evidence (never trusting the model's self-assessment upward), detects
duplicates / changes / contradictions and writes *proposed* rows plus a suggestion.
Everything is idempotent: replaying the same candidate after a crash is a no-op.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import Claim, ClaimEvidence, Entity, Graph, Relationship, Source, Suggestion
from .canonical import (
    DESCRIPTIVE,
    canonical_predicate,
    canonical_url,
    coexist,
    core_name,
    domain_of,
    entity_key,
    is_stage_predicate,
    is_title_predicate,
    mentions,
    norm_name,
    norm_text,
    norm_value,
    plain_words,
    quote_found,
    same_fact,
    sha,
    slug,
    stage_rank,
    title_roles,
    valid_date,
)
from .evidence import is_own_domain

Confidence = Literal["high", "medium", "low"]
_RANK = {"low": 0, "medium": 1, "high": 2}
MAX_CONTENT = 20_000


# --- candidate contracts (also the JSON schema the LLM tools expose) -------------------------


class EvidenceRef(BaseModel):
    url: str = Field(description="URL of a source that was already retrieved in this run")
    excerpt: str = Field(max_length=700, description="Short verbatim quote from the page supporting the fact")
    stance: Literal["supports", "contradicts"] = "supports"


class CandidateClaim(BaseModel):
    predicate: str = Field(max_length=80, description="Attribute name, e.g. title, hq_location, funding_stage")
    value: str | int | float | bool
    valid_from: str | None = Field(default=None, description="When the fact became true (YYYY, YYYY-MM or YYYY-MM-DD)")
    valid_to: str | None = None
    confidence: Confidence = "medium"
    inferred: bool = Field(default=False, description="True if this is a hypothesis not directly stated by a source")
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=6)

    @field_validator("valid_from", "valid_to")
    @classmethod
    def _date(cls, v):
        return valid_date(v)

    @field_validator("predicate")
    @classmethod
    def _pred(cls, v):
        return slug(v).replace("-", "_")


class CandidateEntity(BaseModel):
    type: str = Field(max_length=60)
    name: str = Field(min_length=1, max_length=300)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    description: str = Field(default="", max_length=600)
    identifiers: dict[str, str] = Field(default_factory=dict, description="Strong ids: domain, profile_url")
    disambiguator: str | None = Field(default=None, description="For people: current organization, to tell namesakes apart")
    confidence: Confidence = "medium"
    rationale: str = Field(default="", max_length=600, description="Why this entity belongs in the graph")
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=6)
    claims: list[CandidateClaim] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _clean_name(self):
        name, qualifier = split_name_qualifier(self.name)
        if qualifier and self.type.lower() == "person":
            self.name, self.disambiguator = name, self.disambiguator or qualifier
        return self


_PAREN_SUFFIX = re.compile(r"^(.{2,}?)\s*\(([^()]{2,60})\)\s*$")


def split_name_qualifier(name: str) -> tuple[str, str | None]:
    """'Jane Doe (Acme)' -> ('Jane Doe', 'Acme'). Models often append the organisation to a name;
    it belongs in the disambiguator, not in the display name."""
    m = _PAREN_SUFFIX.match(name.strip())
    return (m.group(1).strip(), m.group(2).strip()) if m else (name.strip(), None)


class EntityRef(BaseModel):
    type: str
    name: str
    identifiers: dict[str, str] = Field(default_factory=dict)
    disambiguator: str | None = None

    @model_validator(mode="after")
    def _clean_name(self):
        name, qualifier = split_name_qualifier(self.name)
        if qualifier and self.type.lower() == "person":
            self.name, self.disambiguator = name, self.disambiguator or qualifier
        return self


class CandidateRelationship(BaseModel):
    type: str = Field(max_length=60, description="Relationship type from the ontology, e.g. WORKS_AT")
    source: EntityRef
    target: EntityRef
    valid_from: str | None = None
    valid_to: str | None = None
    confidence: Confidence = "medium"
    inferred: bool = False
    rationale: str = Field(default="", max_length=600)
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=6)

    @field_validator("valid_from", "valid_to")
    @classmethod
    def _date(cls, v):
        return valid_date(v)

    @field_validator("type")
    @classmethod
    def _type(cls, v):
        return slug(v).replace("-", "_").upper()


@dataclass
class RecordResult:
    status: str  # created | corroborated | skipped
    message: str = ""
    entity_id: str | None = None
    relationship_id: str | None = None
    suggestion_ids: list[str] = field(default_factory=list)
    events: list[tuple[str, dict]] = field(default_factory=list)
    unverified_quotes: list[str] = field(default_factory=list)  # fed back to the agent so it can re-quote


# --- sources ------------------------------------------------------------------------------------


def _syndication_key(text: str) -> str:
    """Near-identical bodies (a press release re-published) share a key => not independent."""
    body = re.sub(r"[^a-z0-9 ]", "", norm_text(text))[:600]
    return sha(body)[:32] if len(body) > 120 else ""


def register_source(s: Session, graph_id: str, *, url: str, title: str = "", content: str = "",
                    published_at: str | None = None, synthetic: bool = False, meta: dict | None = None) -> Source:
    curl = canonical_url(url)
    src = s.scalar(select(Source).where(Source.graph_id == graph_id, Source.canonical_url == curl))
    content = (content or "")[:MAX_CONTENT]
    if src is None:
        src = Source(graph_id=graph_id, url=url, canonical_url=curl, domain=domain_of(url) or curl.split(":", 1)[0],
                     is_synthetic=synthetic, meta=meta or {})
        s.add(src)
    if title:
        src.title = title[:500]
    if content and len(content) >= len(src.content or ""):
        src.content = content
        src.snippet = content[:400]
        src.content_hash = sha(norm_text(content))
        src.syndication_key = _syndication_key(content)
    if valid_date(published_at):
        src.published_at = valid_date(published_at)
    src.fetched_at = utcnow()
    s.flush()
    return src


def refines(a: str, b: str) -> bool:
    """True when `b` says the same thing as `a` with more detail: "amsterdam" -> "amsterdam, netherlands"."""
    return a != b and same_fact(a, b) == "b" and same_fact(b, a) != "b"


def pretty_value(value: object) -> str:
    """Readable value for suggestion titles: 50000000 -> 50M."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    n = abs(value)
    if n >= 1e9:
        return f"{value / 1e9:.2f}".rstrip("0").rstrip(".") + "B"
    if n >= 1e6:
        return f"{value / 1e6:.2f}".rstrip("0").rstrip(".") + "M"
    return str(int(value)) if float(value).is_integer() else str(value)


def _excerpt_in(content: str, excerpt: str) -> bool:
    return quote_found(plain_words(content), excerpt)


# Nouns that, as the last word of an attribute name, mean "one of several": product, key_customer, lead_investor.
_PLURAL_NOUNS = frozenset({
    "product", "investor", "founder", "customer", "partner", "competitor", "subsidiary", "office", "award",
    "language", "skill", "certification", "degree", "publication", "patent", "acquisition", "executive", "member",
    "service", "market", "industry", "technology", "integration", "feature", "client", "supplier", "brand",
    "advisor", "author", "alias", "tag", "backer", "plant", "facility", "site", "segment", "vertical", "category",
    "owner", "shareholder", "stakeholder", "sponsor", "licensee", "country", "region", "model", "program",
})


# Attribute names that are really one end of a relationship: (relationship type, is the entity the source?).
_ROLE_LINKS: dict[str, tuple[str, bool]] = {
    **dict.fromkeys(("investor", "investors", "lead_investor", "lead_investors", "backer", "backers", "funded_by",
                     "backed_by"), ("INVESTED_IN", False)),
    **dict.fromkeys(("founder", "founders", "co_founder", "co_founders", "cofounder", "cofounders", "founded_by"),
                    ("FOUNDED", False)),
    **dict.fromkeys(("employer", "current_employer", "works_for", "current_company"), ("WORKS_AT", True)),
    **dict.fromkeys(("previous_employer", "previous_employers", "previous_company", "previous_companies",
                     "former_employer", "former_employers"), ("PREVIOUSLY_AT", True)),
    **dict.fromkeys(("supplier", "suppliers", "supplied_by"), ("SUPPLIES", False)),
    **dict.fromkeys(("customer", "customers", "supplies_to"), ("SUPPLIES", True)),
    **dict.fromkeys(("operator", "operated_by"), ("OPERATES", False)),
    **dict.fromkeys(("partner", "partners"), ("PARTNERS_WITH", True)),
}
# "Accel; GV, and Sequoia Capital" is three names; "Acme Co., Ltd." is one.
_NAME_LIST = re.compile(r";|,(?!\s*(?:inc|ltd|llc|corp|co|plc|gmbh|sa|ag|nv)\b)\s*(?:and\s+)?|\s+and\s+", re.I)


# Verbs that take many objects: a plant "produces" several drugs; none of them contradicts another.
_PLURAL_VERBS = frozenset({"produces", "manufactures", "makes", "sells", "offers", "uses", "supports", "serves",
                           "includes", "develops", "owns", "operates", "supplies", "distributes", "licenses",
                           "funds", "sponsors", "acquired", "partners", "competes", "treats", "targets"})


def multi_valued(graph: Graph, predicate: str) -> bool:
    """Can this attribute hold several values at once? Then different values are separate facts."""
    if predicate in MULTI_VALUED or predicate in set((graph.ontology or {}).get("multi_valued_predicates", [])):
        return True
    if predicate.startswith(("previous_", "former_", "past_", "other_", "office_", "additional_")):
        return True
    if predicate.split("_", 1)[0] in _PLURAL_VERBS:  # produces, produces_api_for, sells, uses ...
        return True
    last = predicate.rsplit("_", 1)[-1]
    singular = last[:-3] + "y" if last.endswith("ies") else last[:-1] if last.endswith("s") and not last.endswith("ss") else last
    return singular in _PLURAL_NOUNS


_ORG_TYPES = re.compile(r"compan|organi[sz]ation|investor|fund|firm|vendor|supplier|customer|institution|agency|"
                        r"startup|corporat|business|employer|bank|universit|manufacturer|partner|competitor|acquirer")


def _org_like(entity_type: str) -> bool:
    return bool(_ORG_TYPES.search(entity_type.lower()))


# Attributes that naturally hold several values at once. Different values are separate facts, never a conflict.
MULTI_VALUED = frozenset({
    "education", "degrees", "investors", "founders", "products", "awards", "languages", "customers", "partners",
    "competitors", "subsidiaries", "offices", "certifications", "skills", "aliases", "tags", "publications",
    "patents", "acquisitions", "funding_rounds", "key_people", "executives", "board_members", "board_memberships",
    "services", "markets", "industries", "technologies", "integrations", "features", "use_cases", "locations",
    "previously_at", "previous_companies", "previous_employers", "previous_roles", "past_roles", "former_employers",
    "notable_customers", "key_products", "brands", "advisors", "members", "authors", "clients", "suppliers",
})


# --- recorder -----------------------------------------------------------------------------------


class Recorder:
    def __init__(self, s: Session, graph: Graph, run_id: str | None = None):
        self.s, self.graph, self.run_id = s, graph, run_id
        self.unverified: list[str] = []  # quotes not found in retrieved text during this recorder's lifetime
        self.off_subject: list[str] = []  # evidence dropped because its page never names the subject
        self._words: dict[str, str] = {}

    def _page_words(self, src: Source) -> str:
        if src.id not in self._words:
            self._words[src.id] = plain_words(f"{src.title}\n{src.content}")
        return self._words[src.id]

    def _on_subject(self, resolved: list[tuple[Source, EvidenceRef, bool]], *subjects: Entity | CandidateEntity,
                    ) -> list[tuple[Source, EvidenceRef, bool]]:
        """Keep only evidence whose page names every subject (or is the subject's own site). A quote
        about one person filed under another is the commonest way a model corrupts a graph."""
        kept = []
        for src, ref, ok in resolved:
            missing = None
            for subj in subjects:
                key = getattr(subj, "canonical_key", None) or entity_key(subj.type, subj.name, getattr(subj, "identifiers", None),
                                                                         getattr(subj, "disambiguator", None))
                words = self._page_words(src) + plain_words(src.url)
                if not (mentions(words, subj.name, tuple(subj.aliases or ()), person=subj.type.lower() == "person")
                        or is_own_domain(src.domain, key, subj.name, subj.type)):
                    missing = subj.name
                    break
            if missing:
                self.off_subject.append(f"{missing} is not mentioned on {src.url}")
            else:
                kept.append((src, ref, ok))
        return kept

    # evidence -------------------------------------------------------------------------------

    def _resolve_evidence(self, refs: list[EvidenceRef], quiet: bool = False) -> list[tuple[Source, EvidenceRef, bool]]:
        out = []
        for ref in refs:
            src = self.s.scalar(select(Source).where(
                Source.graph_id == self.graph.id, Source.canonical_url == canonical_url(ref.url)))
            if src is None:
                continue  # citing a page that was never retrieved is not evidence
            ok = quote_found(self._page_words(src), ref.excerpt)
            if not ok and not quiet:
                self.unverified.append(ref.excerpt[:90])
            out.append((src, ref, ok))
        return out

    @staticmethod
    def _assess(resolved: list[tuple[Source, EvidenceRef, bool]], claimed: Confidence, inferred: bool) -> Confidence:
        """Confidence is computed from evidence; the model can only lower it."""
        verified = [(src, ref) for src, ref, ok in resolved if ok and ref.stance == "supports"]
        independent = {(src.syndication_key or src.domain) for src, _ in verified}
        if inferred or not verified:
            computed: Confidence = "low"
        elif len(independent) >= 2:
            computed = "high"
        else:
            computed = "medium"
        return computed if _RANK[computed] <= _RANK[claimed] else claimed

    def _attach(self, claim: Claim, resolved: list[tuple[Source, EvidenceRef, bool]]) -> int:
        added = 0
        fresh = not self.s.scalar(select(ClaimEvidence.id).where(ClaimEvidence.claim_id == claim.id).limit(1))
        for src, ref, ok in resolved:
            exists = self.s.scalar(select(ClaimEvidence.id).where(
                ClaimEvidence.claim_id == claim.id, ClaimEvidence.source_id == src.id,
                ClaimEvidence.stance == ref.stance))
            if exists:
                continue
            self.s.add(ClaimEvidence(graph_id=self.graph.id, claim_id=claim.id, source_id=src.id,
                                     excerpt=ref.excerpt, excerpt_verified=ok, stance=ref.stance,
                                     strength="strong" if ok else "weak"))
            added += 1
        self.s.flush()
        # Confidence follows the evidence as it accumulates across calls: a claim first recorded with a
        # quote that could not be verified becomes medium once a verified quote arrives, and high once
        # two independent sources carry one. The label a reviewer sees is then the one the policy uses.
        if not claim.is_inferred and claim.confidence != "high":
            support = self._independent_support(claim.id)
            # On first recording the model may have chosen a lower confidence than the evidence allows;
            # that choice stands. Only later, independent corroboration lifts a low claim to medium.
            lifted = "high" if support >= 2 else "medium" if support == 1 and not fresh else claim.confidence
            if lifted == "high" and claim.confidence == "low" and fresh:
                lifted = claim.confidence
            if _RANK[lifted] > _RANK[claim.confidence]:
                claim.confidence = lifted
                for sug in self.s.scalars(select(Suggestion).where(
                        Suggestion.graph_id == self.graph.id, Suggestion.status == "pending",
                        Suggestion.kind.in_(("entity", "relationship", "claim")))):
                    if sug.claim_ids and sug.claim_ids[0] == claim.id and _RANK[lifted] > _RANK.get(sug.confidence, 0):
                        sug.confidence = lifted
        return added

    def _independent_support(self, claim_id: str) -> int:
        from .evidence import independent_support
        return independent_support(self.s, claim_id)

    # suggestions ----------------------------------------------------------------------------

    def _suggest(self, *, kind: str, key: str, title: str, rationale: str, confidence: str, payload: dict,
                 risk: str = "low", **links) -> Suggestion | None:
        idem = sha(kind, key)
        existing = self.s.scalar(select(Suggestion).where(
            Suggestion.graph_id == self.graph.id, Suggestion.idempotency_key == idem))
        if existing:
            return None
        sug = Suggestion(graph_id=self.graph.id, run_id=self.run_id, kind=kind, title=title, rationale=rationale,
                         confidence=confidence, risk=risk, payload=payload, idempotency_key=idem, **links)
        self.s.add(sug)
        self.s.flush()
        return sug

    # entities -------------------------------------------------------------------------------

    def find_entity(self, ref: EntityRef | CandidateEntity) -> Entity | None:
        key = entity_key(ref.type, ref.name, ref.identifiers, ref.disambiguator)
        ent = self.s.scalar(select(Entity).where(Entity.graph_id == self.graph.id, Entity.canonical_key == key))
        person = ref.type.lower() == "person"
        if ent is None and not person:
            # Anything that is not a person is one node per name and type. The only thing that keeps
            # two same-named organisations apart is a different official domain on each.
            nn = norm_name(ref.name, ref.type)
            same = list(self.s.scalars(select(Entity).where(
                Entity.graph_id == self.graph.id, Entity.name_norm == nn, Entity.type == ref.type,
                Entity.status.in_(("proposed", "accepted"))).order_by(Entity.first_seen_at)))
            if ref.identifiers and (ref.identifiers.get("domain") or ref.identifiers.get("profile_url")):
                same = [e for e in same if ":name:" in e.canonical_key]  # never one with a different domain
            if same:
                ent = next((e for e in same if e.status == "accepted"), same[0])
        if ent is None and not person:
            # "Notion" and "Notion Labs", "Sequoia" and "Sequoia Capital": names that differ only by a
            # generic suffix are one organisation when exactly one such node exists and it does not
            # carry a different official domain.
            core = core_name(norm_name(ref.name, ref.type))
            near = [e for e in self.s.scalars(select(Entity).where(
                Entity.graph_id == self.graph.id, Entity.status.in_(("proposed", "accepted")))
                .order_by(Entity.first_seen_at))
                if e.type.lower() != "person" and (e.type == ref.type or (_org_like(e.type) and _org_like(ref.type)))
                and core_name(norm_name(e.name, e.type)) == core]
            if ref.identifiers and (ref.identifiers.get("domain") or ref.identifiers.get("profile_url")):
                near = [e for e in near if ":name:" in e.canonical_key]
            if len(near) == 1:
                ent = near[0]
        if ent is None and person:
            # Same name plus shared context is the same person: the candidate's page is one this person
            # is already cited from, or it names the organisation they are tied to. A name alone is
            # never enough, so namesakes with nothing in common still become a "possible duplicate".
            nn = norm_name(ref.name, ref.type)
            same = [e for e in self.s.scalars(select(Entity).where(
                Entity.graph_id == self.graph.id, Entity.name_norm == nn, Entity.type == ref.type,
                Entity.status.in_(("proposed", "accepted"))).order_by(Entity.first_seen_at))
                if self._shares_context(ref, e)]
            if len(same) == 1:
                ent = same[0]
        if ent is None and person and not ref.identifiers:
            # A person referred to without strong ids may resolve to exactly one live person of that name.
            nn = norm_name(ref.name, ref.type)
            matches = list(self.s.scalars(select(Entity).where(
                Entity.graph_id == self.graph.id, Entity.name_norm == nn, Entity.type == ref.type,
                Entity.status.in_(("proposed", "accepted")))))
            if len(matches) == 1 and (not ref.disambiguator or isinstance(ref, EntityRef)):
                ent = matches[0]
        if ent is None and _org_like(ref.type):
            # One firm is one node: "Notable Capital" the Investor and "Notable Capital" the Company
            # are the same organisation, whichever type a model picked first.
            nn = norm_name(ref.name, "company")
            matches = [e for e in self.s.scalars(select(Entity).where(
                Entity.graph_id == self.graph.id, Entity.type != ref.type, Entity.status.in_(("proposed", "accepted"))))
                if _org_like(e.type) and norm_name(e.name, "company") == nn]
            if len(matches) == 1:
                ent = matches[0]
        hops = 0
        while ent is not None and ent.status == "merged" and ent.merged_into_id and hops < 5:
            ent = self.s.get(Entity, ent.merged_into_id)
            hops += 1
        return ent

    def _context_names(self, ent: Entity) -> set[str]:
        """Organisations a person is tied to: their disambiguator and the far end of their links."""
        names = set()
        if "@" in ent.canonical_key:
            names.add(ent.canonical_key.rsplit("@", 1)[1].replace("-", " "))
        linked = self.s.scalars(select(Entity.name).join(Relationship, Relationship.to_id == Entity.id).where(
            Relationship.graph_id == self.graph.id, Relationship.from_id == ent.id, Relationship.status != "rejected"))
        return {n for n in (*names, *linked) if len(n) >= 3}

    def _evidence_sources(self, entity_id: str) -> set[str]:
        return set(self.s.scalars(select(ClaimEvidence.source_id).join(Claim, Claim.id == ClaimEvidence.claim_id).where(
            Claim.entity_id == entity_id)))

    def _shares_context(self, ref: EntityRef | CandidateEntity, existing: Entity) -> bool:
        context = self._context_names(existing)
        own = getattr(ref, "disambiguator", None)
        theirs = existing.canonical_key.rsplit("@", 1)[1] if "@" in existing.canonical_key else None
        if own and theirs:
            # Both sides say which organisation they mean. Different organisations are different
            # people ("Alex Kim of BMW", "Alex Kim of Volkswagen"), even when one page names both.
            return bool(set(slug(norm_name(own, "company")).split("-")) & set(theirs.split("-")) - {"the", "of", "and"})
        if own and any(norm_name(own, "company") == norm_name(c, "company") for c in context):
            return True
        pages = [src for src, _, _ in self._resolve_evidence(list(getattr(ref, "evidence", None) or []), quiet=True)]
        if not pages:
            return False
        if {p.id for p in pages} & self._evidence_sources(existing.id):
            return True
        return any(mentions(self._page_words(p), name) for p in pages for name in context)

    def record_entity(self, cand: CandidateEntity, discovered_via: str = "") -> RecordResult:
        resolved = self._resolve_evidence(cand.evidence)
        if not resolved:
            return RecordResult("skipped", "No cited source has been retrieved in this graph; read the page first.")
        resolved = self._on_subject(resolved, cand)
        if not resolved:
            return RecordResult("skipped", f"None of the cited pages mentions '{cand.name}'. Cite a page that names it.")
        ent = self.find_entity(cand)
        res = RecordResult("corroborated")
        if ent is not None and ent.status == "rejected":
            return RecordResult("skipped", f"'{cand.name}' was previously rejected by a reviewer; not re-proposed.",
                                entity_id=ent.id)
        confidence = self._assess(resolved, cand.confidence, False)
        if ent is None:
            key = entity_key(cand.type, cand.name, cand.identifiers, cand.disambiguator)
            ent = Entity(graph_id=self.graph.id, type=cand.type, canonical_key=key, name=cand.name.strip(),
                         name_norm=norm_name(cand.name, cand.type),
                         aliases=sorted({a.strip() for a in cand.aliases if a.strip()}),
                         description=cand.description, discovered_run_id=self.run_id,
                         discovered_via=discovered_via or cand.rationale)
            try:
                with self.s.begin_nested():
                    self.s.add(ent)
                    self.s.flush()
            except IntegrityError:  # concurrent subagent created it first
                ent = self.s.scalar(select(Entity).where(
                    Entity.graph_id == self.graph.id, Entity.canonical_key == key))
                assert ent is not None
            else:
                res.status = "created"
        res.entity_id = ent.id
        exists_claim, _ = self._upsert_claim("entity", ent.id, "exists", True, None, None, confidence, False)
        self._attach(exists_claim, resolved)
        ent.last_verified_at = utcnow()

        claim_ids = [exists_claim.id]
        if res.status == "created":
            sug = self._suggest(
                kind="entity", key=ent.canonical_key, title=f"Add {cand.type}: {ent.name}",
                rationale=cand.rationale or discovered_via, confidence=confidence, entity_id=ent.id,
                claim_ids=claim_ids, payload={"type": cand.type, "name": ent.name, "description": cand.description})
            if sug:
                res.suggestion_ids.append(sug.id)
                res.events.append(("candidate.entity", {
                    "suggestion_id": sug.id, "entity": {"id": ent.id, "name": ent.name, "type": ent.type,
                                                         "status": ent.status}, "confidence": confidence,
                    "rationale": sug.rationale}))
            self._propose_merges(ent, res)
        else:
            new_aliases = {a.strip() for a in (*cand.aliases, cand.name) if a.strip()} - set(ent.aliases or []) - {ent.name}
            if new_aliases:
                ent.aliases = sorted(set(ent.aliases or []) | new_aliases)
            if cand.description and not ent.description:
                ent.description = cand.description

        for c in cand.claims:
            self.record_claim(ent, c, res)
        self._maybe_auto_accept(res)
        return res

    def _propose_merges(self, ent: Entity, res: RecordResult) -> None:
        names = {ent.name_norm} | {norm_name(a, ent.type) for a in ent.aliases or []}
        others = self.s.scalars(select(Entity).where(
            Entity.graph_id == self.graph.id, Entity.id != ent.id, Entity.type == ent.type,
            Entity.status.in_(("proposed", "accepted"))))
        for other in others:
            other_names = {other.name_norm} | {norm_name(a, other.type) for a in other.aliases or []}
            if not names & other_names:
                # "Coatue" and "Coatue Management": probably one firm, but only a person can say so
                if ent.type.lower() == "person" or core_name(ent.name_norm) != core_name(other.name_norm):
                    continue
            pair = "|".join(sorted([ent.id, other.id]))
            sug = self._suggest(
                kind="merge", key=pair, risk="elevated", confidence="low", entity_id=ent.id,
                merge_target_id=other.id, title=f"Possible duplicate: {ent.name} ↔ {other.name}",
                rationale="Same name and type, different identifiers." if names & other_names
                else "The names differ only by a generic word.",
                payload={"duplicate": {"id": ent.id, "name": ent.name, "key": ent.canonical_key},
                         "target": {"id": other.id, "name": other.name, "key": other.canonical_key}})
            if sug:
                res.suggestion_ids.append(sug.id)
                res.events.append(("candidate.merge", {"suggestion_id": sug.id, "title": sug.title}))

    # claims ---------------------------------------------------------------------------------

    def _upsert_claim(self, kind: str, subject_id: str, predicate: str, value, valid_from, valid_to,
                      confidence: str, inferred: bool) -> tuple[Claim, bool]:
        vn = norm_value(value)
        key = sha(kind, subject_id, predicate, vn, valid_from or "")
        claim = self.s.scalar(select(Claim).where(Claim.graph_id == self.graph.id, Claim.claim_key == key))
        if claim:
            if _RANK[confidence] > _RANK[claim.confidence]:
                claim.confidence = confidence
            claim.verified_at = utcnow()
            if valid_to and not claim.valid_to:
                claim.valid_to = valid_to
            return claim, False
        claim = Claim(graph_id=self.graph.id, subject_kind=kind, predicate=predicate, value=value, value_norm=vn,
                      claim_key=key, valid_from=valid_from, valid_to=valid_to, confidence=confidence,
                      is_inferred=inferred, run_id=self.run_id,
                      entity_id=subject_id if kind == "entity" else None,
                      relationship_id=subject_id if kind == "relationship" else None)
        self.s.add(claim)
        self.s.flush()
        return claim, True

    def _adopt_date(self, claim: Claim, valid_from: str | None, valid_to: str | None) -> bool:
        """One continuing fact seen with two start dates ("CFO since 2018-11", "CFO as of 2026-08")
        keeps the more precise date, or failing that the earlier one. Returns False when the dates
        describe a separate, closed period (a second term in the same office), which is its own fact."""
        old, new = claim.valid_from or "", valid_from or ""
        if old == new:
            return True
        precise = old.startswith(new) or new.startswith(old)  # 2024 and 2024-08-05: one date, two precisions
        if not precise and (claim.valid_to or valid_to):
            return False
        if len(new) > len(old) if precise else new < old:
            key = sha("entity", claim.entity_id, claim.predicate, claim.value_norm, new)
            if not self.s.scalar(select(Claim.id).where(Claim.graph_id == self.graph.id, Claim.claim_key == key)):
                claim.valid_from, claim.claim_key = valid_from, key
        return True

    def _attributes(self, entity_type: str) -> frozenset[str]:
        for t in (self.graph.ontology or {}).get("entity_types", []):
            if t.get("type") == entity_type:
                return frozenset(a.get("name", "") for a in t.get("attributes", []))
        return frozenset()

    def _multi_valued(self, predicate: str) -> bool:
        return multi_valued(self.graph, predicate)

    def _relationship_for(self, predicate: str) -> tuple[dict, bool | None] | None:
        """The ontology relationship an attribute name really is, and its direction if the name fixes
        it: "works_at" is the type itself; "investor" is the far end of INVESTED_IN."""
        types = {r.get("type", ""): r for r in (self.graph.ontology or {}).get("relationship_types", [])}
        for name, r in types.items():
            if name.lower() == predicate:
                return r, None
        alias = _ROLE_LINKS.get(predicate)
        if alias and alias[0] in types:
            return types[alias[0]], alias[1]
        return None

    def _claim_as_relationship(self, ent: Entity, c: CandidateClaim, rtype: dict, hint: bool | None,
                               res: RecordResult) -> RecordResult:
        """A model wrote a link as an attribute ("works_at: Datadog", "investors: Accel; GV"). Record
        the link instead, so the graph has an edge rather than a string that can contradict one."""
        names = [n.strip(" .") for n in _NAME_LIST.split(str(c.value)) if len(n.strip(" .")) >= 2][:12]
        for name in names or [str(c.value)]:
            self._link(ent, c.model_copy(update={"value": name}), rtype, hint, res)
        return res

    def _link(self, ent: Entity, c: CandidateClaim, rtype: dict, hint: bool | None, res: RecordResult) -> None:
        src_type, dst_type = rtype.get("source") or ent.type, rtype.get("target") or ent.type
        # "X invested_in Y" reads subject to object, so `ent` is normally the source end. The exception:
        # a model sometimes names the link from the object's side ("plant operates: Northvolt"). Flip
        # only when the types make that unambiguous and the named entity is already known as the source.
        forward = True if hint is None else hint
        if hint is None and ent.type == dst_type != src_type:
            known = self.find_entity(EntityRef(type=src_type, name=str(c.value)))
            forward = not (known is not None and known.type == src_type)
        other_type = dst_type if forward else src_type
        refs = [EvidenceRef(url=e.url, excerpt=e.excerpt, stance=e.stance) for e in c.evidence]
        other = CandidateEntity(type=other_type, name=str(c.value), evidence=refs)
        made = self.record_entity(other, discovered_via=f"Linked from {ent.name} ({c.predicate.replace('_', ' ')})")
        me, them = EntityRef(type=ent.type, name=ent.name), EntityRef(type=other_type, name=other.name)
        link = self.record_relationship(CandidateRelationship(
            type=rtype["type"], source=me if forward else them, target=them if forward else me,
            valid_from=c.valid_from, valid_to=c.valid_to, confidence=c.confidence, inferred=c.inferred, evidence=refs))
        for part in (made, link):
            res.suggestion_ids += part.suggestion_ids
            res.events += part.events
        if link.status == "skipped" and not res.message:
            res.message = link.message
        elif link.status == "created" and res.status == "corroborated":
            res.status = "created"

    def record_claim(self, ent: Entity, c: CandidateClaim, res: RecordResult | None = None) -> RecordResult:
        res = res or RecordResult("corroborated", entity_id=ent.id)
        # One name per attribute: "founding_year" and "year_founded" are the ontology's "founded_year".
        c = c.model_copy(update={"predicate": canonical_predicate(c.predicate, self._attributes(ent.type))})
        if c.predicate in DESCRIPTIVE and isinstance(c.value, str):
            # A description is prose about the entity, not a fact to version or dispute.
            if len(c.value) > len(ent.description or "") and self._on_subject(self._resolve_evidence(c.evidence), ent):
                ent.description = c.value[:600]
            return res
        if isinstance(c.value, str) and (link := self._relationship_for(c.predicate)):
            return self._claim_as_relationship(ent, c, link[0], link[1], res)
        resolved = self._resolve_evidence(c.evidence)
        if not resolved:
            return res
        resolved = self._on_subject(resolved, ent)
        if not resolved:
            if not res.message:
                res.message = f"skipped {c.predicate}: the cited page never mentions {ent.name}"
            return res
        confidence = self._assess(resolved, c.confidence, c.inferred)
        vn = norm_value(c.value)
        # The same value seen once with a date and once without is one fact; keep the more precise date.
        for other in self.s.scalars(select(Claim).where(
                Claim.graph_id == self.graph.id, Claim.entity_id == ent.id, Claim.predicate == c.predicate,
                Claim.value_norm == vn, Claim.status.in_(("accepted", "contested", "proposed")))):
            if (other.valid_from or "") == (c.valid_from or "") or not self._adopt_date(other, c.valid_from, c.valid_to):
                continue
            self._attach(other, resolved)
            other.verified_at = utcnow()
            return res
        # A value that only adds or drops detail ("Berlin" vs "Berlin, Germany", "2012" vs "2012-02-09")
        # is the same fact: corroborate the existing claim and keep the more specific wording, never
        # raise a conflict.
        if not isinstance(c.value, bool) and not self._multi_valued(c.predicate):
            vn = norm_value(c.value)
            for other in self.s.scalars(select(Claim).where(
                    Claim.graph_id == self.graph.id, Claim.entity_id == ent.id, Claim.predicate == c.predicate,
                    Claim.status.in_(("accepted", "contested", "proposed")), Claim.value_norm != vn)):
                better = same_fact(other.value_norm, vn, c.predicate)
                if better is None and is_title_predicate(c.predicate):
                    # "CEO" and "CEO & Co-Founder" are one title at two levels of detail
                    ra, rb = title_roles(other.value_norm), title_roles(vn)
                    better = "b" if ra < rb else "a" if rb <= ra else None
                if better is None:
                    continue
                if better == "b":  # adopt the more specific wording, and its identity key with it
                    key = sha("entity", ent.id, c.predicate, vn, other.valid_from or "")
                    if not self.s.scalar(select(Claim.id).where(Claim.graph_id == self.graph.id, Claim.claim_key == key)):
                        other.value, other.value_norm, other.claim_key = c.value, vn, key
                self._adopt_date(other, c.valid_from, c.valid_to)
                self._attach(other, resolved)
                other.verified_at = utcnow()
                if other.confidence == "medium" and not other.is_inferred and self._independent_support(other.id) >= 2:
                    other.confidence = "high"
                return res
        claim, created = self._upsert_claim("entity", ent.id, c.predicate, c.value, c.valid_from, c.valid_to,
                                            confidence, c.inferred)
        self._attach(claim, resolved)
        if not created:
            # corroboration may lift confidence once independent sources accumulate
            if claim.confidence == "medium" and not claim.is_inferred and self._independent_support(claim.id) >= 2:
                claim.confidence = "high"
            return res
        if res.status == "corroborated":
            res.status = "created"

        prior = None
        # A value with an end date is a closed chapter of history ("VP Technology, 2002 to 2010"): it
        # cannot contradict or replace whatever is true now.
        if not self._multi_valued(c.predicate) and not c.valid_to:
            prior = self.s.scalar(select(Claim).where(
                Claim.graph_id == self.graph.id, Claim.entity_id == ent.id, Claim.predicate == c.predicate,
                Claim.status.in_(("accepted", "contested", "proposed")), Claim.id != claim.id,
                Claim.value_norm != claim.value_norm,
                Claim.valid_to.is_(None)).order_by(Claim.observed_at.desc()))
            if prior is not None and coexist(prior.value_norm, claim.value_norm, c.predicate):
                prior = None  # a founder who is also CEO holds both; neither replaces the other
        staged = None
        if prior is not None and is_stage_predicate(c.predicate):
            old_rank, new_rank = stage_rank(prior.value_norm), stage_rank(claim.value_norm)
            if old_rank is not None and new_rank is not None and old_rank != new_rank:
                staged = new_rank > old_rank
                if not staged:
                    # An earlier financing stage than the one on record is history, not a rival value.
                    claim.status, claim.superseded_by_id = "superseded", prior.id
                    return res

        ev = {"entity": {"id": ent.id, "name": ent.name}, "predicate": c.predicate, "value": c.value,
              "valid_from": c.valid_from, "confidence": confidence}
        if prior is None:
            pending_entity = self.s.scalar(select(Suggestion).where(
                Suggestion.graph_id == self.graph.id, Suggestion.kind == "entity",
                Suggestion.entity_id == ent.id, Suggestion.status == "pending"))
            if pending_entity is not None:
                pending_entity.claim_ids = [*pending_entity.claim_ids, claim.id]  # folded into the entity review
                return res
            sug = self._suggest(
                kind="claim", key=claim.claim_key, title=f"{ent.name}: {c.predicate.replace('_', ' ')} = {pretty_value(c.value)}",
                rationale="", confidence=confidence,
                entity_id=ent.id, claim_ids=[claim.id], payload={"predicate": c.predicate, "value": c.value,
                                                                 "valid_from": c.valid_from})
            kind = "new"
        else:
            later = staged or bool(c.valid_from and (not prior.valid_from or c.valid_from > prior.valid_from))
            before = {"value": prior.value, "valid_from": prior.valid_from, "claim_id": prior.id}
            after = {"value": c.value, "valid_from": c.valid_from, "claim_id": claim.id}
            if later:
                sug = self._suggest(
                    kind="claim", key=claim.claim_key, risk="elevated", confidence=confidence, entity_id=ent.id,
                    claim_ids=[claim.id], target_claim_id=prior.id,
                    title=f"{ent.name}: {c.predicate.replace('_', ' ')} changed to {pretty_value(c.value)}",
                    rationale=f"A newer source says “{c.value}” (from {c.valid_from}). The graph has “{prior.value}”.",
                    payload={"predicate": c.predicate, "change": "supersede", "before": before, "after": after})
                kind = "changed"
            else:
                prior.status = "contested"
                for src, ref, ok in resolved:  # the conflicting page is also evidence *against* the old claim
                    if not self.s.scalar(select(ClaimEvidence.id).where(
                            ClaimEvidence.claim_id == prior.id, ClaimEvidence.source_id == src.id,
                            ClaimEvidence.stance == "contradicts")):
                        self.s.add(ClaimEvidence(graph_id=self.graph.id, claim_id=prior.id, source_id=src.id,
                                                 excerpt=ref.excerpt, excerpt_verified=ok, stance="contradicts",
                                                 strength="strong" if ok else "weak"))
                sug = self._suggest(
                    kind="contradiction", key=claim.claim_key, risk="elevated", confidence=confidence,
                    entity_id=ent.id, claim_ids=[claim.id], target_claim_id=prior.id,
                    title=f"Conflict on {ent.name}: {c.predicate.replace('_', ' ')}",
                    rationale=f"The graph has “{prior.value}”. Another source says “{c.value}”, with no dates to explain it.",
                    payload={"predicate": c.predicate, "change": "contradiction", "before": before, "after": after})
                kind = "contradicted"
                if not res.message:
                    res.message = (f"{c.predicate} conflicts with the recorded value “{prior.value}”. If the sources date "
                                   f"either value, record it again with valid_from so it becomes history, not a conflict")
            ev.update(before=before, after=after)
        if sug:
            res.suggestion_ids.append(sug.id)
            res.events.append(("candidate.claim", {**ev, "suggestion_id": sug.id, "change": kind,
                                                   "title": sug.title}))
        return res

    # relationships --------------------------------------------------------------------------

    def record_relationship(self, cand: CandidateRelationship) -> RecordResult:
        resolved = self._resolve_evidence(cand.evidence)
        if not resolved:
            return RecordResult("skipped", "No cited source has been retrieved in this graph; read the page first.")
        a, b = self.find_entity(cand.source), self.find_entity(cand.target)
        missing = [r.name for r, e in ((cand.source, a), (cand.target, b)) if e is None]
        if missing:
            return RecordResult("skipped", f"Unknown entities: {', '.join(missing)}. Record them as candidate "
                                           f"entities (with evidence) before linking them.")
        assert a and b
        if a.id == b.id:
            return RecordResult("skipped", "Self-relationships are not recorded.")
        resolved = self._on_subject(resolved, a, b)
        if not resolved:
            return RecordResult("skipped", f"No cited page mentions both '{a.name}' and '{b.name}'. A link needs a "
                                           f"source that names both ends.")
        if "rejected" in (a.status, b.status):
            return RecordResult("skipped", "An endpoint was rejected by a reviewer.")
        allowed = {r["type"] for r in (self.graph.ontology or {}).get("relationship_types", [])}
        if allowed and cand.type not in allowed:
            return RecordResult("skipped", f"Relationship type {cand.type} is not in this graph's ontology "
                                           f"({', '.join(sorted(allowed))}).")
        confidence = self._assess(resolved, cand.confidence, cand.inferred)
        dedup = sha(cand.type, a.id, b.id, cand.valid_from or "")[:40]
        rel = self.s.scalar(select(Relationship).where(
            Relationship.graph_id == self.graph.id, Relationship.dedup_key == dedup))
        if rel is None and not cand.valid_from:  # undated restatement of an already-known dated edge
            rel = self.s.scalar(select(Relationship).where(
                Relationship.graph_id == self.graph.id, Relationship.type == cand.type,
                Relationship.from_id == a.id, Relationship.to_id == b.id,
                Relationship.status.in_(("proposed", "accepted"))))
        res = RecordResult("corroborated")
        if rel is not None and rel.status == "rejected":
            return RecordResult("skipped", "This relationship was previously rejected by a reviewer.",
                                relationship_id=rel.id)
        if rel is None:
            rel = Relationship(graph_id=self.graph.id, from_id=a.id, to_id=b.id, type=cand.type, dedup_key=dedup,
                               valid_from=cand.valid_from, valid_to=cand.valid_to, discovered_run_id=self.run_id)
            self.s.add(rel)
            self.s.flush()
            res.status = "created"
        elif cand.valid_to and not rel.valid_to:
            rel.valid_to = cand.valid_to
        rel.last_verified_at = utcnow()
        res.relationship_id = rel.id
        claim, _ = self._upsert_claim("relationship", rel.id, cand.type, f"{a.name} → {b.name}", rel.valid_from,
                                      rel.valid_to, confidence, cand.inferred)
        self._attach(claim, resolved)
        if res.status == "created":
            title = f"{a.name} —{cand.type.replace('_', ' ').lower()}→ {b.name}"
            sug = self._suggest(
                kind="relationship", key=dedup, title=title, rationale=cand.rationale, confidence=confidence,
                relationship_id=rel.id, claim_ids=[claim.id], risk="elevated" if cand.inferred else "low",
                payload={"type": cand.type, "from": {"id": a.id, "name": a.name}, "to": {"id": b.id, "name": b.name},
                         "valid_from": rel.valid_from, "valid_to": rel.valid_to, "inferred": cand.inferred})
            if sug:
                res.suggestion_ids.append(sug.id)
                res.events.append(("candidate.relationship", {
                    "suggestion_id": sug.id, "confidence": confidence, "title": title,
                    "relationship": {"id": rel.id, "type": rel.type, "from_id": a.id, "to_id": b.id,
                                     "from_name": a.name, "to_name": b.name, "status": rel.status}}))
        elif claim.confidence == "medium" and not claim.is_inferred and self._independent_support(claim.id) >= 2:
            claim.confidence = "high"
        self._maybe_auto_accept(res)
        return res

    # policy ---------------------------------------------------------------------------------

    def _maybe_auto_accept(self, res: RecordResult) -> None:
        """Evidence-gated auto-approval (see review.apply_policy). Runs after every recording, so a
        suggestion is promoted as soon as corroboration from a later source makes it qualify."""
        from .review import apply_policy  # local import: review depends on recorder types

        for sug in apply_policy(self.s, self.graph):
            res.events.append(("suggestion.decided", {"suggestion_id": sug.id, "status": sug.status, "title": sug.title,
                                                      "entity_id": sug.entity_id,
                                                      "relationship_id": sug.relationship_id, "by": "policy"}))


def lookup_entities(s: Session, graph_id: str, query: str, limit: int = 10) -> list[Entity]:
    q = f"%{norm_text(query)}%"
    return list(s.scalars(select(Entity).where(
        Entity.graph_id == graph_id, Entity.status.in_(("proposed", "accepted")),
        or_(Entity.name_norm.like(q), Entity.canonical_key.like(q))).limit(limit)))
