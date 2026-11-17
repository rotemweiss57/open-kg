"""Trust rules: identity, evidence verification, independence, contradictions, review."""
import pytest
from sqlalchemy import func, select

from openkg.db import session_scope
from openkg.domain.canonical import canonical_url, entity_key, is_public_http_url, norm_name
from openkg.domain.recorder import (
    CandidateClaim,
    CandidateEntity,
    CandidateRelationship,
    Recorder,
    register_source,
)
from openkg.domain.review import decide
from openkg.models import AuditEvent, Claim, ClaimEvidence, Entity, Graph, Relationship, Suggestion

TEXT_A = "Acme Robotics is a robotics company headquartered in Denver, Colorado. Jane Doe is VP of Product at Acme Robotics since January 2025."
TEXT_B = "Jane Doe joined Acme Robotics as VP of Product in January 2025, the company said in a blog post about its roadmap and hiring plans for the year."


def ev(url, excerpt, stance="supports"):
    return {"url": url, "excerpt": excerpt, "stance": stance}


@pytest.fixture
def g(make_graph):
    gid = make_graph(None, ontology={"entity_types": [{"type": "Company", "attributes": [{"name": "hq_location"}]},
                                                        {"type": "Person", "attributes": [{"name": "title"}]}],
                                     "relationship_types": [{"type": "WORKS_AT"}]})
    with session_scope() as s:
        register_source(s, gid, url="https://news.example.com/a?utm_source=x", title="A", content=TEXT_A)
        register_source(s, gid, url="https://blog.example.org/b", title="B", content=TEXT_B)
    return gid


def acme(**kw):
    return CandidateEntity(type="Company", name="Acme Robotics, Inc.", identifiers={"domain": "https://www.acme.example"},
                           evidence=[ev("https://news.example.com/a", "Acme Robotics is a robotics company headquartered in Denver, Colorado")], **kw)


def jane(url="https://news.example.com/a", excerpt="Jane Doe is VP of Product at Acme Robotics", disamb="Acme Robotics", **kw):
    return CandidateEntity(type="Person", name="Jane Doe", disambiguator=disamb, evidence=[ev(url, excerpt)], **kw)


def test_canonicalization_is_deterministic_and_cautious():
    assert canonical_url("HTTP://WWW.Example.com/a/?utm_source=x&b=1#frag") == "https://example.com/a?b=1"
    assert norm_name("Acme Robotics, Inc.", "Company") == "acme robotics"
    assert entity_key("Company", "Acme", {"domain": "https://www.acme.com/about"}) == "company:domain:acme.com"
    # two people who share a name but not an organization never share a key
    assert entity_key("Person", "Jane Doe", None, "Acme") != entity_key("Person", "Jane Doe", None, "Globex")


@pytest.mark.parametrize("url,ok", [
    ("https://example.com/x", True), ("http://localhost:8000", False), ("http://127.0.0.1/", False),
    ("http://169.254.169.254/latest/meta-data", False), ("http://10.0.0.5/", False), ("file:///etc/passwd", False),
    ("https://user:pw@example.com", False), ("http://intranet/", False), ("http://service.internal/", False),
    ("http://2130706433/", False), ("javascript:alert(1)", False), ("http://[::1]/", False)])
def test_ssrf_guard(url, ok):
    assert is_public_http_url(url) is ok


def test_recording_is_idempotent_and_creates_reviewable_suggestion(g):
    with session_scope() as s:
        rec = Recorder(s, s.get(Graph, g))
        first = rec.record_entity(acme())
        again = rec.record_entity(acme())
        assert first.status == "created" and again.status == "corroborated"
        assert s.scalar(select(func.count()).select_from(Entity)) == 1
        assert s.scalar(select(func.count()).select_from(Suggestion)) == 1
        ent = s.scalar(select(Entity))
        assert ent.status == "proposed"  # a suggested entity is not an accepted entity


def test_uncited_or_unretrieved_evidence_is_not_evidence(g):
    with session_scope() as s:
        rec = Recorder(s, s.get(Graph, g))
        res = rec.record_entity(CandidateEntity(type="Company", name="Ghost Co", evidence=[ev("https://never-fetched.example/x", "Ghost Co exists")]))
        assert res.status == "skipped" and s.scalar(select(func.count()).select_from(Entity)) == 0


def test_excerpt_not_in_page_caps_confidence_low(g):
    with session_scope() as s:
        rec = Recorder(s, s.get(Graph, g))
        rec.record_entity(CandidateEntity(type="Company", name="Acme Robotics", confidence="high",
                                          evidence=[ev("https://news.example.com/a", "Acme Robotics was acquired by Evil Corp for $9 billion")]))
        claim = s.scalar(select(Claim))
        assert claim.confidence == "low"
        assert s.scalar(select(ClaimEvidence.excerpt_verified)) is False


def test_confidence_needs_independent_sources_and_syndication_does_not_count(g):
    with session_scope() as s:
        graph = s.get(Graph, g)
        register_source(s, g, url="https://mirror.example.net/b-copy", title="B copy", content=TEXT_B)  # verbatim copy
        rec = Recorder(s, graph)
        rec.record_entity(jane())
        rec.record_entity(jane(url="https://blog.example.org/b", excerpt="Jane Doe joined Acme Robotics as VP of Product in January 2025"))
        exists = s.scalar(select(Claim).where(Claim.predicate == "exists"))
        assert exists.confidence == "high"  # two different organisations
    with session_scope() as s:
        rec = Recorder(s, s.get(Graph, g))
        c = CandidateClaim(predicate="title", value="VP of Product", confidence="high", evidence=[
            ev("https://blog.example.org/b", "Jane Doe joined Acme Robotics as VP of Product in January 2025"),
            ev("https://mirror.example.net/b-copy", "Jane Doe joined Acme Robotics as VP of Product in January 2025")])
        rec.record_claim(s.scalar(select(Entity)), c)
        assert s.scalar(select(Claim).where(Claim.predicate == "title")).confidence == "medium"  # a copy is one source


def test_namesakes_are_never_merged_automatically(g):
    with session_scope() as s:
        graph = s.get(Graph, g)
        rec = Recorder(s, graph)
        rec.record_entity(jane())
        rec.record_entity(jane(disamb="Globex"))
        assert s.scalar(select(func.count()).select_from(Entity)) == 2
        merge = s.scalar(select(Suggestion).where(Suggestion.kind == "merge"))
        assert merge is not None and merge.risk == "elevated"
        decide(s, graph, merge, "approve", note="same person")
        statuses = sorted(e.status for e in s.scalars(select(Entity)))
        assert statuses == ["merged", "proposed"]


def test_relationship_requires_known_entities_and_ontology_type(g):
    with session_scope() as s:
        rec = Recorder(s, s.get(Graph, g))
        rel = dict(source={"type": "Person", "name": "Jane Doe", "disambiguator": "Acme Robotics"},
                   target={"type": "Company", "name": "Acme Robotics"},
                   evidence=[ev("https://news.example.com/a", "Jane Doe is VP of Product at Acme Robotics")])
        assert rec.record_relationship(CandidateRelationship(type="WORKS_AT", **rel)).status == "skipped"  # endpoints unknown
        rec.record_entity(acme())
        rec.record_entity(jane())
        assert "ontology" in rec.record_relationship(CandidateRelationship(type="MARRIED_TO", **rel)).message
        assert rec.record_relationship(CandidateRelationship(type="WORKS_AT", valid_from="2025-01", **rel)).status == "created"
        assert rec.record_relationship(CandidateRelationship(type="WORKS_AT", valid_from="2025-01", **rel)).status == "corroborated"
        assert s.scalar(select(func.count()).select_from(Relationship)) == 1


