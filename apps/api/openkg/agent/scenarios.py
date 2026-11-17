"""Synthetic scenario worlds for demo mode, CI and tests.

EVERYTHING here is fictional: companies, people, investors, documents and `fixture://` URLs.
Evidence excerpts are sentences that literally occur in the synthetic documents, so the
recorder's excerpt verification, independence and contradiction logic run for real.
"""
from __future__ import annotations

from .fixture import Chapter, Scenario, Step

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def human(date: str) -> str:
    parts = date.split("-")
    if len(parts) == 1:
        return parts[0]
    m = MONTHS[int(parts[1]) - 1]
    return f"{m} {parts[0]}" if len(parts) == 2 else f"{m} {int(parts[2])}, {parts[0]}"


class World:
    def __init__(self, sid: str):
        self.sid = sid
        self.sources: dict[str, dict] = {}
        self._clones: list[tuple[str, str]] = []

    def src(self, key: str, domain: str, title: str, published: str | None = None, lead: str = "") -> str:
        self.sources[key] = {"url": f"fixture://{domain}/{key}", "domain": domain, "title": title,
                             "published_at": published, "sentences": [lead] if lead else []}
        return key

    def clone(self, key: str, new_key: str, domain: str, title: str | None = None) -> str:
        """A syndicated re-publication: identical body on another domain (not independent)."""
        base = self.sources[key]
        self.sources[new_key] = {**base, "url": f"fixture://{domain}/{new_key}", "domain": domain,
                                 "title": title or base["title"]}
        self._clones.append((key, new_key))
        return new_key

    def say(self, srcs: list[str], *variants: str) -> list[dict]:
        ev = []
        for i, key in enumerate(srcs):
            sentence = variants[min(i, len(variants) - 1)]
            src = self.sources[key]
            if sentence not in src["sentences"]:
                src["sentences"].append(sentence)
            ev.append({"url": src["url"], "excerpt": sentence})
            for a, b in self._clones:
                if a == key:
                    ev.append({"url": self.sources[b]["url"], "excerpt": sentence})
        return ev

    def finalize(self) -> dict[str, dict]:
        for a, b in self._clones:
            self.sources[b]["sentences"] = self.sources[a]["sentences"]
        return {k: {**v, "text": " ".join(v["sentences"])} for k, v in self.sources.items()}

    # generic candidate builders -------------------------------------------------------------

    def claim(self, pred: str, value, srcs: list[str], *sents: str, valid_from: str | None = None,
              conf: str = "high", inferred: bool = False) -> dict:
        return {"predicate": pred, "value": value, "valid_from": valid_from, "confidence": conf,
                "inferred": inferred, "evidence": self.say(srcs, *sents)}

    def entity(self, type: str, name: str, srcs: list[str], *sents: str, desc: str = "", ids: dict | None = None,
               disamb: str | None = None, claims: list[dict] | None = None, why: str = "",
               aliases: list[str] | None = None) -> dict:
        return {"type": type, "name": name, "description": desc, "identifiers": ids or {}, "disambiguator": disamb,
                "aliases": aliases or [], "confidence": "high", "rationale": why,
                "evidence": self.say(srcs, *sents), "claims": claims or []}

    def rel(self, type: str, a: tuple, b: tuple, srcs: list[str], *sents: str, valid_from: str | None = None,
            valid_to: str | None = None, inferred: bool = False, why: str = "", conf: str = "high") -> dict:
        def ref(t):
            return {"type": t[0], "name": t[1], "disambiguator": t[2] if len(t) > 2 else None}
        return {"type": type, "source": ref(a), "target": ref(b), "valid_from": valid_from, "valid_to": valid_to,
                "inferred": inferred, "rationale": why, "confidence": conf, "evidence": self.say(srcs, *sents)}


def slice_steps(p: str, title: str, why: str, query: str, srcs: list[str], entities: list[dict],
                rels: list[dict], *, agent: str = "investigator", claims: list[dict] | None = None,
                checks: list[dict] | None = None, depth: str = "basic", via: str = "") -> list[Step]:
    steps = [
        Step(f"{p}:slice", "slice", {"title": title, "why": why, "agent": agent}, pause=0.8),
        Step(f"{p}:search", "search", {"agent": "scout", "query": query, "results": srcs, "depth": depth}),
        Step(f"{p}:read", "read", {"agent": agent, "sources": srcs}),
    ]
    via = via or f"Found while investigating “{title}” (query: {query})."
    for i in range(0, len(entities), 2):
        steps.append(Step(f"{p}:ent{i}", "record", {"agent": agent, "entities": entities[i:i + 2], "via": via}, pause=0.9))
    for i in range(0, len(rels), 3):
        steps.append(Step(f"{p}:rel{i}", "record", {"agent": agent, "relationships": rels[i:i + 3]}, pause=0.9))
    if claims:
        steps.append(Step(f"{p}:claims", "record", {"agent": "verifier", "claims": claims}))
    if checks:
        steps.append(Step(f"{p}:verify", "verify", {"checks": checks}))
    return steps


# =================================================================================================
# Scenario 1 — Series B product leaders
# =================================================================================================


