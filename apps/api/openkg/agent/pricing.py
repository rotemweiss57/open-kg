"""Model price lookup for cost *estimates*.

Valar publishes per-model prices at GET /v1/models, and its auto-router reports which model
actually served each call, so those calls are priced from the published table (realtime tier,
i.e. an upper bound). Everything else uses the operator-configured per-million-token rates.
"""
from __future__ import annotations

import logging
import threading
import time

import httpx

from ..config import get_settings

log = logging.getLogger("openkg.pricing")
_lock = threading.Lock()
_cache: dict[str, tuple[float, float]] = {}
_fetched_at: float | None = None  # None = never tried (a monotonic clock can legitimately be near zero)
TTL = 3600.0


def _load_valar(transport: httpx.BaseTransport | None = None) -> None:
    global _fetched_at
    st = get_settings()
    _fetched_at = time.monotonic()  # also throttles retries after a failure
    if not st.valar_api_key:
        return
    try:
        with httpx.Client(transport=transport, timeout=10) as client:
            resp = client.get(st.valar_base_url.rstrip("/") + "/models",
                              headers={"Authorization": f"Bearer {st.valar_api_key.get_secret_value()}"})
            resp.raise_for_status()
        for m in resp.json().get("data", []):
            tiers = {t.get("tier"): t for t in m.get("pricing") or []}
            tier = tiers.get("realtime") or next(iter(tiers.values()), None)
            if tier and tier.get("input_per_1m_tokens") is not None:
                _cache[m["id"]] = (float(tier["input_per_1m_tokens"]), float(tier["output_per_1m_tokens"]))
    except Exception as e:  # noqa: BLE001 - pricing must never break a run
        log.warning("could not load Valar pricing: %s", type(e).__name__)


def rates(provider: str, model: str, transport: httpx.BaseTransport | None = None) -> tuple[float, float, str]:
    """(input $/Mtok, output $/Mtok, source)."""
    st = get_settings()
    if provider == "valar":
        with _lock:
            stale = _fetched_at is None or time.monotonic() - _fetched_at > (TTL if _cache else 60)
            if model not in _cache and stale:
                _load_valar(transport)
            if model in _cache:
                return (*_cache[model], "valar_published_realtime")
    return st.model_input_usd_per_mtok, st.model_output_usd_per_mtok, "configured"


def estimate(provider: str, model: str, input_tokens: int, output_tokens: int) -> tuple[float, str]:
    rin, rout, source = rates(provider, model)
    return round((input_tokens * rin + output_tokens * rout) / 1e6, 6), source


def reset() -> None:
    global _fetched_at
    _cache.clear()
    _fetched_at = None
