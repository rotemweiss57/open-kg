"""Sign in with Tavily: MCP OAuth 2.1 (dynamic client registration, PKCE S256, refresh tokens).

The access token is only valid at the Tavily MCP endpoint, so TavilyClient switches to MCP when no
TAVILY_API_KEY is set (the key always wins). There is one sign-in per server, kept in the database so
the API, worker and scheduler share it. Ported from tavily-cli's oauth.py. No network call is made
inside a database transaction, and tokens are never logged or returned.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
import urllib.parse
from contextlib import nullcontext
from datetime import datetime, timedelta
from typing import Literal

import httpx
from sqlalchemy.orm import Session

from .agent.tavily import ProviderAuthError
from .config import get_settings
from .db import session_scope, utcnow
from .models import TavilyConnection
from .worker.context import TransientError

SCOPES = "openid offline_access"
CALLBACK_PATH = "/api/v1/system/tavily/callback"
ROW = "default"
NOT_CONNECTED = "Tavily is not connected: sign in with Tavily in Settings or set TAVILY_API_KEY"
EXPIRED = "Tavily sign-in expired; sign in again in Settings"

transport: httpx.BaseTransport | None = None  # test hook: every OAuth request goes through it
_refresh_lock = threading.Lock()  # one refresh at a time in this process


class OAuthError(Exception):
    """Sign-in failed; the message is shown on the Settings page."""


def _request(endpoint: str, **kw) -> httpx.Response:
    with httpx.Client(transport=transport, timeout=30.0) as http:
        return http.post(urllib.parse.urljoin(get_settings().tavily_mcp_url, "/" + endpoint), **kw)


def _post(endpoint: str, what: str, **kw) -> dict:
    r = _request(endpoint, **kw)
    if r.status_code >= 500:
        raise TransientError(f"{what} failed (HTTP {r.status_code})")
    if r.status_code >= 400:
        raise OAuthError(f"{what} failed (HTTP {r.status_code}): {r.text.strip()[:280]}")
    try:
        return r.json()
    except ValueError:
        raise OAuthError(f"{what} returned an invalid response (HTTP {r.status_code})") from None


def _tokens(form: dict) -> dict:
    return _post("token", "Tavily token request", data={**form, "resource": get_settings().tavily_mcp_url})


def _store(row: TavilyConnection, t: dict) -> None:
    row.access_token = t["access_token"]
    row.refresh_token = t.get("refresh_token") or row.refresh_token
    row.expires_at = utcnow() + timedelta(seconds=int(t.get("expires_in") or 3600) - 60)


def status(s: Session | None = None) -> Literal["api_key", "oauth"] | None:
    """How live research reaches Tavily, or None when it cannot. Every gate goes through this."""
    if get_settings().tavily_api_key:
        return "api_key"
    with nullcontext(s) if s is not None else session_scope() as s:
        row = s.get(TavilyConnection, ROW)
        return "oauth" if row and row.refresh_token else None


def live_research_available(s: Session | None = None) -> bool:
    return status(s) is not None and bool(get_settings().configured_providers())


def owner(s: Session) -> str | None:
    """The user who connected the current sign-in; only they may replace or disconnect it."""
    row = s.get(TavilyConnection, ROW)
    return row.connected_by_user_id if row and row.refresh_token else None


def allowed_origin(origin: str) -> bool:
    """Tavily sends the authorization code to this origin, so it must be this server: loopback or CORS_ORIGINS."""
    listed = {o.strip().rstrip("/") for o in get_settings().cors_origins.split(",") if o.strip()}
    return urllib.parse.urlparse(origin).hostname in ("localhost", "127.0.0.1", "::1") or origin.rstrip("/") in listed


def begin(origin: str, user_id: str) -> str:
    """Registers OpenKG with Tavily (once per redirect URI) and returns the URL to send the browser to.
    Everything is kept as pending until complete(), so an abandoned sign-in leaves the live one intact."""
    redirect_uri = origin.rstrip("/") + CALLBACK_PATH
    with session_scope() as s:
        row = s.get(TavilyConnection, ROW)
        known = {row.redirect_uri: row.client_id, row.pending_redirect_uri: row.pending_client_id} if row else {}
        client_id = known.get(redirect_uri) or ""
    if not client_id:
        client_id = _post("register", "Registering OpenKG with Tavily", json={
            "client_name": "OpenKG", "redirect_uris": [redirect_uri], "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "token_endpoint_auth_method": "none", "scope": SCOPES}).get("client_id")
        if not client_id:
            raise OAuthError("Registering OpenKG with Tavily returned no client_id")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(32)
    with session_scope() as s:
        row = s.get(TavilyConnection, ROW) or TavilyConnection(id=ROW)
        row.pending_client_id, row.pending_redirect_uri, row.pending_user_id = client_id, redirect_uri, user_id
        row.pending_state, row.pending_verifier = state, verifier
        s.add(row)
    return urllib.parse.urljoin(get_settings().tavily_mcp_url, "/authorize") + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "scope": SCOPES, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256", "resource": get_settings().tavily_mcp_url})


def complete(code: str, state: str, user_id: str) -> None:
    with session_scope() as s:
        row = s.get(TavilyConnection, ROW)
        if not (row and row.pending_state and state and secrets.compare_digest(row.pending_state, state)
                and row.pending_user_id == user_id):
            raise OAuthError("This sign-in link is stale or was started by someone else. Start again from Settings.")
        client_id, redirect_uri = row.pending_client_id, row.pending_redirect_uri
        form = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                "code_verifier": row.pending_verifier, "client_id": client_id}
        row.pending_state = row.pending_verifier = None  # single use
    t = _tokens(form)
    if not t.get("access_token") or not t.get("refresh_token"):
        raise OAuthError("Tavily did not issue an access and refresh token, so the sign-in could not be kept.")
    with session_scope() as s:
        row = s.get(TavilyConnection, ROW)
        if row is None:
            raise OAuthError("Tavily was disconnected while signing in. Start again from Settings.")
        _store(row, t)
        row.refresh_token = t["refresh_token"]
        row.client_id, row.redirect_uri, row.connected_by_user_id, row.connected_at = client_id, redirect_uri, user_id, utcnow()
        row.pending_client_id = row.pending_redirect_uri = row.pending_user_id = None


def _read() -> tuple[str | None, str, datetime | None, str]:
    with session_scope() as s:
        row = s.get(TavilyConnection, ROW)
        if row is None or not row.refresh_token:
            raise ProviderAuthError(NOT_CONNECTED)
        return row.access_token, row.refresh_token, row.expires_at, row.client_id


def _valid(exp: datetime | None) -> bool:
    return exp is not None and exp > utcnow()


def access_token(force_refresh: bool = False) -> str:
    """A valid access token, refreshed when it has expired (or when the MCP server rejected it).
    Read from the database every time, so every process sees refreshes, reconnects and disconnects."""
    token, _, exp, _ = _read()
    if not force_refresh and token and _valid(exp):
        return token
    with _refresh_lock:
        current, refresh, exp, client_id = _read()
        if current and current != token and _valid(exp):  # another thread refreshed meanwhile
            return current
        try:
            t = _tokens({"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id})
        except OAuthError:
            # Refresh tokens rotate: another process (api, worker, scheduler) may have used this one first.
            for _ in range(4):
                time.sleep(0.5)
                current, newer, exp, _ = _read()
                if newer != refresh and current and _valid(exp):
                    return current
            raise ProviderAuthError(EXPIRED) from None
        with session_scope() as s:
            row = s.get(TavilyConnection, ROW)
            if row is None:
                raise ProviderAuthError(NOT_CONNECTED)
            _store(row, t)
            return row.access_token


def disconnect() -> None:
    """Forgets the sign-in, then revokes the refresh token at Tavily (best effort)."""
    with session_scope() as s:
        row = s.get(TavilyConnection, ROW)
        if row is None:
            return
        form = {"token": row.refresh_token, "token_type_hint": "refresh_token", "client_id": row.client_id}
        s.delete(row)
    if form["token"]:
        try:
            # MCP SDK revocation handlers wrongly require client_secret even for public clients.
            if _request("revoke", data=form).status_code == 400:
                _request("revoke", data={**form, "client_secret": ""})
        except httpx.HTTPError:
            pass