def _series_b() -> Scenario:
    w = World("series-b-product-leaders")
    C, P, INV = "Company", "Person", "Investor"

    def company(name, domain, desc, hq, srcs, why="Matches the charter: US startup with a dated Series B financing."):
        ev = w.say(srcs, f"{name} is a {desc} headquartered in {hq}.", f"{hq}-based {name} is a {desc}.")
        return {"type": C, "name": name, "identifiers": {"domain": domain}, "description": desc.capitalize() + ".",
                "confidence": "high", "rationale": why, "evidence": ev,
                "claims": [{"predicate": "hq_location", "value": hq, "confidence": "high", "evidence": ev}]}

    def investor(name, domain, srcs, focus):
        return w.entity(INV, name, srcs, f"{name} is a venture firm investing in {focus}.",
                        f"{name}, a venture investor focused on {focus}, took part in the round.",
                        desc=f"Venture firm investing in {focus}.", ids={"domain": domain},
                        why="Named as an investor in an in-scope financing.")

    def funding(co, stage, amount, date, lead, others, srcs):
        hd = human(date)
        claims = [
            {"entity": {"type": C, "name": co}, "claim": w.claim(
                "funding_stage", stage, srcs, f"{co} closed a {amount} {stage} round on {hd}, led by {lead}.",
                f"{lead} led the {amount} {stage} financing of {co}, announced {hd}.", valid_from=date)},
            {"entity": {"type": C, "name": co}, "claim": w.claim(
                "last_round_amount", amount, srcs, f"{co} closed a {amount} {stage} round on {hd}, led by {lead}.",
                f"{lead} led the {amount} {stage} financing of {co}, announced {hd}.", valid_from=date)},
        ]
        rels = [w.rel("LED_ROUND", (INV, lead), (C, co), srcs,
                      f"{co} closed a {amount} {stage} round on {hd}, led by {lead}.",
                      f"{lead} led the {amount} {stage} financing of {co}, announced {hd}.", valid_from=date,
                      why=f"Lead investor of the {stage}.")]
        for o in others:
            rels.append(w.rel("INVESTED_IN", (INV, o), (C, co), srcs,
                              f"{o} also participated in {co}'s {stage} round.",
                              f"Existing backer {o} joined the {co} {stage}.", valid_from=date,
                              why=f"Participant in the {stage}."))
        return claims, rels

    def person(name, co, title, since, srcs, why="Holds a product leadership role at an in-scope company."):
        hs = human(since)
        ev = w.say(srcs, f"{name} is {title} at {co}, a role held since {hs}.", f"{name} joined {co} as {title} in {hs}.")
        ent = {"type": P, "name": name, "disambiguator": co, "description": f"{title} at {co}.",
               "confidence": "high", "rationale": why, "evidence": ev,
               "claims": [{"predicate": "title", "value": title, "valid_from": since, "confidence": "high", "evidence": ev}]}
        rel = {"type": "WORKS_AT", "source": {"type": P, "name": name, "disambiguator": co},
               "target": {"type": C, "name": co}, "valid_from": since, "confidence": "high", "evidence": ev,
               "rationale": "Dated, role-specific professional evidence (not a passing mention)."}
        return ent, rel

    def founder(name, co, year, title, srcs):
        ev = w.say(srcs, f"{name} co-founded {co} in {year} and serves as its {title}.",
                   f"{co} was started in {year} by {name}, who remains {title}.")
        ent = {"type": P, "name": name, "disambiguator": co, "description": f"Co-founder and {title} of {co}.",
               "confidence": "high", "rationale": "Founder of an in-scope company.", "evidence": ev,
               "claims": [{"predicate": "title", "value": title, "valid_from": year, "confidence": "high", "evidence": ev}]}
        rel = {"type": "FOUNDED", "source": {"type": P, "name": name, "disambiguator": co},
               "target": {"type": C, "name": co}, "valid_from": year, "confidence": "high", "evidence": ev,
               "rationale": "Explicit founding statement."}
        return ent, rel

    def prev(name, co, prev_co, title, a, b, srcs):
        return w.rel("PREVIOUSLY_AT", (P, name, co), (C, prev_co), srcs,
                     f"Before {co}, {name} was {title} at {prev_co} from {a} to {b}.",
                     f"{name} spent {a} to {b} at {prev_co} as {title}.", valid_from=a, valid_to=b,
                     why="Earlier position with explicit dates.")

    # --- documents ---------------------------------------------------------------------------
    w.src("nw-series-b", "newswire.synthetic", "Northwind Robotics closes $42M Series B", "2026-02-11")
    w.src("nw-techledger", "techledger.synthetic", "Warehouse autonomy startup Northwind lands Series B", "2026-02-12")
    w.src("nw-team", "northwind.synthetic", "Northwind Robotics — Leadership", None)
    w.src("nw-blog", "northwind.synthetic", "Meet the product team behind Northwind OS", "2025-09-03")
    w.src("pm-podcast-raman", "productcraft.synthetic", "Episode 88: Priya Raman on robotics roadmaps", "2025-11-20")
    w.src("th-series-b", "newswire.synthetic", "Tessellate Health raises $58M Series B", "2025-12-04")
    w.clone("th-series-b", "th-series-b-syndicated", "bizdaily.synthetic", "Tessellate Health raises $58M Series B (press release)")
    w.src("th-team", "tessellate.synthetic", "Tessellate Health — About us", None)
    w.src("th-interview", "healthstack.synthetic", "How Tessellate ships clinical software", "2026-01-15")
    w.src("qd-series-b", "techledger.synthetic", "Quillon Data secures $36M Series B", "2026-03-19")
    w.src("qd-about", "quillon.synthetic", "Quillon Data — Company", None)
    w.src("qd-registry", "bizregistry.synthetic", "Business registry extract: Quillon Data, Inc.", "2025-08-01")
    w.src("qd-profile", "productcraft.synthetic", "Operator profile: Marcus Lindqvist", "2026-04-02")
    w.src("he-series-b", "newswire.synthetic", "Harborlight Energy announces $64M Series B", "2026-05-06")
    w.src("he-gridwire", "gridwire.synthetic", "Harborlight's Series B and the virtual power plant race", "2026-05-07")
    w.src("he-team", "harborlight.synthetic", "Harborlight Energy — Team", None)
    w.src("fs-series-b", "techledger.synthetic", "Fennec Security raises $47M Series B", "2025-10-22")
    w.src("fs-blog", "fennec.synthetic", "Fennec Security — Who we are", None)
    w.src("fs-secweekly", "secweekly.synthetic", "Fennec's Series B: identity threat detection heats up", "2025-10-23")
    w.src("pf-seed", "techledger.synthetic", "Pallas Freight raises $9M Series A", "2026-04-11")
    w.src("ml-series-b", "newswire.synthetic", "Marlowe Logistics closes $51M Series B", "2026-07-14")
    w.src("ml-team", "marlowe.synthetic", "Marlowe Logistics — Leadership", None)
    w.src("ml-freightbrief", "freightbrief.synthetic", "Marlowe's product bet after its Series B", "2026-07-20")
    w.src("ob-series-b", "biowire.synthetic", "Oriel Biosystems raises $72M Series B", "2026-08-27")
    w.src("ob-team", "oriel.synthetic", "Oriel Biosystems — People", None)
    w.src("ob-labnotes", "labnotes.synthetic", "Inside Oriel's lab automation platform", "2026-09-09")
    w.src("cp-eng", "cinderpath.synthetic", "Cinderpath engineering blog: platform reliability", "2026-06-02")
    w.src("th-cpo-news", "healthstack.synthetic", "Tessellate Health promotes Elena Vasquez to Chief Product Officer", "2026-10-28")
    w.src("fs-series-c", "techledger.synthetic", "Fennec Security raises $110M Series C", "2026-11-03")
    w.src("fs-series-c-sec", "secweekly.synthetic", "Fennec's Series C caps a fast year", "2026-11-04")

    # --- chapter 0 : initial mapping ---------------------------------------------------------
    steps: list[Step] = [
        Step("c0:status", "status", {"status": "planning"}, pause=0.6),
        Step("c0:plan", "plan", {
            "objective": "Map product leaders at US startups with a dated Series B, with founders, investors and earlier positions.",
            "slices": [
                {"title": "Dated Series B financings", "why": "A financing claim with a date defines which companies are in scope.", "est_calls": 6},
                {"title": "Product leadership per company", "why": "Team pages and interviews give time-qualified titles.", "est_calls": 10},
                {"title": "Earlier positions", "why": "Prior employers reveal professional connections between leaders.", "est_calls": 6},
                {"title": "Cross-check and challenge", "why": "Count independent sources; flag syndicated copies and conflicts.", "est_calls": 4},
            ], "est_calls": 26, "note": "Coverage is a sample of what public sources reveal, never the complete universe."}),
        Step("c0:researching", "status", {"status": "researching"}, pause=0.4),
    ]

    # slice 1 — financings
    f_nw, r_nw = funding("Northwind Robotics", "Series B", "$42 million", "2026-02-11", "Halcyon Ventures", ["Redwater Capital"], ["nw-series-b", "nw-techledger"])
    f_th, r_th = funding("Tessellate Health", "Series B", "$58 million", "2025-12-04", "Ninefold Partners", ["Halcyon Ventures"], ["th-series-b"])
    f_qd, r_qd = funding("Quillon Data", "Series B", "$36 million", "2026-03-19", "Redwater Capital", [], ["qd-series-b"])
    steps += slice_steps(
        "c0s1", "Dated Series B financings", "Financing announcements define scope and reveal investors.",
        "US startup Series B announcement led by 2025..2026",
        ["nw-series-b", "nw-techledger", "th-series-b", "th-series-b-syndicated", "qd-series-b"],
        [company("Northwind Robotics", "northwind.synthetic", "warehouse autonomy company", "Austin, Texas", ["nw-series-b", "nw-techledger"]),
         investor("Halcyon Ventures", "halcyon.synthetic", ["nw-series-b", "th-series-b"], "industrial and health software"),
         company("Tessellate Health", "tessellate.synthetic", "clinical workflow software company", "Boston, Massachusetts", ["th-series-b"]),
         investor("Ninefold Partners", "ninefold.synthetic", ["th-series-b"], "healthcare technology"),
         company("Quillon Data", "quillon.synthetic", "data observability company", "New York, New York", ["qd-series-b", "qd-about"]),
         investor("Redwater Capital", "redwater.synthetic", ["nw-series-b", "qd-series-b"], "enterprise infrastructure")],
        r_nw + r_th + r_qd, agent="scout", claims=f_nw + f_th + f_qd, depth="advanced",
        checks=[
            {"label": "Northwind Robotics Series B", "outcome": "corroborated", "detail": "Two independent outlets report the same amount, date and lead."},
            {"label": "Tessellate Health Series B", "outcome": "syndicated", "detail": "The second page is a verbatim copy of the press release, so it is not counted as independent. Confidence stays medium."},
            {"label": "Quillon Data Series B", "outcome": "single_source", "detail": "Only one outlet so far; held at medium confidence."},
        ])

    # slice 2 — Northwind
    e1, r1 = founder("Jonah Whitfield", "Northwind Robotics", "2021", "Chief Executive Officer", ["nw-team", "nw-techledger"])
    e2, r2 = person("Priya Raman", "Northwind Robotics", "VP of Product", "2024-06", ["nw-team", "pm-podcast-raman"])
    e3, r3 = person("Daniel Okafor", "Northwind Robotics", "Senior Product Manager", "2025-01", ["nw-blog"])
    brightloom = w.entity(C, "Brightloom", ["pm-podcast-raman", "qd-profile"],
                          "Brightloom is a publicly traded workflow software company.",
                          "Brightloom, the publicly traded workflow software company, was his previous employer.",
                          desc="Publicly traded workflow software company (earlier employer, not itself in scope).",
                          ids={"domain": "brightloom.synthetic"}, why="Earlier employer connecting product leaders.")
    steps += slice_steps(
        "c0s2", "Northwind Robotics product organization", "Leadership page plus an interview give dated titles.",
        "Northwind Robotics product leadership VP product team", ["nw-team", "nw-blog", "pm-podcast-raman"],
        [e1, e2, e3, brightloom],
        [r1, r2, r3, prev("Priya Raman", "Northwind Robotics", "Brightloom", "Director of Product", "2020", "2024", ["pm-podcast-raman"])],
        checks=[{"label": "Priya Raman — VP of Product", "outcome": "corroborated", "detail": "Company page and an independent interview agree on title and start date."},
                {"label": "Daniel Okafor — Senior PM", "outcome": "single_source", "detail": "Only the company blog; medium confidence."}])

    # slice 3 — Tessellate
    e1, r1 = founder("Mei-Ling Zhou", "Tessellate Health", "2020", "Chief Executive Officer", ["th-team", "th-series-b"])
    e2, r2 = person("Elena Vasquez", "Tessellate Health", "Head of Product", "2023-09", ["th-team", "th-interview"])
    cinderpath = w.entity(C, "Cinderpath", ["th-interview", "cp-eng"],
                          "Cinderpath is a large developer tools company.",
                          "Cinderpath is a developer tools company with several thousand employees.",
                          desc="Large developer tools company (earlier employer, not itself in scope).",
                          ids={"domain": "cinderpath.synthetic"}, why="Earlier employer connecting product leaders.")
    steps += slice_steps(
        "c0s3", "Tessellate Health product organization", "About page and a trade interview.",
        "Tessellate Health head of product leadership", ["th-team", "th-interview"],
        [e1, e2, cinderpath],
        [r1, r2, prev("Elena Vasquez", "Tessellate Health", "Cinderpath", "Group Product Manager", "2019", "2023", ["th-interview"])],
        checks=[{"label": "Elena Vasquez — Head of Product", "outcome": "corroborated", "detail": "Two independent sources with matching start date."}])

    # slice 4 — Quillon (with a contradiction + an inferred hypothesis)
    e1, r1 = founder("Tobias Brandt", "Quillon Data", "2021", "Chief Executive Officer", ["qd-about", "qd-series-b"])
    e2, r2 = person("Marcus Lindqvist", "Quillon Data", "Chief Product Officer", "2025-03", ["qd-about", "qd-profile"])
    hq_conflict = {"entity": {"type": C, "name": "Quillon Data"}, "claim": w.claim(
        "hq_location", "Jersey City, New Jersey", ["qd-registry"],
        "Registered principal office of Quillon Data, Inc.: Jersey City, New Jersey.", conf="medium")}
    hypothesis = w.rel("WORKED_WITH", (P, "Marcus Lindqvist", "Quillon Data"), (P, "Priya Raman", "Northwind Robotics"),
                       ["qd-profile"], "Marcus Lindqvist spent 2019 to 2023 at Brightloom as Principal Product Manager.",
                       valid_from="2020", valid_to="2023", inferred=True, conf="low",
                       why="Hypothesis only: both worked in Brightloom's product organization during 2020–2023. "
                           "No source states they worked together.")
    steps += slice_steps(
        "c0s4", "Quillon Data product organization", "Company page, registry extract and an operator profile.",
        "Quillon Data chief product officer Marcus Lindqvist background", ["qd-about", "qd-registry", "qd-profile"],
        [e1, e2],
        [r1, r2, prev("Marcus Lindqvist", "Quillon Data", "Brightloom", "Principal Product Manager", "2019", "2023", ["qd-profile"]), hypothesis],
        claims=[hq_conflict],
        checks=[{"label": "Quillon Data headquarters", "outcome": "conflict", "detail": "Press coverage says New York; a registry extract lists Jersey City. Marked contested for review rather than silently choosing one."},
                {"label": "Lindqvist ↔ Raman", "outcome": "hypothesis", "detail": "Overlapping tenure is not evidence of collaboration. Recorded as a low-confidence inferred link."}])

    # slice 5 — more financings (+ an out-of-scope candidate)
    f_he, r_he = funding("Harborlight Energy", "Series B", "$64 million", "2026-05-06", "Kestrel Growth", ["Halcyon Ventures"], ["he-series-b", "he-gridwire"])
    f_fs, r_fs = funding("Fennec Security", "Series B", "$47 million", "2025-10-22", "Juniper Row Capital", ["Redwater Capital"], ["fs-series-b", "fs-secweekly"])
    pallas = w.entity(C, "Pallas Freight", ["pf-seed"], "Pallas Freight is a freight marketplace headquartered in Atlanta, Georgia.",
                      desc="Freight marketplace.", ids={"domain": "pallas.synthetic"},
                      why="Surfaced by the financing search; its round is a Series A, so it may be out of scope.",
                      claims=[w.claim("funding_stage", "Series A", ["pf-seed"], "Pallas Freight raised a $9 million Series A on April 11, 2026.", valid_from="2026-04-11", conf="medium")])
    steps += slice_steps(
        "c0s5", "More Series B financings", "Widen coverage to energy and security.",
        "Series B round announced climate energy security startup US 2025 2026",
        ["he-series-b", "he-gridwire", "fs-series-b", "fs-secweekly", "pf-seed"],
        [company("Harborlight Energy", "harborlight.synthetic", "virtual power plant software company", "Denver, Colorado", ["he-series-b", "he-gridwire"]),
         investor("Kestrel Growth", "kestrel.synthetic", ["he-series-b"], "climate and energy software"),
         company("Fennec Security", "fennec.synthetic", "identity threat detection company", "Seattle, Washington", ["fs-series-b", "fs-secweekly"]),
         investor("Juniper Row Capital", "juniperrow.synthetic", ["fs-series-b"], "security and developer infrastructure"),
         pallas],
        r_he + r_fs, agent="scout", claims=f_he + f_fs, depth="advanced",
        checks=[{"label": "Harborlight Energy Series B", "outcome": "corroborated", "detail": "Two independent outlets."},
                {"label": "Fennec Security Series B", "outcome": "corroborated", "detail": "Two independent outlets."},
                {"label": "Pallas Freight", "outcome": "out_of_scope", "detail": "The financing is a Series A. Proposed with that caveat so a reviewer can reject it."}])

    # slice 6 — Harborlight & Fennec people
    e1, r1 = founder("Grace Adeyemi", "Harborlight Energy", "2020", "Chief Executive Officer", ["he-team", "he-gridwire"])
    e2, r2 = person("Aiko Tanabe", "Harborlight Energy", "Director of Product", "2024-11", ["he-team"])
    e3, r3 = founder("Rafael Ortiz", "Fennec Security", "2021", "Chief Executive Officer", ["fs-blog", "fs-secweekly"])
    e4, r4 = person("Samir Haddad", "Fennec Security", "Group Product Manager", "2025-05", ["fs-blog", "fs-secweekly"])
    steps += slice_steps(
        "c0s6", "Harborlight and Fennec product leaders", "Team pages; look for links back into the existing map.",
        "Harborlight Energy Fennec Security product manager director team", ["he-team", "fs-blog", "fs-secweekly"],
        [e1, e2, e3, e4],
        [r1, r2, r3, r4, prev("Samir Haddad", "Fennec Security", "Northwind Robotics", "Product Manager", "2022", "2025", ["fs-blog"])],
        checks=[{"label": "Samir Haddad — earlier role at Northwind", "outcome": "single_source", "detail": "Connects Fennec to Northwind; one source, medium confidence."}])

    steps += [
        Step("c0:frontier", "frontier", {"questions": [
            {"question": "Who leads product at Quillon Data below the CPO?", "entity": "Quillon Data", "rationale": "Only one product leader found; team page lists no other product roles.", "info_gain": 0.7, "est_calls": 4},
            {"question": "Did Marcus Lindqvist and Priya Raman actually work together at Brightloom?", "entity": "Brightloom", "rationale": "Overlap is inferred; a launch post or talk naming both would confirm or refute it.", "info_gain": 0.6, "est_calls": 3},
            {"question": "Which other US companies closed a Series B in mid-2026?", "rationale": "Coverage so far is five companies; later announcements are unexplored.", "info_gain": 0.9, "est_calls": 8},
            {"question": "Where is Quillon Data actually headquartered?", "entity": "Quillon Data", "rationale": "Two sources conflict; a dated company statement would resolve it.", "info_gain": 0.5, "est_calls": 2},
        ]}),
        Step("c0:finish", "finish", {"text": "Mapped 5 Series B companies, their lead investors, founders and product leaders "
             "from 19 synthetic documents. One headquarters conflict and one inferred connection are waiting for review; "
             "one candidate looks out of scope. Coverage is a sample, not the complete universe."}),
    ]
    chapter0 = Chapter("Initial mapping", "Map Series B companies, investors and product leaders", steps)

    # --- chapter 1 : expansion (played live in the demo) --------------------------------------
    f_ml, r_ml = funding("Marlowe Logistics", "Series B", "$51 million", "2026-07-14", "Redwater Capital", ["Larkspur Capital"], ["ml-series-b", "ml-freightbrief"])
    f_ob, r_ob = funding("Oriel Biosystems", "Series B", "$72 million", "2026-08-27", "Ninefold Partners", ["Kestrel Growth"], ["ob-series-b", "ob-labnotes"])
    x: list[Step] = [
        Step("c1:status", "status", {"status": "planning"}, pause=0.6),
        Step("c1:plan", "plan", {
            "objective": "Extend coverage to mid-2026 Series B companies and trace connections back into the existing map.",
            "slices": [
                {"title": "Mid-2026 Series B financings", "why": "Highest information gain on the open frontier.", "est_calls": 4},
                {"title": "Product leaders at the new companies", "why": "Look for people who previously worked at mapped companies.", "est_calls": 6},
                {"title": "Identity checks", "why": "A shared name is not a shared identity; verify before linking.", "est_calls": 3},
            ], "est_calls": 13, "note": "Skips 19 documents already read in earlier runs."}),
        Step("c1:researching", "status", {"status": "researching"}, pause=0.4),
        Step("c1:skipnote", "note", {"kind": "decision", "text": "Reusing 19 cached sources from earlier runs; only new documents are fetched."}),
    ]
    x += slice_steps(
        "c1s1", "Mid-2026 Series B financings", "Frontier question with the highest expected information gain.",
        "Series B announced July August 2026 US startup led by", ["ml-series-b", "ml-freightbrief", "ob-series-b", "ob-labnotes"],
        [company("Marlowe Logistics", "marlowe.synthetic", "freight orchestration software company", "Chicago, Illinois", ["ml-series-b", "ml-freightbrief"]),
         investor("Larkspur Capital", "larkspur.synthetic", ["ml-series-b"], "supply chain technology"),
         company("Oriel Biosystems", "oriel.synthetic", "lab automation company", "San Diego, California", ["ob-series-b", "ob-labnotes"])],
        r_ml + r_ob, agent="scout", claims=f_ml + f_ob, depth="advanced",
        checks=[{"label": "Marlowe Logistics Series B", "outcome": "corroborated", "detail": "Two independent outlets."},
                {"label": "Oriel Biosystems Series B", "outcome": "corroborated", "detail": "Two independent outlets."}])
    e1, r1 = founder("Idris Bello", "Marlowe Logistics", "2022", "Chief Executive Officer", ["ml-team", "ml-freightbrief"])
    e2, r2 = person("Hannah Kowalski", "Marlowe Logistics", "VP of Product", "2025-08", ["ml-team", "ml-freightbrief"])
    e3, r3 = founder("Sana Mirza", "Oriel Biosystems", "2021", "Chief Executive Officer", ["ob-team", "ob-labnotes"])
    e4, r4 = person("Leo Fontaine", "Oriel Biosystems", "Head of Product", "2026-01", ["ob-team", "ob-labnotes"])
    x += slice_steps(
        "c1s2", "Product leaders at Marlowe and Oriel", "Team pages and trade coverage; watch for earlier roles at mapped companies.",
        "Marlowe Logistics Oriel Biosystems VP product head of product", ["ml-team", "ml-freightbrief", "ob-team", "ob-labnotes"],
        [e1, e2, e3, e4],
        [r1, r2, r3, r4,
         prev("Hannah Kowalski", "Marlowe Logistics", "Northwind Robotics", "Senior Product Manager", "2021", "2025", ["ml-freightbrief"]),
         prev("Leo Fontaine", "Oriel Biosystems", "Tessellate Health", "Product Lead", "2021", "2025", ["ob-labnotes"]),
         w.rel("ADVISES", (P, "Priya Raman", "Northwind Robotics"), (C, "Oriel Biosystems"), ["ob-team"],
               "Priya Raman, VP of Product at Northwind Robotics, joined Oriel Biosystems as a product advisor in March 2026.",
               valid_from="2026-03", why="Explicit advisory role connecting two mapped companies.")],
        checks=[{"label": "Hannah Kowalski — earlier role at Northwind", "outcome": "single_source", "detail": "Links Marlowe to Northwind; medium confidence."},
                {"label": "Leo Fontaine — earlier role at Tessellate", "outcome": "single_source", "detail": "Links Oriel to Tessellate; medium confidence."}])
    namesake = w.entity(P, "Daniel Okafor", ["cp-eng"], "Daniel Okafor is a Staff Engineer at Cinderpath working on platform reliability.",
                        desc="Staff Engineer at Cinderpath.", disamb="Cinderpath",
                        why="Surfaced while checking Daniel Okafor's background. Different employer and function, so recorded as a separate person.",
                        claims=[w.claim("title", "Staff Engineer", ["cp-eng"], "Daniel Okafor is a Staff Engineer at Cinderpath working on platform reliability.", conf="medium")])
    x += slice_steps(
        "c1s3", "Identity check: Daniel Okafor", "Two people share this name; verify before linking anything.",
        "\"Daniel Okafor\" product manager OR engineer Cinderpath Northwind", ["cp-eng"], [namesake],
        [w.rel("WORKS_AT", (P, "Daniel Okafor", "Cinderpath"), (C, "Cinderpath"), ["cp-eng"],
               "Daniel Okafor is a Staff Engineer at Cinderpath working on platform reliability.", why="Role-specific statement.")],
        agent="verifier",
        checks=[{"label": "Daniel Okafor (Northwind) vs Daniel Okafor (Cinderpath)", "outcome": "possible_duplicate",
                 "detail": "Same name, different employer and role. Not merged automatically; sent to review."}])
    x += [
        Step("c1:frontier", "frontier", {"questions": [
            {"question": "Who else moved from Northwind Robotics to later-stage startups?", "entity": "Northwind Robotics", "rationale": "Two alumni found in product roles elsewhere; the pattern may continue.", "info_gain": 0.65, "est_calls": 5},
            {"question": "What does Larkspur Capital's portfolio look like?", "entity": "Larkspur Capital", "rationale": "New investor with a single known investment.", "info_gain": 0.45, "est_calls": 4},
        ]}),
        Step("c1:finish", "finish", {"text": "Added 2 companies, 1 investor and 5 people from 7 new synthetic documents, with three "
             "links back into the existing map. A possible duplicate person was left for review."}),
    ]
    chapter1 = Chapter("Expansion", "Extend to mid-2026 financings and trace connections", x)

    # --- monitor scripts ---------------------------------------------------------------------
    monitors = {
        "Elena Vasquez": [
            {"sources": ["th-team"], "claims": [w.claim("title", "Head of Product", ["th-team"], "Elena Vasquez is Head of Product at Tessellate Health, a role held since September 2023.", valid_from="2023-09")]},
            {"sources": ["th-team", "th-cpo-news"], "claims": [w.claim(
                "title", "Chief Product Officer", ["th-cpo-news"],
                "Tessellate Health has promoted Elena Vasquez to Chief Product Officer, effective October 2026.", valid_from="2026-10", conf="medium")]},
        ],
        "Fennec Security": [
            {"sources": ["fs-series-b"], "claims": []},
            {"sources": ["fs-series-b", "fs-series-c", "fs-series-c-sec"], "claims": [w.claim(
                "funding_stage", "Series C", ["fs-series-c", "fs-series-c-sec"],
                "Fennec Security closed a $110 million Series C round on November 3, 2026.",
                "Fennec Security's $110 million Series C, announced November 3, 2026, comes a year after its Series B.", valid_from="2026-11-03")]},
        ],
    }

    ontology = {
        "entity_types": [
            {"type": C, "description": "A company: in-scope startup or a relevant earlier employer", "color": "sky",
             "attributes": [{"name": "hq_location", "description": "Headquarters city"}, {"name": "funding_stage", "description": "Latest dated financing stage"},
                            {"name": "last_round_amount", "description": "Size of the latest round"}, {"name": "employee_count", "description": "Approximate headcount"}]},
            {"type": P, "description": "A product leader or founder, from public professional sources", "color": "amber",
             "attributes": [{"name": "title", "description": "Current title, time-qualified"}, {"name": "location", "description": "Public work location"}]},
            {"type": INV, "description": "A venture investor in an in-scope round", "color": "violet",
             "attributes": [{"name": "focus", "description": "Stated investment focus"}]},
        ],
        "relationship_types": [
            {"type": "WORKS_AT", "source": P, "target": C, "description": "Current role; requires dated professional evidence", "temporal": True},
            {"type": "PREVIOUSLY_AT", "source": P, "target": C, "description": "Earlier role with a validity interval", "temporal": True},
            {"type": "FOUNDED", "source": P, "target": C, "description": "Founder or co-founder", "temporal": True},
            {"type": "LED_ROUND", "source": INV, "target": C, "description": "Led a dated financing round", "temporal": True},
            {"type": "INVESTED_IN", "source": INV, "target": C, "description": "Participated in a dated financing round", "temporal": True},
            {"type": "ADVISES", "source": P, "target": C, "description": "Advisory role", "temporal": True},
            {"type": "WORKED_WITH", "source": P, "target": P, "description": "Professional connection; often inferred, so low confidence", "temporal": True},
        ],
        "multi_valued_predicates": [],
    }
    charter = {
        "question": "Who leads product at US startups that recently raised a Series B, and how are they connected?",
        "scope": "US-headquartered startups with a dated Series B between October 2025 and September 2026; their product leaders, founders, investors and earlier positions.",
        "inclusion": ["Company has a dated Series B financing claim", "Person holds a product leadership or founder role, supported by professional sources"],
        "exclusion": ["Private contact details", "Companies whose latest round is not a Series B", "Links inferred only from a shared name"],
        "preferred_domains": ["company team pages", "financing announcements", "trade interviews"],
        "excluded_domains": [], "time_horizon": "2025-10 to 2026-09",
        "evidence_standard": "Titles need dated professional evidence. Financing stage must be tied to a dated round. Two copies of one press release count as one source.",
        "ambiguities": ["“Product manager” is read as product leadership roles (PM through CPO).", "“All PMs” is not observable; the graph is a growing sample."],
        "stop_conditions": ["Budget or call cap reached", "New searches return only already-read sources"],
        "watch_defaults": ["title", "funding_stage"],
        "coverage_note": "A sample of what public sources reveal. Never a complete list.",
    }
    return Scenario(
        id="series-b-product-leaders", name="Series B product leaders",
        description="Product leaders at fictional US Series B startups, with founders, investors and earlier roles.",
        prompt="Map product managers at Series B startups in the US. Include their companies, investors, previous positions, founders and relevant professional connections.",
        charter=charter, ontology=ontology, sources=w.finalize(), chapters=[chapter0, chapter1], monitor_scripts=monitors,
        seed={"reject": [("Pallas Freight", "Out of scope: latest round is a Series A, not a Series B.")],
              "keep_pending": ["worked with", "Samir Haddad —previously at"],
              "monitors": [("Elena Vasquez", "Role or title changes", ["title"], 2), ("Fennec Security", "New financing or stage change", ["funding_stage"], 1),
                           ("Priya Raman", "Role or title changes", ["title"], 0)]})