def _accepted_acme(s, graph):
    rec = Recorder(s, graph)
    res = rec.record_entity(acme(claims=[CandidateClaim(predicate="hq_location", value="Denver, Colorado", evidence=[
        ev("https://news.example.com/a", "headquartered in Denver, Colorado")])]))
    decide(s, graph, s.get(Suggestion, res.suggestion_ids[0]), "approve")
    return rec, s.get(Entity, res.entity_id)


def test_contradiction_marks_claim_contested_and_keeps_both_sides(g):
    with session_scope() as s:
        graph = s.get(Graph, g)
        rec, ent = _accepted_acme(s, graph)
        register_source(s, g, url="https://registry.example.gov/acme", content="Registered office of Acme Robotics: Boulder, Colorado. Filed with the state registry.")
        rec.record_claim(ent, CandidateClaim(predicate="hq_location", value="Boulder, Colorado", evidence=[
            ev("https://registry.example.gov/acme", "Registered office of Acme Robotics: Boulder, Colorado")]))
        old = s.scalar(select(Claim).where(Claim.value_norm == "denver, colorado"))
        assert old.status == "contested"  # not silently presented as certain
        assert s.scalar(select(func.count()).where(ClaimEvidence.claim_id == old.id, ClaimEvidence.stance == "contradicts")) == 1
        sug = s.scalar(select(Suggestion).where(Suggestion.kind == "contradiction"))
        decide(s, graph, sug, "reject", note="registry is a mailing address")
        assert old.status == "accepted"
        assert s.scalar(select(Claim).where(Claim.value_norm == "boulder, colorado")).status == "rejected"
        # the rejected claim and its evidence are retained for audit
        assert s.scalar(select(func.count()).select_from(ClaimEvidence)) >= 3
        assert s.scalar(select(func.count()).where(AuditEvent.action == "suggestion.rejected")) == 1


def test_dated_change_supersedes_and_preserves_history(g):
    with session_scope() as s:
        graph = s.get(Graph, g)
        rec = Recorder(s, graph)
        res = rec.record_entity(jane(claims=[CandidateClaim(predicate="title", value="VP of Product", valid_from="2025-01", evidence=[
            ev("https://news.example.com/a", "Jane Doe is VP of Product at Acme Robotics since January 2025")])]))
        decide(s, graph, s.get(Suggestion, res.suggestion_ids[0]), "approve")
        register_source(s, g, url="https://news.example.com/promo", content="Acme Robotics has promoted Jane Doe to Chief Product Officer, effective March 2026, the company announced.")
        ent = s.get(Entity, res.entity_id)
        rec.record_claim(ent, CandidateClaim(predicate="title", value="Chief Product Officer", valid_from="2026-03", evidence=[
            ev("https://news.example.com/promo", "promoted Jane Doe to Chief Product Officer, effective March 2026")]))
        sug = s.scalar(select(Suggestion).where(Suggestion.kind == "claim"))
        assert sug.payload["change"] == "supersede" and sug.risk == "elevated"
        old = s.scalar(select(Claim).where(Claim.value_norm == "vp of product"))
        assert old.status == "accepted"  # a dated change is not a contradiction
        decide(s, graph, sug, "approve")
        assert (old.status, old.valid_to) == ("superseded", "2026-03")
        assert s.scalar(select(Claim).where(Claim.value_norm == "chief product officer")).status == "accepted"


def test_decisions_are_idempotent_and_reject_cascades(g):
    with session_scope() as s:
        graph = s.get(Graph, g)
        rec = Recorder(s, graph)
        rec.record_entity(acme())
        j = rec.record_entity(jane())
        rel = rec.record_relationship(CandidateRelationship(
            type="WORKS_AT", source={"type": "Person", "name": "Jane Doe", "disambiguator": "Acme Robotics"},
            target={"type": "Company", "name": "Acme Robotics"}, evidence=[ev("https://news.example.com/a", "Jane Doe is VP of Product at Acme Robotics")]))
        sug = s.get(Suggestion, j.suggestion_ids[0])
        decide(s, graph, sug, "reject", note="out of scope")
        decide(s, graph, sug, "approve")  # second decision is a no-op
        assert sug.status == "rejected"
        assert s.get(Suggestion, rel.suggestion_ids[0]).status == "rejected"  # dependent edge cascaded
        assert s.get(Relationship, rel.relationship_id).status == "rejected"
        assert rec.record_entity(jane()).status == "skipped"  # rejected entities are not re-proposed


def test_approving_relationship_accepts_its_endpoints(g):
    with session_scope() as s:
        graph = s.get(Graph, g)
        rec = Recorder(s, graph)
        rec.record_entity(acme())
        rec.record_entity(jane())
        rel = rec.record_relationship(CandidateRelationship(
            type="WORKS_AT", source={"type": "Person", "name": "Jane Doe", "disambiguator": "Acme Robotics"},
            target={"type": "Company", "name": "Acme Robotics"}, evidence=[ev("https://news.example.com/a", "Jane Doe is VP of Product at Acme Robotics")]))
        decide(s, graph, s.get(Suggestion, rel.suggestion_ids[0]), "approve")
        assert {e.status for e in s.scalars(select(Entity))} == {"accepted"}
        assert s.scalar(select(func.count()).where(Suggestion.status == "pending")) == 0


def test_auto_accept_policy_is_explicit_logged_and_conservative(g):
    with session_scope() as s:
        graph = s.get(Graph, g)
        graph.autonomy_mode = "auto_accept_high"
        rec = Recorder(s, graph)
        one = rec.record_entity(acme())  # single source: stays pending
        assert s.get(Suggestion, one.suggestion_ids[0]).status == "pending"
        both = CandidateEntity(type="Person", name="Jane Doe", disambiguator="Acme Robotics", evidence=[
            ev("https://news.example.com/a", "Jane Doe is VP of Product at Acme Robotics"),
            ev("https://blog.example.org/b", "Jane Doe joined Acme Robotics as VP of Product in January 2025")])
        res = rec.record_entity(both)
        sug = s.get(Suggestion, res.suggestion_ids[0])
        assert sug.status == "auto_approved" and "independent sources" in sug.decision_note
        assert s.scalar(select(AuditEvent.actor_kind).where(AuditEvent.action == "suggestion.auto_approved")) == "policy"


