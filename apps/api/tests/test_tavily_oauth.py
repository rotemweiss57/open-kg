"""Sign in with Tavily: the OAuth flow against a mock authorization server, and the MCP mode of TavilyClient."""
from __future__ import annotations

import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fakes import connect_tavily_oauth, fake_tavily, fake_tavily_mcp, mcp_handler
from fastapi.testclient import TestClient

from openkg import tavily_oauth
from openkg.agent.tavily import CircuitBreaker, ProviderAuthError, TavilyClient
from openkg.config import get_settings
from openkg.db import session_scope, utcnow
from openkg.models import AuditEvent, TavilyConnection
from openkg.tavily_oauth import OAuthError


class AuthServer:
    """Mock https://mcp.tavily.com OAuth endpoints. Refresh tokens rotate on every use."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.n, self.reject_refresh, self.on_refresh = 0, False, lambda: None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if path == "/register" else {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.calls.append((path, body))
        if path == "/register":
            return httpx.Response(201, json={"client_id": f"client-{len(self.calls)}", "token_endpoint_auth_method": "none"})
        if path == "/token":
            if body["grant_type"] == "refresh_token" and self.reject_refresh:
                self.on_refresh()
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.n += 1
            return httpx.Response(200, json={"access_token": f"at-{self.n}", "refresh_token": f"rt-{self.n}", "expires_in": 3600})
        if path == "/revoke":
            return httpx.Response(200)
        return httpx.Response(404)


@pytest.fixture
def auth_server(monkeypatch):
    srv = AuthServer()
    monkeypatch.setattr(tavily_oauth, "transport", httpx.MockTransport(srv))
    monkeypatch.setattr(tavily_oauth.time, "sleep", lambda _: None)
    return srv


def row() -> TavilyConnection | None:
    with session_scope() as s:
        r = s.get(TavilyConnection, "default")
        if r:
            s.expunge(r)
        return r


def test_sign_in_flow_registers_once_and_stores_tokens(auth_server, workspace):
    assert tavily_oauth.status() is None
    uid = workspace["user_id"]
    url = urlparse(tavily_oauth.begin("http://localhost:3100", uid))
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.netloc == "mcp.tavily.com" and url.path == "/authorize"
    assert q["redirect_uri"] == "http://localhost:3100/api/v1/system/tavily/callback" and q["code_challenge_method"] == "S256"
    assert q["resource"] == "https://mcp.tavily.com/mcp" and "offline_access" in q["scope"]
    assert tavily_oauth.status() is None  # pending only

    with pytest.raises(OAuthError, match="stale"):
        tavily_oauth.complete("code", "wrong-state", workspace["user_id"])
    tavily_oauth.begin("http://localhost:3100", uid)  # same redirect URI: the client is reused
    assert [p for p, _ in auth_server.calls] == ["/register"]
    state = row().pending_state
    with pytest.raises(OAuthError, match="someone else"):
        tavily_oauth.complete("code-1", state, "another-user")
    tavily_oauth.begin("http://localhost:3100", uid)
    state = row().pending_state
    tavily_oauth.complete("code-1", state, workspace["user_id"])
    token_call = auth_server.calls[-1][1]
    assert token_call["grant_type"] == "authorization_code" and token_call["code_verifier"] and token_call["client_id"] == "client-1"
    r = row()
    assert (r.access_token, r.refresh_token, r.pending_state, r.connected_by_user_id) == ("at-1", "rt-1", None, workspace["user_id"])
    assert tavily_oauth.status() == "oauth"
    with pytest.raises(OAuthError):  # the state is single use
        tavily_oauth.complete("code-1", state, workspace["user_id"])

    # A second sign-in from another origin that is never finished leaves the live connection untouched.
    tavily_oauth.begin("https://kg.example.com", uid)
    r = row()
    assert (r.client_id, r.redirect_uri, r.refresh_token) == ("client-1", "http://localhost:3100/api/v1/system/tavily/callback", "rt-1")
    assert r.pending_client_id not in (None, "client-1") and tavily_oauth.access_token() == "at-1"


def test_refresh_on_expiry_and_lost_refresh_race(auth_server):
    connect_tavily_oauth(access="old", refresh="rt-0", expires_in=-10)
    assert tavily_oauth.access_token() == "at-1"
    assert auth_server.calls[-1][1]["refresh_token"] == "rt-0" and row().refresh_token == "rt-1"
    assert tavily_oauth.access_token() == "at-1" and len(auth_server.calls) == 1  # cached

    def other_process_refreshed_first():
        with session_scope() as s:
            r = s.get(TavilyConnection, "default")
            r.access_token, r.refresh_token, r.expires_at = "at-other", "rt-other", utcnow() + timedelta(hours=1)

    # Our refresh token was rotated by another process mid-flight: use what that process stored.
    auth_server.reject_refresh, auth_server.on_refresh = True, other_process_refreshed_first
    assert tavily_oauth.access_token(force_refresh=True) == "at-other"
    auth_server.on_refresh = lambda: None
    with pytest.raises(ProviderAuthError, match="sign in again"):
        tavily_oauth.access_token(force_refresh=True)  # nobody else refreshed: the sign-in is gone


def test_disconnect_revokes_and_forgets(auth_server):
    connect_tavily_oauth()
    tavily_oauth.disconnect()
    assert row() is None and tavily_oauth.status() is None
    assert auth_server.calls == [("/revoke", {"token": "rt-1", "token_type_hint": "refresh_token", "client_id": "client-1"})]
    with pytest.raises(ProviderAuthError, match="not connected"):
        TavilyClient()


def test_mcp_mode_maps_tools_and_unwraps_results():
    connect_tavily_oauth()
    calls: list[dict] = []
    t = fake_tavily_mcp(calls)
    res = t.search("acme series b", search_depth="advanced")
    assert res.data["results"] and res.credits == 2.0
    ext = t.extract(["https://news.example.com/acme-series-b"], query="funding", chunks_per_source=3)
    assert ext.credits_reported and ext.credits == 1.0
    assert [c["tool"] for c in calls] == ["tavily_search", "tavily_extract"]
    assert calls[0]["path"] == "/mcp" and calls[0]["auth"] == "Bearer at-1"
    assert "include_usage" not in calls[0]["args"] and "chunks_per_source" not in calls[1]["args"]
    plain = TavilyClient(transport=httpx.MockTransport(mcp_handler(sse=False)), breaker=CircuitBreaker())
    assert plain.map("https://acme-robotics.example.com").data["results"]


def test_mcp_tool_errors_follow_rest_status_handling():
    connect_tavily_oauth()
    with pytest.raises(ProviderAuthError, match="sign in again"):
        fake_tavily_mcp(tool_error={"error": "Search failed", "status": 401, "detail": {}}).search("x")
    calls: list[dict] = []
    from openkg.worker.context import TransientError
    with pytest.raises(TransientError):
        fake_tavily_mcp(calls, tool_error={"error": "Search failed", "status": 429}).search("x")
    assert len(calls) == 4  # retried


def test_http_401_forces_one_refresh(auth_server):
    connect_tavily_oauth(access="revoked", refresh="rt-0")
    seen: list[str] = []
    ok = mcp_handler()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return httpx.Response(401) if request.headers["authorization"] == "Bearer revoked" else ok(request)

    t = TavilyClient(transport=httpx.MockTransport(handler), breaker=CircuitBreaker(), sleep=lambda _: None)
    assert t.search("acme").data["results"] and seen == ["Bearer revoked", "Bearer at-1"]


def test_hosted_origin_must_be_listed_in_cors_origins(monkeypatch):
    assert tavily_oauth.allowed_origin("http://127.0.0.1:3100") and tavily_oauth.allowed_origin("http://[::1]:3100")
    assert not tavily_oauth.allowed_origin("https://kg.example.com")
    monkeypatch.setenv("CORS_ORIGINS", "https://kg.example.com/, https://other.example.com")
    get_settings.cache_clear()
    assert tavily_oauth.allowed_origin("https://kg.example.com") and not tavily_oauth.allowed_origin("https://kg.example.com.evil")


def test_api_key_wins_over_sign_in(monkeypatch):
    connect_tavily_oauth()
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-key")
    get_settings.cache_clear()
    assert tavily_oauth.status() == "api_key"
    calls: list[dict] = []
    from fakes import tavily_handler
    TavilyClient(transport=httpx.MockTransport(tavily_handler(calls)), breaker=CircuitBreaker()).search("acme")
    assert calls[0]["path"] == "/search" and calls[0]["auth"] == "Bearer tvly-key"
    assert fake_tavily().search("acme").data["results"]


def test_api_connect_callback_status_and_no_token_leaks(auth_server, monkeypatch):
    from test_api import signup

    from openkg.api.app import app
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    get_settings.cache_clear()
    c = signup(app, "a@example.com")
    st = c.get("/api/v1/system/status").json()
    assert st["tavily_auth"] is None and not st["tavily_configured"] and not st["live_research_available"]
    assert c.post("/api/v1/system/tavily/connect", json={"origin": "http://evil/x?"}).status_code == 422
    foreign = c.post("/api/v1/system/tavily/connect", json={"origin": "https://evil.example"})
    assert foreign.status_code == 400 and "CORS_ORIGINS" in foreign.json()["detail"]
    anon = TestClient(app).get("/api/v1/system/tavily/callback", params={"code": "c", "state": "s"}, follow_redirects=False)
    assert anon.status_code == 303 and "sign+in+to+OpenKG" in anon.headers["location"]
    assert TestClient(app).post("/api/v1/system/tavily/connect", json={"origin": "http://localhost:3100"}).status_code == 401

    url = c.post("/api/v1/system/tavily/connect", json={"origin": "http://localhost:3100"}).json()["authorize_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    bad = c.get("/api/v1/system/tavily/callback", params={"code": "c", "state": "nope"}, follow_redirects=False)
    assert bad.status_code == 303 and bad.headers["location"].startswith("/settings?tavily=error")
    url = c.post("/api/v1/system/tavily/connect", json={"origin": "http://localhost:3100"}).json()["authorize_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    r = c.get("/api/v1/system/tavily/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/settings?tavily=connected"

    body = c.get("/api/v1/system/status").text
    st = json.loads(body)
    assert st["tavily_auth"] == "oauth" and st["tavily_configured"] and st["live_research_available"]
    assert "at-1" not in body and "rt-1" not in body
    assert "at-1" not in json.dumps(c.get("/api/openapi.json").json())
    other = signup(app, "b@example.com")
    assert other.delete("/api/v1/system/tavily").status_code == 403
    assert other.post("/api/v1/system/tavily/connect", json={"origin": "http://localhost:3100"}).status_code == 403
    assert c.delete("/api/v1/system/tavily").status_code == 204
    assert c.get("/api/v1/system/status").json()["tavily_auth"] is None
    with session_scope() as s:
        assert {a.action for a in s.query(AuditEvent).filter(AuditEvent.action.like("tavily.%"))} == {
            "tavily.connected", "tavily.disconnected"}
