from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ClaimEvidence, Source

MIN_INDEPENDENT_SOURCES = 2


def independent_support(s: Session, claim_id: str) -> int:
    """Number of independent sources carrying a verified supporting quote. Syndicated copies of
    one text count once; so do several pages from one domain."""
    rows = s.execute(
        select(Source.domain, Source.syndication_key).join(ClaimEvidence, ClaimEvidence.source_id == Source.id)
        .where(ClaimEvidence.claim_id == claim_id, ClaimEvidence.stance == "supports",
               ClaimEvidence.excerpt_verified.is_(True))).all()
    return len({(k or d) for d, k in rows})


_AFFIXES_PRE, _AFFIXES_POST = ("try", "get", "use", "join", "go"), ("hq", "app", "labs", "ai", "db", "io", "inc", "tech", "data")


def is_own_domain(domain: str, entity_key: str, name: str, entity_type: str) -> bool:
    """Is `domain` the entity's own official site? Either it is the recorded identifier, or (for
    organisations only) its main label is the organisation's name, optionally with a common
    affix: pinecone.io, trychroma.com, qdrant.tech. People never match."""
    domain = domain.lower().removeprefix("www.")
    if ":domain:" in entity_key:
        own = entity_key.split(":domain:", 1)[1]
        if domain == own or domain.endswith("." + own):
            return True
    if entity_type.lower() == "person":
        return False
    compact = re.sub(r"[^a-z0-9]", "", name.lower())
    compact = re.sub(r"(inc|labs|ltd|llc|corp|nv|as|gmbh)$", "", compact) or compact
    labels = domain.split(".")
    label = labels[-3] if len(labels) >= 3 and labels[-2] in ("co", "com") and len(labels[-1]) == 2 else labels[-2] if len(labels) >= 2 else ""
    if len(compact) < 4 or not label:
        return False
    return label == compact or any(label == p + compact for p in _AFFIXES_PRE) or any(label == compact + x for x in _AFFIXES_POST)


def first_party_support(s: Session, claim_id: str, entity) -> bool:
    """True when a verified supporting quote comes from the entity's own official site. An
    organisation's own site is sufficient evidence for facts about itself; it is never used for
    relationships, which involve a second party."""
    domains = s.scalars(
        select(Source.domain).join(ClaimEvidence, ClaimEvidence.source_id == Source.id)
        .where(ClaimEvidence.claim_id == claim_id, ClaimEvidence.stance == "supports",
               ClaimEvidence.excerpt_verified.is_(True)))
    return any(is_own_domain(d, entity.canonical_key, entity.name, entity.type) for d in domains)