def test_later_corroboration_triggers_auto_approval_and_edges_follow(g):
    from openkg.domain.review import apply_policy
    with session_scope() as s:
        graph = s.get(Graph, g)
        graph.autonomy_mode = "auto_accept_high"
        register_source(s, g, url="https://wire.example.net/acme", content="Acme Robotics is a robotics company headquartered in Denver, Colorado, according to its filing. Jane Doe is VP of Product at Acme Robotics, the filing adds.")
        rec = Recorder(s, graph)
        a = rec.record_entity(acme())
        j = rec.record_entity(jane())
        rel = dict(type="WORKS_AT", source={"type": "Person", "name": "Jane Doe", "disambiguator": "Acme Robotics"},
                   target={"type": "Company", "name": "Acme Robotics"})
        r = rec.record_relationship(CandidateRelationship(**rel, evidence=[ev("https://news.example.com/a", "Jane Doe is VP of Product at Acme Robotics")]))
        ids = [a.suggestion_ids[0], j.suggestion_ids[0], r.suggestion_ids[0]]
        assert [s.get(Suggestion, i).status for i in ids] == ["pending"] * 3  # one source each: not enough
        # a second, independent source arrives later for all three
        rec.record_entity(CandidateEntity(type="Company", name="Acme Robotics, Inc.", identifiers={"domain": "acme.example"},
                                          evidence=[ev("https://wire.example.net/acme", "Acme Robotics is a robotics company headquartered in Denver, Colorado")]))
        rec.record_entity(jane(url="https://wire.example.net/acme", excerpt="Jane Doe is VP of Product at Acme Robotics"))
        res = rec.record_relationship(CandidateRelationship(**rel, evidence=[ev("https://wire.example.net/acme", "Jane Doe is VP of Product at Acme Robotics")]))
        assert [s.get(Suggestion, i).status for i in ids] == ["auto_approved"] * 3
        assert any(e[0] == "suggestion.decided" and e[1]["by"] == "policy" for e in res.events)
        assert apply_policy(s, graph) == []  # idempotent


def test_policy_never_auto_approves_changes_conflicts_merges_or_hypotheses(g):
    from openkg.domain.review import qualifies_for_auto_approval
    with session_scope() as s:
        graph = s.get(Graph, g)
        graph.autonomy_mode = "auto_accept_high"
        rec = Recorder(s, graph)
        rec.record_entity(jane())
        rec.record_entity(jane(disamb="Globex"))
        for sug in s.scalars(select(Suggestion).where(Suggestion.kind == "merge")):
            assert not qualifies_for_auto_approval(s, sug)
        hyp = Suggestion(graph_id=g, kind="relationship", risk="elevated", idempotency_key="x", claim_ids=[])
        s.add(hyp)
        s.flush()
        assert not qualifies_for_auto_approval(s, hyp)


def test_model_name_quirks_do_not_create_duplicate_identities(g):
    with session_scope() as s:
        rec = Recorder(s, s.get(Graph, g))
        rec.record_entity(jane())
        again = rec.record_entity(CandidateEntity(type="Person", name="Jane Doe (Acme Robotics)", evidence=[
            ev("https://blog.example.org/b", "Jane Doe joined Acme Robotics as VP of Product in January 2025")]))
        assert again.status == "corroborated"
        assert [e.name for e in s.scalars(select(Entity))] == ["Jane Doe"]
        # an organisation seen first by name, then with its domain, is one entity; no merge suggestion needed
        rec.record_entity(CandidateEntity(type="Company", name="Acme Robotics", evidence=[ev("https://news.example.com/a", "Acme Robotics is a robotics company")]))
        rec.record_entity(acme())
        assert s.scalar(select(func.count()).where(Entity.type == "Company")) == 1
        assert s.scalar(select(func.count()).where(Suggestion.kind == "merge")) == 0
        # parentheses are kept for non-people, e.g. product names
        assert CandidateEntity(type="Company", name="Alphabet (Google)", evidence=[ev("https://x.example.com", "a quote here")]).name == "Alphabet (Google)"


def test_policy_accepts_an_entity_but_not_its_weakly_sourced_attributes(g):
    with session_scope() as s:
        graph = s.get(Graph, g)
        graph.autonomy_mode = "auto_accept_high"
        rec = Recorder(s, graph)
        cand = CandidateEntity(type="Person", name="Jane Doe", disambiguator="Acme Robotics", evidence=[
            ev("https://news.example.com/a", "Jane Doe is VP of Product at Acme Robotics"),
            ev("https://blog.example.org/b", "Jane Doe joined Acme Robotics as VP of Product in January 2025")],
            claims=[CandidateClaim(predicate="title", value="VP of Product", evidence=[
                        ev("https://news.example.com/a", "Jane Doe is VP of Product at Acme Robotics"),
                        ev("https://blog.example.org/b", "Jane Doe joined Acme Robotics as VP of Product in January 2025")]),
                    CandidateClaim(predicate="location", value="Denver", evidence=[ev("https://news.example.com/a", "headquartered in Denver, Colorado")])])
        rec.record_entity(cand)
        assert s.scalar(select(Entity.status)) == "accepted"
        by = {c.predicate: c.status for c in s.scalars(select(Claim))}
        assert by == {"exists": "accepted", "title": "accepted", "location": "proposed"}
        left = s.scalar(select(Suggestion).where(Suggestion.kind == "claim"))
        assert left.status == "pending" and "location" in left.title


def test_first_party_source_is_enough_for_facts_about_an_organisation_but_not_for_links(g):
    with session_scope() as s:
        graph = s.get(Graph, g)
        graph.autonomy_mode = "auto_accept_high"
        register_source(s, g, url="https://www.acme.example/about", content="Acme Robotics is headquartered in Denver, Colorado and was founded in 2019. Jane Doe is our VP of Product. We build warehouse robots.")
        rec = Recorder(s, graph)
        own = [ev("https://www.acme.example/about", "Acme Robotics is headquartered in Denver, Colorado and was founded in 2019")]
        rec.record_entity(CandidateEntity(type="Company", name="Acme Robotics", identifiers={"domain": "acme.example"}, evidence=own, claims=[
            CandidateClaim(predicate="hq_location", value="Denver, Colorado", evidence=own),
            CandidateClaim(predicate="funding_stage", value="Series B", evidence=[ev("https://news.example.com/a", "Acme Robotics is a robotics company")])]))
        ent = s.scalar(select(Entity).where(Entity.type == "Company"))
        assert ent.status == "accepted"  # its own site says it exists
        by = {c.predicate: c.status for c in s.scalars(select(Claim).where(Claim.entity_id == ent.id))}
        assert by["hq_location"] == "accepted" and by["funding_stage"] == "proposed"  # third-party single source still waits
        # a person named on the company site is not auto-approved (no own domain), nor is the link
        j = rec.record_entity(CandidateEntity(type="Person", name="Jane Doe", disambiguator="Acme Robotics", evidence=[ev("https://www.acme.example/about", "Jane Doe is our VP of Product")]))
        r = rec.record_relationship(CandidateRelationship(type="WORKS_AT", source={"type": "Person", "name": "Jane Doe", "disambiguator": "Acme Robotics"},
                                                          target={"type": "Company", "name": "Acme Robotics"}, evidence=[ev("https://www.acme.example/about", "Jane Doe is our VP of Product")]))
        assert s.get(Suggestion, j.suggestion_ids[0]).status == "pending" and s.get(Suggestion, r.suggestion_ids[0]).status == "pending"


