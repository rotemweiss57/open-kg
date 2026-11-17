"""Site icons for sources, fetched and cached by the server.

The browser asks OpenKG, never a third-party icon service, so nobody outside learns which sites a
workspace researches. This is the only place the server itself fetches from an arbitrary host, so it
is deliberately narrow: only domains that already appear as sources in the caller's workspace, only
`/favicon.ico`, only public addresses (checked after DNS resolution, on every redirect hop), only
small raster images, and never SVG, which could carry script.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from .domain.canonical import is_public_http_url, sha

CACHE = Path(tempfile.gettempdir()) / "openkg-favicons"
MAX_BYTES = 200_000
MISS_TTL = 24 * 3600
_DOMAIN = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")
_MAGIC = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\x00\x00\x01\x00", "image/x-icon"), (b"GIF8", "image/gif"),
          (b"\xff\xd8\xff", "image/jpeg"), (b"RIFF", "image/webp"))


def valid_domain(domain: str) -> bool:
    return len(domain) <= 253 and bool(_DOMAIN.fullmatch(domain))


def sniff(data: bytes) -> str | None:
    for magic, media in _MAGIC:
        if data.startswith(magic) and (media != "image/webp" or data[8:12] == b"WEBP"):
            return media
    return None


def _resolves_public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


_LINK = re.compile(rb"<link\b[^>]*>", re.I)
_REL_ICON = re.compile(rb"rel\s*=\s*[\"']?[^\"'>]*\bicon\b", re.I)
_HREF = re.compile(rb"href\s*=\s*[\"']?([^\"'\s>]+)", re.I)


def _download(client: httpx.Client, url: str, limit: int, check_dns: bool) -> tuple[bytes, str] | None:
    """Body and final URL, following at most three redirects, each hop re-checked as public."""
    for _ in range(4):
        host = urlsplit(url).hostname or ""
        if not is_public_http_url(url) or (check_dns and not _resolves_public(host)):
            return None
        with client.stream("GET", url) as resp:
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                url = urljoin(url, resp.headers["location"])
                continue
            if resp.status_code != 200:
                return None
            data = b""
            for chunk in resp.iter_bytes():
                data += chunk
                if len(data) > limit:
                    break
            return data[:limit + 1], url
    return None


def _fetch(domain: str, transport: httpx.BaseTransport | None = None) -> bytes | None:
    check_dns = transport is None
    with httpx.Client(timeout=4.0, follow_redirects=False, transport=transport,
                      headers={"User-Agent": "Mozilla/5.0 (compatible; OpenKG favicon fetcher)", "Accept": "image/*,text/html;q=0.8"}) as client:
        got = _download(client, f"https://{domain}/favicon.ico", MAX_BYTES, check_dns)
        if got and len(got[0]) <= MAX_BYTES and sniff(got[0]):
            return got[0]
        # No /favicon.ico: read the home page's <link rel="icon"> instead (raster icons only).
        page = _download(client, f"https://{domain}/", 300_000, check_dns)
        if not page:
            return None
        html, base = page
        for tag in _LINK.findall(html)[:40]:
            href = _HREF.search(tag)
            if not href or not _REL_ICON.search(tag):
                continue
            target = urljoin(base, href.group(1).decode("ascii", "ignore"))
            if target.lower().split("?")[0].endswith(".svg"):
                continue
            icon = _download(client, target, MAX_BYTES, check_dns)
            if icon and len(icon[0]) <= MAX_BYTES and sniff(icon[0]):
                return icon[0]
    return None


def get(domain: str, transport: httpx.BaseTransport | None = None) -> tuple[bytes, str] | None:
    """The icon and its media type, or None. Hits and misses are both cached on disk."""
    domain = domain.lower().strip(".")
    if not valid_domain(domain):
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / sha(domain)[:40]
    if path.exists():
        data = path.read_bytes()
        if data:
            return data, sniff(data) or "image/x-icon"
        if time.time() - path.stat().st_mtime < MISS_TTL:
            return None
    try:
        data = _fetch(domain, transport)
    except (httpx.HTTPError, OSError, ValueError):
        data = None
    path.write_bytes(data or b"")
    return (data, sniff(data) or "image/x-icon") if data else None
