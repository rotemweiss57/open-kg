"""Thin Tavily client (search / extract / map / crawl).

REST with TAVILY_API_KEY; without a key, the same calls go to the Tavily MCP server as JSON-RPC
`tools/call` with the server-wide "Sign in with Tavily" token (see openkg/tavily_oauth.py).
Raw httpx rather than the SDK so every call has an explicit timeout, bounded retries with
jittered backoff, a circuit breaker, and an injectable transport for contract tests and
offline use. Parameters follow https://docs.tavily.com/documentation/api-reference.
"""
from __future__ import annotations

import json
import random
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import get_settings
from ..worker.context import TransientError


class ProviderAuthError(Exception):
    pass


class ProviderRequestError(Exception):
    pass


@dataclass
class TavilyResult:
    data: dict[str, Any]
    credits: float
    credits_reported: bool  # False => estimated from the documented rate card


class CircuitBreaker:
    def __init__(self, threshold: int = 5, cooldown: float = 60.0):
        self.threshold, self.cooldown = threshold, cooldown
        self.failures, self.opened_at, self.lock = 0, 0.0, threading.Lock()

    def before(self) -> None:
        with self.lock:
            if self.failures >= self.threshold and time.monotonic() - self.opened_at < self.cooldown:
                raise TransientError("Tavily circuit breaker is open after repeated failures; backing off")

    def record(self, ok: bool) -> None:
        with self.lock:
            if ok:
                self.failures = 0
            else:
                self.failures += 1
                if self.failures >= self.threshold:
                    self.opened_at = time.monotonic()


_breaker = CircuitBreaker()
# Arguments the MCP tools do not accept (their arguments are validated strictly).
_MCP_DROP = {"/search": {"include_usage"}, "/extract": {"chunks_per_source"}}