def test_a_more_specific_value_corroborates_instead_of_conflicting(g):
    from openkg.domain.recorder import refines
    assert refines("amsterdam", "amsterdam, netherlands") and refines("new york", "new york, ny, united states")
    assert not refines("series a", "series b") and not refines("berlin", "berlin") and not refines("san francisco", "redwood city, united states")
    with session_scope() as s:
        graph = s.get(Graph, g)
        rec, ent = _accepted_acme(s, graph)  # hq_location = "Denver, Colorado"
        register_source(s, g, url="https://dir.example.net/acme", content="Acme Robotics — robotics — Denver. Listed in the regional business directory for the area.")
        register_source(s, g, url="https://long.example.net/acme", content="Acme Robotics has offices at Denver, Colorado, United States according to the directory listing.")
        for url, value, quote in (("https://dir.example.net/acme", "Denver", "Acme Robotics — robotics — Denver"),
                                  ("https://long.example.net/acme", "Denver, Colorado, United States", "Denver, Colorado, United States")):
            rec.record_claim(ent, CandidateClaim(predicate="hq_location", value=value, evidence=[ev(url, quote)]))
        claims = list(s.scalars(select(Claim).where(Claim.predicate == "hq_location")))
        assert len(claims) == 1 and claims[0].status == "accepted" and claims[0].value == "Denver, Colorado, United States"
        assert claims[0].confidence == "high"  # three independent sources now agree
        assert s.scalar(select(func.count()).where(Suggestion.kind == "contradiction")) == 0


@pytest.mark.parametrize("domain,name,etype,ok", [
    ("pinecone.io", "Pinecone", "Company", True), ("www.trychroma.com", "Chroma", "Company", True),
    ("qdrant.tech", "Qdrant", "Company", True), ("weaviate.io", "Weaviate B.V.", "Company", False),
    ("redis.io", "Redis Labs", "Company", True), ("techcrunch.com", "Pinecone", "Company", False),
    ("applebees.com", "Apple", "Company", False), ("janedoe.com", "Jane Doe", "Person", False),
    ("acme.co.uk", "Acme", "Company", True), ("ibm.com", "IBM", "Company", False)])
def test_own_domain_recognition_is_conservative(domain, name, etype, ok):
    from openkg.domain.evidence import is_own_domain
    assert is_own_domain(domain, f"{etype.lower()}:name:x", name, etype) is ok


def test_existing_refinement_conflicts_are_cleaned_up_automatically(g):
    from openkg.domain.recorder import pretty_value
    from openkg.domain.review import apply_policy
    assert pretty_value(50000000) == "50M" and pretty_value(2200000000) == "2.2B" and pretty_value(2021) == "2021" and pretty_value("x") == "x"
    with session_scope() as s:
        graph = s.get(Graph, g)
        rec, ent = _accepted_acme(s, graph)
        old = s.scalar(select(Claim).where(Claim.predicate == "hq_location"))
        # simulate a false conflict recorded before refinement handling existed
        new = Claim(graph_id=g, subject_kind="entity", entity_id=ent.id, predicate="hq_location", value="Denver, Colorado, USA",
                    value_norm="denver, colorado, usa", claim_key="legacy", status="proposed")
        s.add(new)
        s.flush()
        old.status = "contested"
        sug = Suggestion(graph_id=g, kind="contradiction", risk="elevated", idempotency_key="legacy", claim_ids=[new.id], target_claim_id=old.id)
        s.add(sug)
        s.flush()
        apply_policy(s, graph)
        assert sug.status == "superseded" and "Not a conflict" in sug.decision_note
        assert (old.status, old.value) == ("accepted", "Denver, Colorado, USA") and new.status == "superseded"


def test_same_fact_and_predicate_families():
    from openkg.domain.canonical import canonical_predicate, norm_value, same_fact
    assert same_fact("amsterdam, the netherlands", "amsterdam, netherlands") is not None
    assert same_fact("san francisco, ca", "san francisco, california, usa") == "b"
    assert same_fact("2012", "2012-02-09") == "b" and same_fact("2012-02-09", "2012") == "a"
    assert same_fact("2012", "2013") is None and same_fact("series a", "series b") is None
    assert same_fact("san francisco, california", "san francisco, united states", "hq_location") is not None
    assert same_fact("ceo, acme", "ceo, beta", "title") is None  # the shared-first-part rule is for places only
    assert same_fact("springfield", "portland, oregon", "hq_location") is None
    assert norm_value(2012) == norm_value("2012") and norm_value(1.5) == "1.5"
    assert canonical_predicate("founding_year") == "founded_year"
    assert canonical_predicate("headquarters", frozenset({"hq_location"})) == "hq_location"
    assert canonical_predicate("hq_location", frozenset({"location"})) == "location"
    assert canonical_predicate("role", frozenset({"role", "title"})) == "role"  # the ontology's own name wins
    assert canonical_predicate("annual_capacity") == "annual_capacity"
    assert canonical_predicate("number_of_charge_points") == "charge_points_count" == canonical_predicate("charge_points_count")
    from openkg.domain.canonical import norm_name
    assert norm_name("Mercedes\u2011Benz Group") == norm_name("Mercedes-Benz Group")  # non-breaking hyphen
    from openkg.domain.recorder import multi_valued
    from openkg.models import Graph
    assert multi_valued(Graph(ontology={}), "owner") and multi_valued(Graph(ontology={}), "owners") and not multi_valued(Graph(ontology={}), "hq_location")


def test_attribute_synonyms_and_spellings_do_not_conflict(g):
    """Regression from live runs: one company got founded_year / founding_year / founded_date as three
    facts, and "Amsterdam, the Netherlands" vs "Amsterdam, Netherlands" as a conflict."""
    from openkg.domain.recorder import CandidateClaim, CandidateEntity, Recorder, register_source
    graph_id = g
    with session_scope() as s:
        g = s.get(Graph, graph_id)
        for i, text in enumerate(("Elastic was founded in 2012 in Amsterdam, the Netherlands.",
                                  "Elastic, founded 2012-02-09, is based in Amsterdam, Netherlands.")):
            register_source(s, g.id, url=f"https://src{i}.example/elastic", title="t", content=text)
        rec = Recorder(s, g)
        res = rec.record_entity(CandidateEntity(type="Company", name="Elastic", description="Search company", evidence=[
            {"url": "https://src0.example/elastic", "excerpt": "Elastic was founded in 2012"}]))
        ent = s.get(Entity, res.entity_id)

        def claim(pred, value, src, quote):
            rec.record_claim(ent, CandidateClaim(predicate=pred, value=value, evidence=[
                {"url": f"https://src{src}.example/elastic", "excerpt": quote}]))

        claim("founding_year", 2012, 0, "founded in 2012")
        claim("founded_date", "2012-02-09", 1, "founded 2012-02-09")
        claim("headquarters", "Amsterdam, the Netherlands", 0, "Amsterdam, the Netherlands")
        claim("hq_location", "Amsterdam, Netherlands", 1, "Amsterdam, Netherlands")
        facts = {c.predicate: c for c in s.scalars(select(Claim).where(Claim.entity_id == ent.id, Claim.predicate != "exists"))}
        assert set(facts) == {"founded_year", "hq_location"}
        assert facts["founded_year"].value == "2012-02-09" and facts["founded_year"].confidence == "high"
        assert facts["hq_location"].confidence == "high" and facts["hq_location"].status != "contested"
        assert not s.scalar(select(Suggestion.id).where(Suggestion.graph_id == g.id, Suggestion.kind == "contradiction"))
        # recording either wording again is a no-op, not a duplicate
        claim("founded_year", "2012", 0, "founded in 2012")
        claim("founded_year", "2012-02-09", 1, "founded 2012-02-09")
        assert s.scalar(select(func.count()).select_from(Claim).where(Claim.entity_id == ent.id, Claim.predicate == "founded_year")) == 1


