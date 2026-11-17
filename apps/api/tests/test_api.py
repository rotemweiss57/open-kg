"""HTTP surface: auth, CSRF, tenancy (IDOR), SSE replay, review, exports, error format."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from openkg.config import get_settings
from openkg.db import session_scope
from openkg.models import Entity, Graph, Run, Source
from openkg.worker.executor import execute_run


@pytest.fixture
def app():
    from openkg.api.app import create_app
    return create_app()


def signup(app, email):
    c = TestClient(app)
    r = c.post("/api/v1/auth/register", json={"email": email, "password": "correct horse battery", "name": email.split("@")[0]})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


def new_graph(c, name="G", scenario="series-b-product-leaders", autonomy="review_first"):
    r = c.post("/api/v1/graphs", json={"name": name, "scenario": scenario, "autonomy_mode": autonomy})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def run_to_end(c, gid):
    r = c.post(f"/api/v1/graphs/{gid}/runs", json={"engine": "fixture"})
    assert r.status_code == 202 and r.json()["status"] == "queued"  # returns immediately with a durable id
    rid = r.json()["id"]
    with session_scope() as s:
        s.get(Run, rid).config = {**s.get(Run, rid).config, "step_delay": 0}
    execute_run(rid)  # the worker, independent of any browser connection
    return rid


def test_requires_auth_and_csrf_and_uses_problem_details(app):
    anon = TestClient(app)
    r = anon.get("/api/v1/graphs")
    assert r.status_code == 401 and r.headers["content-type"].startswith("application/problem+json")
    assert set(r.json()) >= {"title", "status", "detail"}
    c = signup(app, "a@example.com")
    del c.headers["X-CSRF-Token"]
    assert c.post("/api/v1/graphs", json={"name": "x"}).status_code == 403  # cookie alone cannot write
    assert c.get("/api/v1/graphs").status_code == 200
    bad = signup(app, "v@example.com").post("/api/v1/graphs", json={"name": ""})
    assert bad.status_code == 422 and bad.json()["errors"][0]["field"] == "name"


def test_login_logout_and_bad_credentials(app):
    signup(app, "a@example.com")
    c = TestClient(app)
    assert c.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "nope"}).status_code == 401
    assert c.post("/api/v1/auth/login", json={"email": "ghost@example.com", "password": "nope"}).status_code == 401
    r = c.post("/api/v1/auth/login", json={"email": "A@Example.com", "password": "correct horse battery"})
    assert r.status_code == 200 and "HttpOnly" in r.headers["set-cookie"] and "samesite=lax" in r.headers["set-cookie"].lower()
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert c.post("/api/v1/auth/logout").status_code == 204
    assert c.get("/api/v1/auth/me").status_code == 401
    assert signup(app, "b@example.com").post("/api/v1/auth/register", json={"email": "a@example.com", "password": "another long password"}).status_code == 422


def test_demo_login_only_exists_in_demo_mode(app, monkeypatch):
    from openkg.seed import ensure_demo_user
    ensure_demo_user()
    assert TestClient(app).post("/api/v1/auth/demo").status_code == 200
    monkeypatch.setenv("DEMO_MODE", "false")
    get_settings.cache_clear()
    assert TestClient(app).post("/api/v1/auth/demo").status_code == 404
    # the demo account has no password and can never be used through the normal login
    assert TestClient(app).post("/api/v1/auth/login", json={"email": "demo@openkg.local", "password": "!"}).status_code == 401
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DEMO_MODE", "true")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError):
        get_settings()


def test_two_graphs_are_independent_and_persist(app):
    c = signup(app, "a@example.com")
    g1, g2 = new_graph(c, "One"), new_graph(c, "Two", "solid-state-battery-supply-chain")
    run_to_end(c, g1)
    run_to_end(c, g2)
    s1, s2 = (c.get(f"/api/v1/graphs/{g}/snapshot").json() for g in (g1, g2))
    assert len(s1["nodes"]) == 24 and len(s2["nodes"]) == 10
    assert not {n["id"] for n in s1["nodes"]} & {n["id"] for n in s2["nodes"]}
    fresh = TestClient(app, cookies=c.cookies)  # "refresh": a new client with the same session sees the same state
    assert [g["name"] for g in fresh.get("/api/v1/graphs").json()] == ["Two", "One"] or len(fresh.get("/api/v1/graphs").json()) == 2
    ent = s1["nodes"][0]["id"]
    assert c.get(f"/api/v1/graphs/{g1}/entities/{ent}").status_code == 200
    assert c.get(f"/api/v1/graphs/{g2}/entities/{ent}").status_code == 404  # an id from another graph does not resolve


def test_cross_tenant_access_is_denied_everywhere(app):
    alice, mallory = signup(app, "alice@example.com"), signup(app, "mallory@example.com")
    gid = new_graph(alice)
    rid = run_to_end(alice, gid)
    snap = alice.get(f"/api/v1/graphs/{gid}/snapshot").json()
    ent, rel = snap["nodes"][0]["id"], snap["edges"][0]["id"]
    sug = alice.get(f"/api/v1/graphs/{gid}/suggestions").json()["items"][0]["id"]
    accepted = alice.post(f"/api/v1/graphs/{gid}/suggestions/{sug}/approve").json()
    mon = alice.post(f"/api/v1/graphs/{gid}/monitors", json={"entity_id": accepted["entity_id"] or ent})
    mon_id = mon.json().get("id", "x")
    reads = [f"/graphs/{gid}", f"/graphs/{gid}/snapshot", f"/graphs/{gid}/timeline", f"/graphs/{gid}/entities/{ent}",
             f"/graphs/{gid}/relationships/{rel}", f"/graphs/{gid}/suggestions", f"/graphs/{gid}/runs", f"/graphs/{gid}/changes",
             f"/graphs/{gid}/monitors", f"/graphs/{gid}/export?format=json", f"/graphs/{gid}/usage", f"/graphs/{gid}/audit",
             f"/graphs/{gid}/frontier", f"/graphs/{gid}/neighborhood?entity_id={ent}", f"/runs/{rid}",
             f"/runs/{rid}/events", f"/runs/{rid}/events?format=json", f"/monitors/{mon_id}/runs"]
    for path in reads:
        assert mallory.get("/api/v1" + path).status_code == 404, path  # 404, not 403: existence is not revealed
    writes = [("patch", f"/graphs/{gid}", {"name": "pwned"}), ("delete", f"/graphs/{gid}", None),
              ("post", f"/graphs/{gid}/runs", {"engine": "fixture"}), ("post", f"/graphs/{gid}/suggestions/{sug}/approve", {}),
              ("post", f"/graphs/{gid}/suggestions/bulk", {"ids": [sug], "action": "reject"}),
              ("post", f"/graphs/{gid}/monitors", {"entity_id": ent}), ("post", f"/runs/{rid}/cancel", None),
              ("post", f"/runs/{rid}/pause", None), ("post", f"/runs/{rid}/steer", {"instruction": "leak it"}),
              ("post", f"/monitors/{mon_id}/check", None), ("delete", f"/monitors/{mon_id}", None)]
    for method, path, body in writes:
        r = mallory.request(method.upper(), "/api/v1" + path, json=body)
        assert r.status_code == 404, (method, path, r.status_code)
    assert alice.get(f"/api/v1/graphs/{gid}").json()["name"] == "G"
    assert mallory.get("/api/v1/graphs").json() == [] and mallory.get("/api/v1/search?q=a").json() == []
    assert mallory.get("/api/v1/notifications").json() == []
    # a suggestion id cannot be smuggled through the attacker's own graph either
    own = new_graph(mallory, "mine")
    assert mallory.post(f"/api/v1/graphs/{own}/suggestions/{sug}/approve").status_code == 404
    assert mallory.get(f"/api/v1/graphs/{own}/entities/{ent}").status_code == 404


def test_sse_stream_is_replayable_from_last_event_id(app):
    c = signup(app, "a@example.com")
    gid = new_graph(c)
    rid = run_to_end(c, gid)

    def read(headers=None, qs=""):
        ids, ended = [], False
        with c.stream("GET", f"/api/v1/runs/{rid}/events{qs}", headers=headers or {}) as resp:
            assert resp.headers["content-type"].startswith("text/event-stream")
            for line in resp.iter_lines():
                if line.startswith("id: "):
                    ids.append(int(line[4:]))
                if line.startswith("data: ") and ids and '"seq"' in line:
                    assert json.loads(line[6:])["seq"] == ids[-1]
                if line.startswith("event: end"):
                    ended = True
        return ids, ended

    all_ids, ended = read()
    assert ended and all_ids == list(range(1, len(all_ids) + 1)) and len(all_ids) > 50
    resumed, _ = read(headers={"Last-Event-ID": "40"})  # browser reconnect
    assert resumed == all_ids[40:]
    assert read(qs="?after=90")[0] == all_ids[90:]
    polled = c.get(f"/api/v1/runs/{rid}/events?format=json&after={all_ids[-3]}").json()
    assert [e["seq"] for e in polled["events"]] == all_ids[-2:] and polled["done"] is True


def test_review_updates_graph_and_keeps_audit_record(app):
    c = signup(app, "a@example.com")
    gid = new_graph(c)
    run_to_end(c, gid)
    assert c.get(f"/api/v1/graphs/{gid}").json()["stats"]["entities"] == 0  # nothing accepted yet
    assert len(c.get(f"/api/v1/graphs/{gid}/snapshot?include_proposed=false").json()["nodes"]) == 0
    items = c.get(f"/api/v1/graphs/{gid}/suggestions?kind=entity").json()["items"]
    keep, drop = items[0], items[1]
    assert keep["claims"] and keep["claims"][0]["evidence"][0]["source"]["is_synthetic"] is True
    assert c.post(f"/api/v1/graphs/{gid}/suggestions/{keep['id']}/approve").json()["status"] == "approved"
    assert c.post(f"/api/v1/graphs/{gid}/suggestions/{drop['id']}/reject", json={"note": "not relevant"}).json()["status"] == "rejected"
    assert c.post(f"/api/v1/graphs/{gid}/suggestions/{drop['id']}/approve").json()["status"] == "rejected"  # decided once
    nodes = c.get(f"/api/v1/graphs/{gid}/snapshot?include_proposed=false").json()["nodes"]
    assert [n["id"] for n in nodes] == [keep["entity_id"]]
    assert drop["entity_id"] not in {n["id"] for n in c.get(f"/api/v1/graphs/{gid}/snapshot").json()["nodes"]}
    decided = c.get(f"/api/v1/graphs/{gid}/suggestions?status=decided").json()["items"]
    assert any(d["id"] == drop["id"] and d["decision_note"] == "not relevant" for d in decided)  # auditable
    actions = [a["action"] for a in c.get(f"/api/v1/graphs/{gid}/audit").json()]
    assert "suggestion.rejected" in actions and "suggestion.approved" in actions
    low_risk = [s["id"] for s in c.get(f"/api/v1/graphs/{gid}/suggestions").json()["items"] if s["risk"] == "low" and s["kind"] == "entity"][:5]
    assert len(c.post(f"/api/v1/graphs/{gid}/suggestions/bulk", json={"ids": low_risk, "action": "approve"}).json()["decided"]) == len(low_risk)


def test_inspector_shows_evidence_time_and_contested_status(app):
    c = signup(app, "a@example.com")
    gid = new_graph(c)
    run_to_end(c, gid)
    quillon = next(n for n in c.get(f"/api/v1/graphs/{gid}/snapshot").json()["nodes"] if n["name"] == "Quillon Data")
    assert quillon["contested"] is True
    d = c.get(f"/api/v1/graphs/{gid}/entities/{quillon['id']}").json()
    hq = next(a for a in d["attributes"] if a["predicate"] == "hq_location")
    assert hq["current"]["status"] == "contested" and len(hq["history"]) == 1
    assert {e["stance"] for e in hq["current"]["evidence"]} == {"supports", "contradicts"}
    stage = next(a for a in d["attributes"] if a["predicate"] == "funding_stage")
    assert stage["current"]["valid_from"] == "2026-03-19"  # "Series B" is tied to a dated round
    assert "employee_count" in d["unknowns"]
    edge = next(n for n in d["neighbors"] if n["type"] == "LED_ROUND")
    r = c.get(f"/api/v1/graphs/{gid}/relationships/{edge['relationship_id']}").json()
    assert r["valid_from"] == "2026-03-19" and r["claims"][0]["evidence"][0]["excerpt_verified"] is True
    assert "content" not in r["claims"][0]["evidence"][0]["source"]  # full page text never leaves the server


def test_time_machine_snapshot(app):
    c = signup(app, "a@example.com")
    gid = new_graph(c)
    run_to_end(c, gid)
    tl = c.get(f"/api/v1/graphs/{gid}/timeline").json()
    mid = tl["points"][len(tl["points"]) // 2]
    past = c.get(f"/api/v1/graphs/{gid}/snapshot", params={"as_of": mid["at"]}).json()
    assert len(past["nodes"]) == mid["entities"] < 24


def test_export_formats_and_csv_injection(app):
    c = signup(app, "a@example.com")
    gid = new_graph(c)
    run_to_end(c, gid)
    ids = [s["id"] for s in c.get(f"/api/v1/graphs/{gid}/suggestions").json()["items"] if s["kind"] in ("entity", "relationship")]
    c.post(f"/api/v1/graphs/{gid}/suggestions/bulk", json={"ids": ids, "action": "approve"})
    with session_scope() as s:
        s.scalar(select(Entity).where(Entity.name == "Halcyon Ventures")).name = "=HYPERLINK(\"http://evil\")"
    data = c.get(f"/api/v1/graphs/{gid}/export?format=json").json()
    assert data["graph"]["synthetic"] is True and "SYNTHETIC" in data["notice"]
    assert data["claims"][0]["evidence"][0]["source"]["url"].startswith("fixture://")
    csv = c.get(f"/api/v1/graphs/{gid}/export?format=csv").text
    assert "'=HYPERLINK" in csv and "\n=HYPERLINK" not in csv
    assert c.get(f"/api/v1/graphs/{gid}/export?format=graphml").text.startswith("<?xml")
    assert c.get(f"/api/v1/graphs/{gid}/export?format=exe").status_code == 422


def test_delete_graph_removes_all_artifacts(app):
    c = signup(app, "a@example.com")
    gid, other = new_graph(c), new_graph(c, "keep")
    run_to_end(c, gid)
    run_to_end(c, other)
    assert c.delete(f"/api/v1/graphs/{gid}").status_code == 204
    with session_scope() as s:
        assert s.get(Graph, gid) is None
        assert s.scalar(select(func.count()).where(Source.graph_id == gid)) == 0
        assert s.scalar(select(func.count()).where(Entity.graph_id == gid)) == 0
        assert s.scalar(select(func.count()).where(Entity.graph_id == other)) == 24


def test_status_never_exposes_secrets_and_health(app, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-super-secret-value")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-super-secret-value")
    get_settings.cache_clear()
    c = signup(app, "a@example.com")
    body = c.get("/api/v1/system/status").text
    assert "super-secret" not in body
    st = json.loads(body)
    assert st["live_research_available"] is True and st["primary_provider"] == "anthropic"
    assert [p["configured"] for p in st["providers"]] == [True, False, False]
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}
    assert "super-secret" not in json.dumps(c.get("/api/openapi.json").json())


def test_oversized_and_malformed_requests(app):
    c = signup(app, "a@example.com")
    assert c.post("/api/v1/graphs", content=b"x" * 1_100_000, headers={"content-type": "application/json"}).status_code == 413
    assert c.post("/api/v1/charter/propose", json={"prompt": "x" * 5000}).status_code == 422
    gid = new_graph(c)
    assert c.get(f"/api/v1/graphs/{gid}/snapshot?limit=999999").status_code == 422
    r = c.post("/api/v1/charter/propose", json={"prompt": "Map product managers at Series B startups in the US"}).json()
    assert r["generated_by"] == "heuristic" and r["scenario"] == "series-b-product-leaders" and r["ontology"]["entity_types"]
    assert any("sample" in a or "observable" in a for a in r["charter"]["ambiguities"])  # never promises completeness


def test_login_is_rate_limited(app):
    signup(app, "a@example.com")
    c = TestClient(app)
    codes = [c.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "wrong"}).status_code for _ in range(12)]
    assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]


def test_new_graphs_auto_approve_well_evidenced_facts_by_default_and_policy_can_be_applied_later(app):
    c = signup(app, "a@example.com")
    auto = c.post("/api/v1/graphs", json={"name": "auto", "scenario": "series-b-product-leaders"}).json()
    assert auto["autonomy_mode"] == "auto_accept_high"
    run_to_end(c, auto["id"])
    stats = c.get(f"/api/v1/graphs/{auto['id']}").json()["stats"]
    assert stats["entities"] >= 10 and stats["pending_suggestions"] > 0  # single-source and risky items still wait
    decided = c.get(f"/api/v1/graphs/{auto['id']}/suggestions?status=decided").json()["items"]
    assert decided and all(d["status"] == "auto_approved" and d["decided_by_kind"] == "policy" for d in decided)
    assert not any(d["kind"] in ("merge", "contradiction") for d in decided)

    manual = new_graph(c, "manual")
    run_to_end(c, manual)
    page = c.get(f"/api/v1/graphs/{manual}/suggestions").json()
    assert page["policy_eligible"] > 0 and c.get(f"/api/v1/graphs/{manual}").json()["stats"]["entities"] == 0
    applied = c.post(f"/api/v1/graphs/{manual}/policy/apply").json()
    assert applied["approved"] >= page["policy_eligible"]
    assert c.get(f"/api/v1/graphs/{manual}").json()["autonomy_mode"] == "review_first"  # one-off, setting unchanged
    assert c.get(f"/api/v1/graphs/{manual}/suggestions").json()["policy_eligible"] == 0


def test_markdown_export_and_include_proposed(app):
    c = signup(app, "md@example.com")
    gid = new_graph(c)
    run_to_end(c, gid)  # review_first: everything the run found is still only proposed
    md = c.get(f"/api/v1/graphs/{gid}/export", params={"format": "markdown"})
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert md.text.startswith("# ") and "Accepted facts only." in md.text and "SYNTHETIC DEMO DATA" in md.text
    both = c.get(f"/api/v1/graphs/{gid}/export", params={"format": "markdown", "include_proposed": True})
    assert "marked *proposed*" in both.text and "*(proposed)*" in both.text and "## Sources" in both.text
    assert len(both.text) > len(md.text)
    rows = c.get(f"/api/v1/graphs/{gid}/export", params={"format": "csv", "include_proposed": True}).text
    assert ",proposed," in rows


def test_favicons_are_served_only_for_known_public_sources(app, monkeypatch, tmp_path):
    import httpx

    from openkg import favicons
    from openkg.domain.recorder import register_source
    monkeypatch.setattr(favicons, "CACHE", tmp_path)
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.host == "redirect.example.com":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})
        if request.url.host == "svg.example.com":
            return httpx.Response(200, content=b"<svg onload='alert(1)'></svg>")
        return httpx.Response(200, content=png)
    transport = httpx.MockTransport(handler)
    assert favicons.get("news.example.com", transport) == (png, "image/png")
    assert favicons.get("news.example.com", transport) == (png, "image/png") and len(seen) == 1  # cached
    assert favicons.get("redirect.example.com", transport) is None   # a redirect to a private address is refused
    assert favicons.get("svg.example.com", transport) is None        # SVG can carry script: never served
    assert favicons.get("localhost", transport) is None and favicons.get("../etc/passwd", transport) is None
    assert not any("169.254" in u for u in seen)

    c = signup(app, "icons@example.com")
    gid = c.post("/api/v1/graphs", json={"name": "Live"}).json()["id"]
    with session_scope() as s:
        register_source(s, gid, url="https://news.example.com/a", title="A", content="text")
    monkeypatch.setattr(favicons, "get", lambda d, transport=None: (png, "image/png") if d == "news.example.com" else None)
    ok = c.get("/api/v1/favicon/news.example.com")
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/png" and "default-src 'none'" in ok.headers["content-security-policy"]
    assert c.get("/api/v1/favicon/never-researched.example.org").status_code == 404   # not a source in this workspace
    assert signup(app, "other@example.com").get("/api/v1/favicon/news.example.com").status_code == 404  # nor in another one
    assert TestClient(app).get("/api/v1/favicon/news.example.com").status_code == 401