class TavilyClient:
    def __init__(self, api_key: str | None = None, *, base_url: str | None = None,
                 transport: httpx.BaseTransport | None = None, timeout: float = 60.0, max_retries: int = 3,
                 breaker: CircuitBreaker | None = None, sleep=time.sleep):
        st = get_settings()
        key = api_key or (st.tavily_api_key.get_secret_value() if st.tavily_api_key else "")
        self._mcp = not key
        if self._mcp:
            from ..tavily_oauth import NOT_CONNECTED, access_token, status
            if status() != "oauth":
                raise ProviderAuthError(NOT_CONNECTED)
            self._token = access_token
        self._url = base_url or (st.tavily_mcp_url if self._mcp else st.tavily_base_url)
        # The remote MCP server is stateless but requires some mcp-session-id on every tools/call.
        mcp_headers = {"Accept": "application/json, text/event-stream", "mcp-session-id": uuid.uuid4().hex}
        self._http = httpx.Client(base_url=self._url, transport=transport, timeout=timeout, headers={
            "X-Client-Source": "openkg", **(mcp_headers if self._mcp else {"Authorization": f"Bearer {key}"})})
        self.max_retries, self.breaker, self._sleep = max_retries, breaker or _breaker, sleep

    def _send(self, path: str, body: dict[str, Any]) -> httpx.Response:
        if not self._mcp:
            return self._http.post(path, json=body)
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "tavily_" + path.strip("/"), "arguments": body}}
        resp = self._http.post(self._url, json=rpc, headers={"Authorization": f"Bearer {self._token()}"})
        if resp.status_code == 401:  # revoked or expired early: refresh once
            resp = self._http.post(self._url, json=rpc, headers={"Authorization": f"Bearer {self._token(force_refresh=True)}"})
        return resp

    @staticmethod
    def _decode(resp: httpx.Response) -> dict[str, Any]:
        """Unwraps an MCP tools/call reply (SSE or plain JSON) into the tool's own result."""
        text = resp.text
        msgs = [json.loads(ln[5:]) for ln in text.splitlines() if ln.startswith("data:")] or [json.loads(text)]
        msg = next((m for m in msgs if "result" in m or "error" in m), {"error": {"message": "reply had no result"}})
        if "error" in msg:
            raise ProviderRequestError(f"Tavily MCP error: {str(msg['error'].get('message', msg['error']))[:300]}")
        result = msg["result"]
        if result.get("isError"):
            raise ProviderRequestError(f"Tavily MCP tool error: {result['content'][0]['text'][:300]}")
        return result.get("structuredContent") or json.loads(result["content"][0]["text"])

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        drop = _MCP_DROP.get(path, set()) if self._mcp else set()
        body = {k: v for k, v in body.items() if v is not None and v != [] and k not in drop}
        last = ""
        for attempt in range(self.max_retries + 1):
            self.breaker.before()
            try:
                resp = self._send(path, body)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last = f"{type(e).__name__}: {e}"
                self.breaker.record(False)
            else:
                status, data = resp.status_code, None
                if status == 200:
                    data = self._decode(resp) if self._mcp else resp.json()
                    if self._mcp and "error" in data:  # the tool reports Tavily's HTTP status in its result
                        status = int(data.get("status") or 502)  # no status: treat as a retryable server error
                if status == 200:
                    self.breaker.record(True)
                    return data
                if status in (401, 403):
                    raise ProviderAuthError("Tavily sign-in expired; sign in again in Settings" if self._mcp
                                            else "Tavily rejected the API key")
                if status in (429, 432, 433) or status >= 500:
                    last = f"HTTP {status}"
                    self.breaker.record(status < 500)  # rate limits don't trip the breaker
                    retry_after = resp.headers.get("retry-after")
                    if retry_after and retry_after.isdigit() and attempt < self.max_retries:
                        self._sleep(min(float(retry_after), 30.0))
                        continue
                else:
                    detail = json.dumps(data) if data else resp.text
                    raise ProviderRequestError(f"Tavily {path} failed with HTTP {status}: {detail[:300]}")
            if attempt < self.max_retries:
                self._sleep(min(20.0, 0.8 * 2 ** attempt) * (0.5 + random.random() / 2))
        raise TransientError(f"Tavily {path} unavailable after {self.max_retries + 1} attempts ({last})")

    @staticmethod
    def _credits(data: dict, estimate: float) -> tuple[float, bool]:
        usage = data.get("usage") or {}
        if isinstance(usage.get("credits"), (int, float)):
            return float(usage["credits"]), True
        return estimate, False

    def search(self, query: str, *, search_depth: str = "basic", max_results: int = 5, topic: str = "general",
               time_range: str | None = None, start_date: str | None = None, end_date: str | None = None,
               include_domains: list[str] | None = None, exclude_domains: list[str] | None = None,
               include_raw_content: bool | str = False, country: str | None = None) -> TavilyResult:
        data = self._post("/search", {
            "query": query[:400], "search_depth": search_depth, "max_results": max(1, min(max_results, 20)),
            "topic": topic, "time_range": time_range, "start_date": start_date, "end_date": end_date,
            "include_domains": include_domains, "exclude_domains": exclude_domains,
            "include_raw_content": include_raw_content or None, "country": country, "include_usage": True})
        c, rep = self._credits(data, 2.0 if search_depth == "advanced" else 1.0)
        return TavilyResult(data, c, rep)

    def extract(self, urls: list[str], *, query: str | None = None, extract_depth: str = "basic",
                chunks_per_source: int | None = None) -> TavilyResult:
        urls = urls[:20]
        data = self._post("/extract", {"urls": urls, "query": query, "extract_depth": extract_depth,
                                       "chunks_per_source": chunks_per_source if query else None,
                                       "format": "markdown", "include_usage": True})
        ok = len(data.get("results", []))
        per5 = 2.0 if extract_depth == "advanced" else 1.0
        c, rep = self._credits(data, per5 * -(-ok // 5) if ok else 0.0)
        return TavilyResult(data, c, rep)

    def map(self, url: str, *, instructions: str | None = None, max_depth: int = 1, max_breadth: int = 20,
            limit: int = 30) -> TavilyResult:
        data = self._post("/map", {"url": url, "instructions": instructions, "max_depth": min(max_depth, 3),
                                   "max_breadth": min(max_breadth, 50), "limit": min(limit, 100),
                                   "include_usage": True})
        pages = len(data.get("results", []))
        c, rep = self._credits(data, (2.0 if instructions else 1.0) * max(1, -(-pages // 10)))
        return TavilyResult(data, c, rep)

    def crawl(self, url: str, *, instructions: str | None = None, max_depth: int = 1, max_breadth: int = 10,
              limit: int = 10, extract_depth: str = "basic") -> TavilyResult:
        data = self._post("/crawl", {"url": url, "instructions": instructions, "max_depth": min(max_depth, 2),
                                     "max_breadth": min(max_breadth, 20), "limit": min(limit, 25),
                                     "extract_depth": extract_depth, "include_usage": True})
        pages = len(data.get("results", []))
        est = (2.0 if instructions else 1.0) * max(1, -(-pages // 10)) + (2.0 if extract_depth == "advanced" else 1.0) * -(-pages // 5)
        c, rep = self._credits(data, est)
        return TavilyResult(data, c, rep)

    def close(self) -> None:
        self._http.close()