def _people_graph(make_graph):
    gid = make_graph(None, ontology={
        "entity_types": [{"type": "Company", "attributes": [{"name": "hq_location"}]},
                         {"type": "Person", "attributes": [{"name": "title"}]}],
        "relationship_types": [{"type": "WORKS_AT", "source": "Person", "target": "Company"},
                               {"type": "PREVIOUSLY_AT", "source": "Person", "target": "Company"}]})
    pages = {
        "https://corp.example/leadership": "# Leadership\n\n## Olivia Park\n\nCEO & CO-FOUNDER\n\nOlivia Park founded Corvid[[2]](#cite_note-2) "
                                           "in 2010. Olivia holds an MS from [Ecole Centrale](https://ecp.example \"ECP\").\n\n## Dana Reyes\n\n"
                                           "Chief Financial Officer\n\nSince:\n\n2018",
        "https://news.example/dana": "Dana Reyes joins Corvid as CFO. Dana has a BA from Yale University and an MBA from Harvard. "
                                     "She was previously at Goldman Sachs for ten years.",
    }
    with session_scope() as s:
        for url, text in pages.items():
            register_source(s, gid, url=url, title="page", content=text)
    return gid


def test_quotes_survive_formatting_but_not_paraphrase(make_graph):
    """Regression from a live run where 58% of real quotes failed: markdown links, footnotes, headings,
    table separators and an ellipsis must not make a word-for-word quote look invented."""
    gid = _people_graph(make_graph)
    url = "https://corp.example/leadership"
    with session_scope() as s:
        rec = Recorder(s, s.get(Graph, gid))

        def verified(quote):
            from openkg.domain.recorder import EvidenceRef
            return rec._resolve_evidence([EvidenceRef(url=url, excerpt=quote)])[0][2]

        assert verified("## Olivia Park  CEO & CO-FOUNDER")
        assert verified("Olivia Park founded Corvid in 2010")           # footnote marker in between
        assert verified("Olivia holds an MS from Ecole Centrale")       # markdown link
        assert verified('"Olivia Park founded ... holds an MS from Ecole Centrale."')  # elision, wrapping quotes
        assert verified("Since: 2018")
        assert not verified("Olivia Park founded Corvid in 2012")       # wrong fact
        assert not verified("Corvid was founded by Olivia Park")        # paraphrase
        assert not verified("holds an MS ... Olivia Park founded")      # reordered
        assert not verified("CEO")                                      # too short to be evidence


def test_evidence_about_someone_else_is_refused(make_graph):
    """Regression from a live run: a model filed Dana's degrees under Olivia in one batched call."""
    gid = _people_graph(make_graph)
    with session_scope() as s:
        rec = Recorder(s, s.get(Graph, gid))
        for name in ("Olivia Park", "Dana Reyes"):
            rec.record_entity(CandidateEntity(type="Person", name=name, evidence=[
                ev("https://corp.example/leadership", f"## {name}")]))
        olivia = s.scalar(select(Entity).where(Entity.name == "Olivia Park"))
        dana = s.scalar(select(Entity).where(Entity.name == "Dana Reyes"))
        degree = CandidateClaim(predicate="education", value="Yale University, BA", evidence=[
            ev("https://news.example/dana", "Dana has a BA from Yale University")])
        res = rec.record_claim(olivia, degree)
        assert "never mentions Olivia Park" in res.message and rec.off_subject
        assert s.scalar(select(func.count()).select_from(Claim).where(Claim.entity_id == olivia.id, Claim.predicate == "education")) == 0
        rec.record_claim(dana, degree)
        rec.record_claim(dana, CandidateClaim(predicate="education", value="Harvard, MBA", evidence=[
            ev("https://news.example/dana", "an MBA from Harvard")]))
        degrees = list(s.scalars(select(Claim).where(Claim.entity_id == dana.id, Claim.predicate == "education")))
        assert len(degrees) == 2 and all(c.status == "proposed" for c in degrees)  # several degrees are not a conflict
        assert not s.scalar(select(Suggestion.id).where(Suggestion.kind == "contradiction"))
        # an entity nobody's page names is not recorded at all
        ghost = rec.record_entity(CandidateEntity(type="Person", name="Sam Ghost", evidence=[
            ev("https://news.example/dana", "Dana Reyes joins Corvid as CFO")]))
        assert ghost.status == "skipped"


def test_titles_dates_and_links_written_as_attributes(make_graph):
    gid = _people_graph(make_graph)
    lead = "https://corp.example/leadership"
    with session_scope() as s:
        rec = Recorder(s, s.get(Graph, gid))
        rec.record_entity(CandidateEntity(type="Person", name="Olivia Park", evidence=[ev(lead, "## Olivia Park")]))
        rec.record_entity(CandidateEntity(type="Person", name="Dana Reyes", evidence=[ev(lead, "## Dana Reyes")]))
        olivia = s.scalar(select(Entity).where(Entity.name == "Olivia Park"))
        dana = s.scalar(select(Entity).where(Entity.name == "Dana Reyes"))

        def claim(ent, pred, value, url, quote, valid_from=None):
            return rec.record_claim(ent, CandidateClaim(predicate=pred, value=value, valid_from=valid_from,
                                                        evidence=[ev(url, quote)]))

        # the same title, split, abbreviated, dated and undated, is one fact and never a conflict
        claim(olivia, "title", "Chief Executive Officer", lead, "CEO & CO-FOUNDER")
        claim(olivia, "title", "Co-Founder", lead, "CEO & CO-FOUNDER", "2010")
        claim(olivia, "job_title", "CEO & Co-Founder", lead, "CEO & CO-FOUNDER")
        claim(dana, "title", "Chief Financial Officer", lead, "Chief Financial Officer")
        claim(dana, "title", "Chief Financial Officer", lead, "Since: 2018", "2018")
        assert not s.scalar(select(Suggestion.id).where(Suggestion.kind == "contradiction"))
        assert not s.scalar(select(Claim.id).where(Claim.status == "contested"))
        cfo = list(s.scalars(select(Claim).where(Claim.entity_id == dana.id, Claim.predicate == "title")))
        assert len(cfo) == 1 and cfo[0].valid_from == "2018"
        # "CFO as of 2026-08" is the same tenure, not a second fact: the earlier start date stays
        claim(dana, "title", "Chief Financial Officer", lead, "Chief Financial Officer", "2026-08")
        claim(dana, "title", "CFO", lead, "Chief Financial Officer", "2018-11-05")
        cfo = list(s.scalars(select(Claim).where(Claim.entity_id == dana.id, Claim.predicate == "title")))
        assert len(cfo) == 1 and cfo[0].valid_from == "2018-11-05"
        # an earlier job with an end date is history, not a rival to the current title
        rec.record_claim(dana, CandidateClaim(predicate="title", value="Vice President, Finance", valid_from="2010", valid_to="2018",
                                              evidence=[ev("https://news.example/dana", "Dana Reyes joins Corvid as CFO")]))
        assert not s.scalar(select(Suggestion.id).where(Suggestion.kind == "contradiction"))
        assert not s.scalar(select(Claim.id).where(Claim.status == "contested"))
        # a real undated disagreement is still a conflict
        claim(dana, "title", "Chief Operating Officer", "https://news.example/dana", "Dana Reyes joins Corvid")
        assert s.scalar(select(Suggestion.id).where(Suggestion.kind == "contradiction"))

        # "works_at: Corvid" is a link, so it becomes an edge (creating the company) instead of a string
        res = claim(dana, "previously_at", "Goldman Sachs", "https://news.example/dana", "previously at Goldman Sachs")
        assert res.status == "created"
        rel = s.scalar(select(Relationship).where(Relationship.type == "PREVIOUSLY_AT"))
        assert rel is not None and rel.from_id == dana.id
        assert s.get(Entity, rel.to_id).name == "Goldman Sachs" and s.get(Entity, rel.to_id).type == "Company"
        assert not s.scalar(select(Claim.id).where(Claim.entity_id == dana.id, Claim.predicate == "previously_at"))