# =================================================================================================
# Scenario 2 — Solid-state battery supply chain
# =================================================================================================


def _battery() -> Scenario:
    w = World("solid-state-battery-supply-chain")
    CO, FAC, TECH, MAT, INST = "Company", "Facility", "Technology", "Material", "Institution"

    w.src("vc-profile", "cellreport.synthetic", "Veridian Cells and the sulfide bet", "2026-04-08")
    w.src("vc-site", "veridian.synthetic", "Veridian Cells — Technology", None)
    w.src("vc-pilot", "energydesk.synthetic", "Veridian opens Reno pilot line", "2026-01-22")
    w.src("sm-site", "sablecrest.synthetic", "Sablecrest Materials — Products", None)
    w.src("sm-supply", "cellreport.synthetic", "Sablecrest signs electrolyte supply deal with Veridian", "2026-03-03")
    w.src("kl-site", "kairoslithium.synthetic", "Kairos Lithium — Lithium metal foil", None)
    w.src("kl-deal", "minerwire.synthetic", "Kairos Lithium to supply foil to Veridian and Norrland", "2026-05-19")
    w.src("am-partner", "autowire.synthetic", "Altamira Motors partners with Veridian on solid-state packs", "2026-06-10")
    w.src("am-ir", "altamira.synthetic", "Altamira Motors — Technology roadmap update", "2026-06-11")
    w.src("nv-site", "norrlandvolt.synthetic", "Norrland Volt — Gigafactory One", None)
    w.src("nv-report", "energydesk.synthetic", "Norrland Volt bets on oxide separators", "2026-02-14")
    w.src("bc-site", "brightmoor.synthetic", "Brightmoor Ceramics — LLZO separators", None)
    w.src("hi-license", "halvorsen.synthetic", "Halvorsen Institute licenses anode-free design to Veridian", "2025-11-05")
    w.src("tr-site", "tesserarecycling.synthetic", "Tessera Recycling — Closed-loop lithium", None)
    w.src("tr-deal", "minerwire.synthetic", "Tessera Recycling to recover lithium from Norrland scrap", "2026-08-12")
    w.src("vc-capacity", "energydesk.synthetic", "Veridian quadruples Reno pilot capacity", "2026-11-05")
    w.src("vc-capacity-2", "cellreport.synthetic", "Reno pilot line now at 200 MWh", "2026-11-06")
    w.src("am-delay", "autowire.synthetic", "Altamira pushes solid-state pilot vehicles to 2028", "2026-11-09")

    def co(name, domain, desc, hq, srcs, why="Participant in the solid-state battery supply chain."):
        ev = w.say(srcs, f"{name} is a {desc} based in {hq}.", f"{name}, a {desc} from {hq}, is expanding.")
        return {"type": CO, "name": name, "identifiers": {"domain": domain}, "description": desc.capitalize() + ".",
                "confidence": "high", "rationale": why, "evidence": ev,
                "claims": [{"predicate": "hq_location", "value": hq, "confidence": "high", "evidence": ev}]}

    steps = [
        Step("c0:status", "status", {"status": "planning"}, pause=0.6),
        Step("c0:plan", "plan", {
            "objective": "Map who develops, supplies and licenses what in the solid-state battery supply chain.",
            "slices": [
                {"title": "Cell developers and their chemistries", "why": "Technology choice determines the upstream supply chain.", "est_calls": 6},
                {"title": "Material suppliers", "why": "Electrolyte, anode and separator suppliers with dated agreements.", "est_calls": 8},
                {"title": "Facilities and partnerships", "why": "Pilot lines, plants and automaker programmes.", "est_calls": 6},
            ], "est_calls": 20, "note": "Supply agreements are recorded only when a source states them with a date."}),
        Step("c0:researching", "status", {"status": "researching"}, pause=0.4),
    ]
    sulfide = w.entity(TECH, "Sulfide solid electrolyte cell", ["vc-site", "vc-profile"],
                       "Veridian's cells use a sulfide solid electrolyte in place of liquid electrolyte.",
                       "The company's design relies on a sulfide solid electrolyte, which conducts lithium ions well but is sensitive to moisture.",
                       desc="Cell architecture using a sulfide solid electrolyte.", why="Core technology of a mapped developer.")
    anode_free = w.entity(TECH, "Anode-free lithium-metal design", ["hi-license", "vc-site"],
                          "The anode-free lithium-metal design plates lithium directly onto the current collector during charging.",
                          "Veridian's roadmap includes an anode-free lithium-metal design licensed from academia.",
                          desc="Design that forms the lithium anode in place during charging.", why="Licensed technology.")
    halvorsen = w.entity(INST, "Halvorsen Institute of Technology", ["hi-license"],
                         "The Halvorsen Institute of Technology is a research university with a battery materials laboratory.",
                         desc="Research university with a battery materials laboratory.", ids={"domain": "halvorsen.synthetic"},
                         why="Licensor of a mapped technology.")
    steps += slice_steps(
        "c0s1", "Cell developers and their chemistries", "Start from developers; chemistry decides the supply chain.",
        "solid-state battery cell developer sulfide electrolyte pilot line", ["vc-profile", "vc-site", "hi-license"],
        [co("Veridian Cells", "veridian.synthetic", "solid-state battery cell developer", "Reno, Nevada", ["vc-profile", "vc-pilot"]),
         sulfide, anode_free, halvorsen],
        [w.rel("DEVELOPS", (CO, "Veridian Cells"), (TECH, "Sulfide solid electrolyte cell"), ["vc-site", "vc-profile"],
               "Veridian's cells use a sulfide solid electrolyte in place of liquid electrolyte.",
               "The company's design relies on a sulfide solid electrolyte, which conducts lithium ions well but is sensitive to moisture.",
               why="Developer of the technology."),
         w.rel("LICENSES_FROM", (CO, "Veridian Cells"), (INST, "Halvorsen Institute of Technology"), ["hi-license"],
               "Veridian Cells signed a licence with the Halvorsen Institute of Technology on November 5, 2025.",
               valid_from="2025-11-05", why="Dated licence agreement."),
         w.rel("DEVELOPS", (CO, "Veridian Cells"), (TECH, "Anode-free lithium-metal design"), ["vc-site"],
               "Veridian's roadmap includes an anode-free lithium-metal design licensed from academia.", why="On the developer's roadmap.")],
        agent="scout", depth="advanced",
        checks=[{"label": "Veridian — sulfide chemistry", "outcome": "corroborated", "detail": "Company page and independent trade profile agree."}])

    li2s = w.entity(MAT, "Lithium sulfide", ["sm-site", "sm-supply"], "Sablecrest produces battery-grade lithium sulfide, the precursor for sulfide electrolytes.",
                    "Lithium sulfide is the key precursor for sulfide solid electrolytes.", desc="Precursor for sulfide solid electrolytes.",
                    aliases=["Li2S"], why="Critical upstream material.")
    foil = w.entity(MAT, "Lithium metal foil", ["kl-site", "kl-deal"], "Kairos Lithium rolls lithium metal foil as thin as 20 micrometres.",
                    "Lithium metal foil is the anode material for most solid-state designs.", desc="Thin lithium foil used as anode.", why="Critical upstream material.")
    steps += slice_steps(
        "c0s2", "Material suppliers", "Follow the sulfide chemistry upstream to electrolyte and anode suppliers.",
        "sulfide electrolyte supplier lithium metal foil supply agreement solid-state", ["sm-site", "sm-supply", "kl-site", "kl-deal"],
        [co("Sablecrest Materials", "sablecrest.synthetic", "specialty electrolyte materials producer", "Osaka, Japan", ["sm-site", "sm-supply"]), li2s,
         co("Kairos Lithium", "kairoslithium.synthetic", "lithium metal processor", "Perth, Australia", ["kl-site", "kl-deal"]), foil],
        [w.rel("SUPPLIES", (CO, "Sablecrest Materials"), (CO, "Veridian Cells"), ["sm-supply"],
               "Sablecrest Materials signed a multi-year agreement on March 3, 2026 to supply sulfide electrolyte to Veridian Cells.",
               valid_from="2026-03-03", why="Dated supply agreement."),
         w.rel("PRODUCES", (CO, "Sablecrest Materials"), (MAT, "Lithium sulfide"), ["sm-site", "sm-supply"],
               "Sablecrest produces battery-grade lithium sulfide, the precursor for sulfide electrolytes.",
               "Lithium sulfide is the key precursor for sulfide solid electrolytes.", why="Stated product."),
         w.rel("SUPPLIES", (CO, "Kairos Lithium"), (CO, "Veridian Cells"), ["kl-deal"],
               "Kairos Lithium will supply lithium metal foil to Veridian Cells under an agreement announced May 19, 2026.",
               valid_from="2026-05-19", why="Dated supply agreement."),
         w.rel("PRODUCES", (CO, "Kairos Lithium"), (MAT, "Lithium metal foil"), ["kl-site", "kl-deal"],
               "Kairos Lithium rolls lithium metal foil as thin as 20 micrometres.",
               "Lithium metal foil is the anode material for most solid-state designs.", why="Stated product."),
         w.rel("USES", (TECH, "Sulfide solid electrolyte cell"), (MAT, "Lithium sulfide"), ["sm-supply"],
               "Lithium sulfide is the key precursor for sulfide solid electrolytes.", why="Material dependency of the technology.")],
        checks=[{"label": "Sablecrest → Veridian supply", "outcome": "single_source", "detail": "One trade report with a date; medium confidence."}])

    pilot = w.entity(FAC, "Veridian Reno Pilot Line", ["vc-pilot", "vc-profile"], "Veridian opened its Reno pilot line on January 22, 2026 with 50 MWh of annual capacity.",
                     "The Reno pilot line, opened in January 2026, can produce 50 MWh of cells a year.", desc="Pilot production line in Reno, Nevada.",
                     why="Production facility of a mapped developer.",
                     claims=[w.claim("annual_capacity", "50 MWh", ["vc-pilot", "vc-profile"], "Veridian opened its Reno pilot line on January 22, 2026 with 50 MWh of annual capacity.",
                                     "The Reno pilot line, opened in January 2026, can produce 50 MWh of cells a year.", valid_from="2026-01-22")])
    steps += slice_steps(
        "c0s3", "Facilities and automaker programmes", "Pilot capacity and offtake partners show how real the chain is.",
        "Veridian Cells pilot line capacity automaker partnership solid-state", ["vc-pilot", "am-partner", "am-ir"],
        [pilot, co("Altamira Motors", "altamira.synthetic", "passenger vehicle manufacturer", "Detroit, Michigan", ["am-partner", "am-ir"],
                   why="Automaker with a dated development partnership.")],
        [w.rel("OPERATES", (CO, "Veridian Cells"), (FAC, "Veridian Reno Pilot Line"), ["vc-pilot", "vc-profile"],
               "Veridian opened its Reno pilot line on January 22, 2026 with 50 MWh of annual capacity.",
               "The Reno pilot line, opened in January 2026, can produce 50 MWh of cells a year.", valid_from="2026-01-22", why="Operator of the facility."),
         w.rel("PARTNERS_WITH", (CO, "Altamira Motors"), (CO, "Veridian Cells"), ["am-partner", "am-ir"],
               "Altamira Motors and Veridian Cells announced a joint development agreement for solid-state packs on June 10, 2026.",
               "Our joint development agreement with Veridian Cells, signed June 10, 2026, targets pilot vehicles.", valid_from="2026-06-10",
               why="Dated joint development agreement.")],
        claims=[{"entity": {"type": CO, "name": "Altamira Motors"}, "claim": w.claim(
            "pilot_vehicle_target", "2027", ["am-partner", "am-ir"], "Altamira expects pilot vehicles with solid-state packs in 2027.",
            "Pilot vehicles using solid-state packs are planned for 2027.", valid_from="2026-06-10")}],
        checks=[{"label": "Altamira ↔ Veridian agreement", "outcome": "corroborated", "detail": "Trade press and the automaker's own roadmap update agree on the date."}])
    steps += [
        Step("c0:frontier", "frontier", {"questions": [
            {"question": "Which cell developers use oxide rather than sulfide electrolytes, and who supplies them?", "rationale": "Only the sulfide branch is mapped so far.", "info_gain": 0.85, "est_calls": 6},
            {"question": "Where does Sablecrest Materials source lithium for its lithium sulfide?", "entity": "Sablecrest Materials", "rationale": "The chain stops one tier upstream.", "info_gain": 0.55, "est_calls": 4},
        ]}),
        Step("c0:finish", "finish", {"text": "Mapped the sulfide branch: one cell developer, two material suppliers, a licensor, a pilot line and an "
             "automaker programme, from 10 synthetic documents. The oxide branch is unexplored."}),
    ]
    chapter0 = Chapter("Initial mapping", "Map the sulfide branch of the supply chain", steps)

    oxide = w.entity(TECH, "Oxide ceramic separator", ["nv-report", "bc-site"], "Norrland Volt's cells use an oxide ceramic separator made of LLZO.",
                     "Brightmoor's oxide ceramic separator is a thin LLZO sheet.", desc="Solid separator made of LLZO oxide ceramic.", why="Competing chemistry branch.")
    gf = w.entity(FAC, "Norrland Gigafactory One", ["nv-site", "nv-report"], "Norrland Gigafactory One in Luleå is planned for 8 GWh of annual capacity.",
                  "Gigafactory One, under construction in Luleå, targets 8 GWh a year.", desc="Cell factory under construction in Luleå, Sweden.",
                  why="Largest planned facility in the map.",
                  claims=[w.claim("annual_capacity", "8 GWh (planned)", ["nv-site", "nv-report"], "Norrland Gigafactory One in Luleå is planned for 8 GWh of annual capacity.",
                                  "Gigafactory One, under construction in Luleå, targets 8 GWh a year.")])
    x = [
        Step("c1:status", "status", {"status": "planning"}, pause=0.6),
        Step("c1:plan", "plan", {"objective": "Explore the oxide branch and end-of-life recycling.", "slices": [
            {"title": "Oxide-electrolyte developers", "why": "Top frontier question from the previous run.", "est_calls": 5},
            {"title": "Recycling loop", "why": "Closes the chain from scrap back to lithium.", "est_calls": 3}],
            "est_calls": 8, "note": "Skips 10 documents already read."}),
        Step("c1:researching", "status", {"status": "researching"}, pause=0.4),
    ]
    x += slice_steps(
        "c1s1", "Oxide-electrolyte developers", "Frontier: the unexplored chemistry branch.",
        "oxide ceramic separator LLZO solid-state cell maker gigafactory", ["nv-site", "nv-report", "bc-site", "kl-deal"],
        [co("Norrland Volt", "norrlandvolt.synthetic", "battery cell manufacturer", "Luleå, Sweden", ["nv-site", "nv-report"]), oxide,
         co("Brightmoor Ceramics", "brightmoor.synthetic", "technical ceramics maker", "Dresden, Germany", ["bc-site", "nv-report"]), gf],
        [w.rel("DEVELOPS", (CO, "Norrland Volt"), (TECH, "Oxide ceramic separator"), ["nv-report"],
               "Norrland Volt's cells use an oxide ceramic separator made of LLZO.", why="Developer of the technology."),
         w.rel("SUPPLIES", (CO, "Brightmoor Ceramics"), (CO, "Norrland Volt"), ["nv-report", "bc-site"],
               "Brightmoor Ceramics has supplied LLZO separators to Norrland Volt since February 2026.",
               "Since February 2026 we have delivered LLZO separator sheets to Norrland Volt.", valid_from="2026-02", why="Dated supply relationship."),
         w.rel("OPERATES", (CO, "Norrland Volt"), (FAC, "Norrland Gigafactory One"), ["nv-site", "nv-report"],
               "Norrland Gigafactory One in Luleå is planned for 8 GWh of annual capacity.",
               "Gigafactory One, under construction in Luleå, targets 8 GWh a year.", why="Operator of the facility."),
         w.rel("SUPPLIES", (CO, "Kairos Lithium"), (CO, "Norrland Volt"), ["kl-deal"],
               "The same agreement covers lithium metal foil deliveries to Norrland Volt from 2027.", valid_from="2027",
               why="Shared supplier linking both chemistry branches.")],
        agent="scout", depth="advanced",
        checks=[{"label": "Brightmoor → Norrland supply", "outcome": "corroborated", "detail": "Supplier and an independent report agree on the start date."},
                {"label": "Kairos Lithium supplies both branches", "outcome": "single_source", "detail": "A shared supplier is a concentration risk worth watching."}])
    x += slice_steps(
        "c1s2", "Recycling loop", "Who recovers lithium from production scrap?",
        "solid-state battery scrap lithium recovery recycling agreement", ["tr-site", "tr-deal"],
        [co("Tessera Recycling", "tesserarecycling.synthetic", "battery materials recycler", "Rotterdam, Netherlands", ["tr-site", "tr-deal"])],
        [w.rel("RECYCLES_FOR", (CO, "Tessera Recycling"), (CO, "Norrland Volt"), ["tr-deal"],
               "Tessera Recycling will recover lithium from Norrland Volt production scrap under a contract signed August 12, 2026.",
               valid_from="2026-08-12", why="Dated recycling contract.")],
        checks=[{"label": "Tessera ↔ Norrland contract", "outcome": "single_source", "detail": "One trade report; medium confidence."}])
    x += [Step("c1:finish", "finish", {"text": "Added the oxide branch and a recycling loop: 3 companies, 1 technology and 1 facility from 5 new synthetic documents. "
                                               "Kairos Lithium now supplies both chemistry branches."})]
    chapter1 = Chapter("Expansion", "Explore the oxide branch and recycling", x)

    monitors = {
        "Veridian Reno Pilot Line": [
            {"sources": ["vc-pilot"], "claims": []},
            {"sources": ["vc-pilot", "vc-capacity", "vc-capacity-2"], "claims": [w.claim(
                "annual_capacity", "200 MWh", ["vc-capacity", "vc-capacity-2"],
                "Veridian has expanded its Reno pilot line to 200 MWh of annual capacity as of November 2026.",
                "The Reno pilot line now runs at 200 MWh a year following an expansion completed in November 2026.", valid_from="2026-11")]},
        ],
        "Altamira Motors": [
            {"sources": ["am-partner"], "claims": []},
            {"sources": ["am-partner", "am-delay"], "claims": [w.claim(
                "pilot_vehicle_target", "2028", ["am-delay"], "Altamira Motors now expects pilot vehicles with solid-state packs in 2028, a year later than planned.",
                valid_from="2026-11-09", conf="medium")]},
        ],
    }
    ontology = {
        "entity_types": [
            {"type": CO, "description": "Developer, supplier, automaker or recycler", "color": "sky",
             "attributes": [{"name": "hq_location", "description": "Headquarters"}, {"name": "pilot_vehicle_target", "description": "Target year for pilot vehicles"}]},
            {"type": TECH, "description": "A cell technology or component design", "color": "lime", "attributes": [{"name": "maturity", "description": "Lab, pilot or production"}]},
            {"type": MAT, "description": "A critical input material", "color": "rose", "attributes": []},
            {"type": FAC, "description": "A plant or pilot line", "color": "amber", "attributes": [{"name": "annual_capacity", "description": "Stated annual capacity"}]},
            {"type": INST, "description": "University or research institute", "color": "violet", "attributes": []},
        ],
        "relationship_types": [
            {"type": "DEVELOPS", "source": CO, "target": TECH, "description": "Develops or deploys a technology", "temporal": False},
            {"type": "SUPPLIES", "source": CO, "target": CO, "description": "Dated supply agreement", "temporal": True},
            {"type": "PRODUCES", "source": CO, "target": MAT, "description": "Produces a material", "temporal": False},
            {"type": "USES", "source": TECH, "target": MAT, "description": "Material dependency", "temporal": False},
            {"type": "OPERATES", "source": CO, "target": FAC, "description": "Operates a facility", "temporal": True},
            {"type": "LICENSES_FROM", "source": CO, "target": INST, "description": "Dated technology licence", "temporal": True},
            {"type": "PARTNERS_WITH", "source": CO, "target": CO, "description": "Joint development or offtake", "temporal": True},
            {"type": "RECYCLES_FOR", "source": CO, "target": CO, "description": "Recycling contract", "temporal": True},
        ],
        "multi_valued_predicates": [],
    }
    charter = {
        "question": "Who develops, supplies and licenses what in the solid-state battery supply chain?",
        "scope": "Cell developers, their material suppliers, facilities, licensors and automaker programmes, 2025 onward.",
        "inclusion": ["Supply or licence agreements stated with a date", "Facilities with stated capacity"],
        "exclusion": ["Rumoured partnerships", "Capacity figures without a source"],
        "preferred_domains": ["company technology pages", "trade press", "investor updates"], "excluded_domains": [],
        "time_horizon": "2025 onward",
        "evidence_standard": "Agreements need a dated statement from a party or independent trade press. Planned capacity is labelled as planned.",
        "ambiguities": ["“Solid-state” includes semi-solid designs only if a source says so explicitly."],
        "stop_conditions": ["Budget or call cap reached", "Frontier questions exhausted"],
        "watch_defaults": ["annual_capacity", "pilot_vehicle_target"],
        "coverage_note": "Covers what public sources state; private supply contracts are invisible.",
    }
    return Scenario(
        id="solid-state-battery-supply-chain", name="Solid-state battery supply chain",
        description="Developers, suppliers, facilities and licences in a fictional solid-state battery ecosystem.",
        prompt="Map the solid-state battery supply chain: who develops which cell technologies, who supplies critical materials, which facilities exist, and who partners with whom.",
        charter=charter, ontology=ontology, sources=w.finalize(), chapters=[chapter0, chapter1], monitor_scripts=monitors,
        seed={"reject": [], "keep_pending": ["Kairos Lithium —supplies"],
              "monitors": [("Veridian Reno Pilot Line", "Capacity changes", ["annual_capacity"], 2), ("Altamira Motors", "Programme timing changes", ["pilot_vehicle_target"], 0)]})


SCENARIOS: dict[str, Scenario] = {s.id: s for s in (_series_b(), _battery())}