def test_one_organisation_is_one_node_across_types(make_graph):
    """Regression from a live run: investors appeared twice, once as Company and once as Investor."""
    gid = make_graph(None, ontology={
        "entity_types": [{"type": "Company", "attributes": []}, {"type": "Investor", "attributes": []},
                         {"type": "Person", "attributes": []}],
        "relationship_types": [{"type": "INVESTED_IN", "source": "Investor", "target": "Company"}]})
    url = "https://news.example/round"
    with session_scope() as s:
        register_source(s, gid, url=url, title="Round", content="Notable Capital led the Series A of Act Security. "
                                                               "Jordan Notable is a person, unrelated.")
        rec = Recorder(s, s.get(Graph, gid))
        e = [ev(url, "Notable Capital led the Series A of Act Security")]
        rec.record_entity(CandidateEntity(type="Company", name="Act Security", evidence=e))
        first = rec.record_entity(CandidateEntity(type="Company", name="Notable Capital", evidence=e))
        again = rec.record_entity(CandidateEntity(type="Investor", name="Notable Capital, LLC", evidence=e))
        assert again.entity_id == first.entity_id and again.status == "corroborated"
        # the link written as an attribute reads subject -> object, whatever the node types are
        firm = s.get(Entity, first.entity_id)
        rec.record_claim(firm, CandidateClaim(predicate="invested_in", value="Act Security", evidence=e))
        rel = s.scalar(select(Relationship).where(Relationship.type == "INVESTED_IN"))
        assert rel.from_id == firm.id and s.get(Entity, rel.to_id).name == "Act Security"
        assert s.scalar(select(func.count()).select_from(Entity)) == 2
        # people are never unified across types or by name
        person = rec.record_entity(CandidateEntity(type="Person", name="Notable Capital", evidence=e))
        assert person.entity_id != first.entity_id


def test_mention_check_is_generous_about_how_a_page_names_something():
    from openkg.domain.canonical import mentions, plain_words
    page = plain_words("Denis, our CTO, previously built a music search engine. | 4 | PlanetScaleDatabase | San Francisco |")
    assert mentions(page, "Denis Charrier", person=True)          # first name only
    assert mentions(page, "PlanetScale")                           # words run together by a scraper
    assert mentions(page, "Planet Scale Inc.")
    assert not mentions(page, "Alexis Lê-Quôc", person=True)
    assert not mentions(page, "Kinetica") and not mentions(page, "The Group Inc")  # generic words never match


def test_values_that_change_over_time_become_history_not_conflicts(make_graph):
    """Regression from a live run: Series F vs Series G, and seven "conflicting" products."""
    gid = make_graph(None, ontology={"entity_types": [{"type": "Company", "attributes": [{"name": "funding_stage"}]}],
                                     "relationship_types": []})
    url = "https://news.example/ramp"
    with session_scope() as s:
        register_source(s, gid, url=url, title="Ramp", content=(
            "Ramp raised a Series F in March 2025. Ramp announced its Series G in January 2026. Ramp sells corporate "
            "cards, expense management and procurement software. Ramp has offices in New York and Miami."))
        g = s.get(Graph, gid)
        rec = Recorder(s, g)
        ent = s.get(Entity, rec.record_entity(CandidateEntity(type="Company", name="Ramp", evidence=[
            ev(url, "Ramp raised a Series F")])).entity_id)

        def claim(pred, value, quote, valid_from=None):
            return rec.record_claim(ent, CandidateClaim(predicate=pred, value=value, valid_from=valid_from, evidence=[ev(url, quote)]))

        for product in ("corporate cards", "expense management", "procurement software"):
            claim("product", product, product)
        claim("sells", "corporate cards", "corporate cards")  # verbs with many objects never conflict either
        claim("sells", "procurement software", "procurement software")
        claim("office_location", "New York", "offices in New York")
        claim("office_location", "Miami", "New York and Miami")
        assert not s.scalar(select(Suggestion.id).where(Suggestion.kind == "contradiction"))

        # an attribute with no inherent order: two undated values are a real conflict until dated
        claim("latest_filing", "Series G", "Ramp announced its Series G")
        res = claim("latest_filing", "Series F", "Ramp raised a Series F")
        assert "record it again with valid_from" in res.message  # the agent is told how to fix it
        assert s.scalar(select(func.count()).select_from(Suggestion).where(Suggestion.kind == "contradiction", Suggestion.status == "pending")) == 1
        # the agent re-records both with the dates the page gives
        claim("latest_filing", "Series G", "Series G in January 2026", "2026-01")
        claim("latest_filing", "Series F", "Series F in March 2025", "2025-03")
        from openkg.domain.review import apply_policy
        apply_policy(s, g)
        assert not s.scalar(select(Suggestion.id).where(Suggestion.kind == "contradiction", Suggestion.status == "pending"))
        stage = {c.value: c for c in s.scalars(select(Claim).where(Claim.entity_id == ent.id, Claim.predicate == "latest_filing"))}
        assert set(stage) == {"Series F", "Series G"}
        assert stage["Series F"].status == "superseded" and stage["Series F"].superseded_by_id == stage["Series G"].id
        assert stage["Series G"].status in ("proposed", "accepted") and stage["Series G"].valid_from == "2026-01"


def test_organisations_are_one_node_whatever_the_disambiguator(make_graph):
    """Regression from a live run: CATL appeared four times, once per carmaker it supplies."""
    gid = make_graph(None, ontology={
        "entity_types": [{"type": "Company", "attributes": []}, {"type": "Facility", "attributes": []},
                         {"type": "Person", "attributes": []}],
        "relationship_types": [{"type": "OPERATES", "source": "Company", "target": "Facility"},
                               {"type": "SUPPLIES", "source": "Company", "target": "Company"}]})
    url = "https://news.example/batteries"
    with session_scope() as s:
        register_source(s, gid, url=url, title="Batteries", content=(
            "CATL supplies BMW and Volkswagen. Northvolt operates the Northvolt Ett gigafactory. "
            "Alex Kim of BMW and Alex Kim of Volkswagen are different people. CATL's site is catl.example; "
            "an unrelated CATL uses othercatl.example."))
        rec = Recorder(s, s.get(Graph, gid))
        e = [ev(url, "CATL supplies BMW and Volkswagen")]
        ids = {rec.record_entity(CandidateEntity(type="Company", name="CATL", disambiguator=d, evidence=e)).entity_id
               for d in (None, "BMW", "Volkswagen", "supplier to Stellantis")}
        assert len(ids) == 1
        assert not s.scalar(select(Suggestion.id).where(Suggestion.kind == "merge"))
        # a domain seen later attaches to the same node; a second, different domain is a different company
        with_domain = rec.record_entity(CandidateEntity(type="Company", name="CATL", identifiers={"domain": "catl.example"}, evidence=e))
        assert with_domain.entity_id in ids
        # people still split on disambiguator
        people = {rec.record_entity(CandidateEntity(type="Person", name="Alex Kim", disambiguator=d, evidence=[
            ev(url, f"Alex Kim of {d}")])).entity_id for d in ("BMW", "Volkswagen")}
        assert len(people) == 2

        # a link named from the object's side is flipped when the types make it unambiguous
        rec.record_entity(CandidateEntity(type="Company", name="Northvolt", evidence=[ev(url, "Northvolt operates")]))
        plant = s.get(Entity, rec.record_entity(CandidateEntity(type="Facility", name="Northvolt Ett gigafactory", evidence=[
            ev(url, "Northvolt Ett gigafactory")])).entity_id)
        rec.record_claim(plant, CandidateClaim(predicate="operates", value="Northvolt", evidence=[
            ev(url, "Northvolt operates the Northvolt Ett gigafactory")]))
        rel = s.scalar(select(Relationship).where(Relationship.type == "OPERATES"))
        assert s.get(Entity, rel.from_id).name == "Northvolt" and rel.to_id == plant.id
        assert not s.scalar(select(Entity.id).where(Entity.type == "Facility", Entity.name == "Northvolt"))


def test_duplicate_organisations_from_older_graphs_are_merged(make_graph):
    from openkg.domain.review import merge_duplicate_organisations
    gid = make_graph(None, ontology={"entity_types": [{"type": "Company", "attributes": []}, {"type": "Person", "attributes": []}],
                                     "relationship_types": []})
    with session_scope() as s:
        g = s.get(Graph, gid)

        def pair(etype, key_a, key_b):
            a = Entity(graph_id=gid, type=etype, canonical_key=key_a, name="Catl", name_norm="catl", status="accepted")
            b = Entity(graph_id=gid, type=etype, canonical_key=key_b, name="Catl", name_norm="catl")
            s.add_all([a, b])
            s.flush()
            s.add(Suggestion(graph_id=gid, kind="merge", title="Possible duplicate", rationale="", confidence="low", risk="elevated",
                             payload={}, entity_id=b.id, merge_target_id=a.id, claim_ids=[], idempotency_key=f"m-{a.id}"))
            s.flush()
            return a, b

        co_a, co_b = pair("Company", "company:name:catl", "company:name:catl@bmw")
        dom_a, dom_b = pair("Company", "company:domain:catl.example", "company:domain:othercatl.example")
        pa, pb = pair("Person", "person:name:catl@x", "person:name:catl@y")
        assert merge_duplicate_organisations(s, g) == 1
        assert co_b.status == "merged" and co_b.merged_into_id == co_a.id
        assert dom_b.status == "proposed" and pb.status == "proposed"  # different domains, and people, wait for a person


def test_confidence_label_follows_later_evidence_but_respects_the_models_caution(g):
    """Regression from a live run: a fact approved on two sources still showed "low"."""
    with session_scope() as s:
        graph = s.get(Graph, g)
        rec = Recorder(s, graph)
        # the model deliberately lowers confidence on first recording: that stands
        res = rec.record_entity(acme(confidence="low"))
        exists = s.scalar(select(Claim).where(Claim.entity_id == res.entity_id, Claim.predicate == "exists"))
        sug = s.get(Suggestion, res.suggestion_ids[0])
        assert exists.confidence == "low" and sug.confidence == "low"
        # a second, independent source corroborates: the claim and its pending suggestion follow the evidence
        rec.record_entity(CandidateEntity(type="Company", name="Acme Robotics", identifiers={"domain": "acme.example"},
                                          evidence=[ev("https://blog.example.org/b", "Acme Robotics")]))
        s.refresh(exists)
        s.refresh(sug)
        assert exists.confidence == "high" and sug.confidence == "high"


def test_role_attributes_become_links_and_lists_are_split(make_graph):
    """Regression from a live run: "investor: DARPA" and "founder: Hartmut Neven" stayed strings."""
    from openkg.domain.recorder import _NAME_LIST
    assert [n.strip() for n in _NAME_LIST.split("Accel; GV, and Sequoia Capital") if n.strip()] == ["Accel", "GV", "Sequoia Capital"]
    assert [n.strip() for n in _NAME_LIST.split("Acme Co., Ltd.") if n.strip()] == ["Acme Co., Ltd."]
    gid = make_graph(None, ontology={
        "entity_types": [{"type": "Company", "attributes": []}, {"type": "Investor", "attributes": []},
                         {"type": "Person", "attributes": []}],
        "relationship_types": [{"type": "INVESTED_IN", "source": "Investor", "target": "Company"},
                               {"type": "FOUNDED", "source": "Person", "target": "Company"}]})
    url = "https://news.example/ionq"
    with session_scope() as s:
        register_source(s, gid, url=url, title="IonQ", content=(
            "IonQ was co-founded by Christopher Monroe and Jungsang Kim. Its Series B was led by GV and "
            "New Enterprise Associates. Mubadala is not mentioned as an investor here."))
        rec = Recorder(s, s.get(Graph, gid))
        ionq = s.get(Entity, rec.record_entity(CandidateEntity(type="Company", name="IonQ", evidence=[
            ev(url, "IonQ was co-founded by")])).entity_id)
        rec.record_claim(ionq, CandidateClaim(predicate="investors", value="GV; New Enterprise Associates; Tiger Global", evidence=[
            ev(url, "led by GV and New Enterprise Associates")]))
        rec.record_claim(ionq, CandidateClaim(predicate="founders", value="Christopher Monroe and Jungsang Kim", evidence=[
            ev(url, "co-founded by Christopher Monroe and Jungsang Kim")]))
        edges = {(s.get(Entity, r.from_id).name, r.type, s.get(Entity, r.to_id).name) for r in s.scalars(select(Relationship))}
        assert edges == {("GV", "INVESTED_IN", "IonQ"), ("New Enterprise Associates", "INVESTED_IN", "IonQ"),
                         ("Christopher Monroe", "FOUNDED", "IonQ"), ("Jungsang Kim", "FOUNDED", "IonQ")}
        # Tiger Global is not on the page, so it was not created; and no string attribute was left behind
        assert not s.scalar(select(Entity.id).where(Entity.name == "Tiger Global"))
        assert s.scalar(select(Entity.type).where(Entity.name == "GV")) == "Investor"
        assert not s.scalar(select(Claim.id).where(Claim.entity_id == ionq.id, Claim.predicate.in_(("investors", "founders"))))


def test_descriptions_are_prose_not_disputable_facts(g):
    """Regression from a live run: two wordings of a company description were raised as a conflict."""
    from openkg.domain.canonical import same_fact
    assert same_fact("builds deepeval", "builds the deepeval evaluation framework", "description") == "b"
    with session_scope() as s:
        rec = Recorder(s, s.get(Graph, g))
        ent = s.get(Entity, rec.record_entity(acme()).entity_id)
        for text in ("A robotics company.", "A robotics company headquartered in Denver, Colorado."):
            rec.record_claim(ent, CandidateClaim(predicate="description", value=text, evidence=[
                ev("https://news.example.com/a", "Acme Robotics is a robotics company headquartered in Denver, Colorado")]))
        assert ent.description == "A robotics company headquartered in Denver, Colorado."
        assert not s.scalar(select(Claim.id).where(Claim.predicate == "description"))
        assert not s.scalar(select(Suggestion.id).where(Suggestion.kind == "contradiction"))


def test_one_person_is_one_node_when_context_agrees(make_graph):
    """Regression from a live run: Patrick Collison became four nodes (with an employer, without,
    with a personal domain, with a Wikipedia URL) and a twelve-item duplicate queue."""
    gid = make_graph(None, ontology={"entity_types": [{"type": "Person", "attributes": []}, {"type": "Company", "attributes": []}],
                                     "relationship_types": [{"type": "WORKS_AT", "source": "Person", "target": "Company"}]})
    pages = {"https://corp.example/team": "Pat Rivera is chief executive of Corvid. Sam Lee is an advisor.",
             "https://wiki.example/Pat_Rivera": "Pat Rivera (born 1988) is an entrepreneur, co-founder and CEO of Corvid.",
             "https://golf.example/results": "Pat Rivera won the regional amateur golf championship."}
    with session_scope() as s:
        for url, text in pages.items():
            register_source(s, gid, url=url, title="page", content=text)
        rec = Recorder(s, s.get(Graph, gid))
        first = rec.record_entity(CandidateEntity(type="Person", name="Pat Rivera", disambiguator="Corvid", evidence=[
            ev("https://corp.example/team", "Pat Rivera is chief executive of Corvid")])).entity_id
        variants = [
            CandidateEntity(type="Person", name="Pat Rivera", evidence=[ev("https://corp.example/team", "Pat Rivera is chief executive")]),
            CandidateEntity(type="Person", name="Pat Rivera", identifiers={"profile_url": "https://wiki.example/Pat_Rivera"},
                            evidence=[ev("https://wiki.example/Pat_Rivera", "co-founder and CEO of Corvid")]),
            CandidateEntity(type="Person", name="Pat Rivera", disambiguator="Corvid CEO",
                            evidence=[ev("https://wiki.example/Pat_Rivera", "Pat Rivera (born 1988) is an entrepreneur")]),
        ]
        assert {rec.record_entity(v).entity_id for v in variants} == {first}
        assert not s.scalar(select(Suggestion.id).where(Suggestion.kind == "merge"))
        # a namesake with nothing in common stays separate and is offered as a possible duplicate
        golfer = rec.record_entity(CandidateEntity(type="Person", name="Pat Rivera", disambiguator="golf", evidence=[
            ev("https://golf.example/results", "Pat Rivera won the regional amateur golf championship")])).entity_id
        assert golfer != first and s.scalar(select(Suggestion.id).where(Suggestion.kind == "merge"))
        from openkg.domain.review import merge_duplicate_organisations
        assert merge_duplicate_organisations(s, s.get(Graph, gid)) == 0  # nothing ties the golfer to Corvid


def test_names_differing_by_a_generic_suffix_are_one_organisation(make_graph):
    """Regression from a live run: "Notion" and "Notion Labs" were two companies, "Coatue" and
    "Coatue Management" two investors."""
    from openkg.domain.canonical import core_name
    assert core_name("coatue management") == "coatue" == core_name("coatue")
    assert core_name("sequoia capital") == "sequoia" and core_name("general catalyst") == "general catalyst"
    assert core_name("the group") == "the group"  # nothing distinctive left: unchanged
    gid = make_graph(None, ontology={"entity_types": [{"type": "Investor", "attributes": []}, {"type": "Person", "attributes": []}],
                                     "relationship_types": []})
    url = "https://news.example/round"
    with session_scope() as s:
        register_source(s, gid, url=url, title="Round", content="Coatue and Coatue Management and Accel joined the round. "
                                                               "Jordan Capital is a person.")
        rec = Recorder(s, s.get(Graph, gid))
        ids = [rec.record_entity(CandidateEntity(type="Investor", name=name, evidence=[ev(url, "joined the round")])).entity_id
               for name in ("Coatue", "Accel", "Coatue Management")]
        assert ids[0] == ids[2] != ids[1]
        assert "Coatue Management" in s.get(Entity, ids[0]).aliases
        assert not s.scalar(select(Suggestion.id).where(Suggestion.kind == "merge"))
        # a different official domain keeps two same-named firms apart, and offers them for review
        other = rec.record_entity(CandidateEntity(type="Investor", name="Accel Partners", identifiers={"domain": "accel.example"},
                                                  evidence=[ev(url, "Accel joined the round")])).entity_id
        assert other == ids[1]  # first sight of a domain attaches to the existing name-only node
        # people are never matched this way
        a = rec.record_entity(CandidateEntity(type="Person", name="Jordan", evidence=[ev(url, "Jordan Capital is a person")])).entity_id
        b = rec.record_entity(CandidateEntity(type="Person", name="Jordan Capital", evidence=[ev(url, "Jordan Capital is a person")])).entity_id
        assert a != b


def test_funding_stages_are_a_sequence_and_founder_spellings_agree(make_graph):
    """Regression from a live run: Seed, Series A..D without dates raised three conflicts, and
    "CEO and Founder" contradicted "Co-founder and CEO"."""
    from openkg.domain.canonical import stage_rank, title_roles
    assert stage_rank("seed") < stage_rank("series a") < stage_rank("series d") < stage_rank("public")
    assert stage_rank("series b-1") == stage_rank("series b") and stage_rank("growth") is None
    assert title_roles("ceo and founder") == title_roles("co-founder and ceo") == title_roles("cofounder & chief executive officer")
    gid = make_graph(None, ontology={"entity_types": [{"type": "Company", "attributes": [{"name": "funding_stage"}]},
                                                      {"type": "Person", "attributes": [{"name": "title"}]}], "relationship_types": []})
    url = "https://news.example/notion"
    with session_scope() as s:
        register_source(s, gid, url=url, title="Notion", content=(
            "Notion raised a Seed round, a Series A, a Series B and a Series C. Ivan Zhao is CEO and Founder; "
            "Ivan Zhao, Co-founder and CEO, said so."))
        g = s.get(Graph, gid)
        rec = Recorder(s, g)
        co = s.get(Entity, rec.record_entity(CandidateEntity(type="Company", name="Notion", evidence=[ev(url, "Notion raised a Seed round")])).entity_id)
        ivan = s.get(Entity, rec.record_entity(CandidateEntity(type="Person", name="Ivan Zhao", evidence=[ev(url, "Ivan Zhao is CEO and Founder")])).entity_id)
        for stage in ("Series B", "Seed", "Series C", "Series A"):  # any order, no dates
            rec.record_claim(co, CandidateClaim(predicate="funding_stage", value=stage, evidence=[ev(url, "Notion raised a Seed round, a Series A, a Series B and a Series C")]))
        for title in ("CEO and Founder", "Co-founder and CEO"):
            rec.record_claim(ivan, CandidateClaim(predicate="title", value=title, evidence=[ev(url, "Ivan Zhao")]))
        assert not s.scalar(select(Suggestion.id).where(Suggestion.kind == "contradiction"))
        assert not s.scalar(select(Claim.id).where(Claim.status == "contested"))
        stages = {c.value: c.status for c in s.scalars(select(Claim).where(Claim.entity_id == co.id, Claim.predicate == "funding_stage"))}
        assert stages["Seed"] == "superseded" and stages["Series A"] == "superseded"
        assert stages["Series C"] == "proposed"  # the latest stage is the one proposed as current
        assert s.scalar(select(func.count()).select_from(Claim).where(Claim.entity_id == ivan.id, Claim.predicate == "title")) == 1
