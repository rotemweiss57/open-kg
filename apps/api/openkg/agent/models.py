"""Model routing: roles -> (provider, model), with explicit opt-in fallbacks and metering.

Nothing here invents capabilities: providers are only offered when a key is configured, a
fallback is only used when the operator listed it in MODEL_FALLBACKS, and it only triggers on
availability errors (429 / 5xx / timeouts), never on a successful-but-different answer.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult
from langchain_core.runnables import Runnable
from pydantic import ConfigDict

from ..config import Settings, get_settings

ROLES = ("planner", "researcher", "extractor", "verifier")
# Defaults are documented model IDs; override with MODEL_NAME / MODEL_ROLE_* for anything else.
DEFAULT_MODELS = {
    "anthropic": {"planner": "claude-sonnet-5-5", "researcher": "claude-sonnet-5-5",
                  "extractor": "claude-haiku-5-5", "verifier": "claude-sonnet-5-5"},
    "openai": {r: "gpt-5-mini" for r in ROLES},
    # Chosen for accuracy and cost, not latency. scripts/model_bench.py compares models on your own pages.
    # The auto-router is avoided because per-role choices are more predictable in quality and price.
    "valar": {"planner": "Qwen/Qwen3.5-397B-A17B", "researcher": "openai/gpt-oss-120b",
              "extractor": "Qwen/Qwen3.5-397B-A17B", "verifier": "Qwen/Qwen3.5-397B-A17B"},
}
CAPABILITIES = {
    "anthropic": ["tool_calling", "structured_output", "prompt_caching"],
    "openai": ["tool_calling", "structured_output"],
    "valar": ["tool_calling", "structured_output", "async_background (not used by the agent loop)"],
}


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


class NoProviderError(Exception):
    pass


def _retryable(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return status in (408, 409, 429) or status >= 500
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError, TimeoutError, ConnectionError)):
        return True
    # SDK exception class names vary by version (APITimeoutError, OpenAITimeoutError, ...): match by meaning.
    name = type(exc).__name__.lower()
    if any(k in name for k in ("timeout", "connection", "ratelimit", "overloaded", "internalserver", "serviceunavailable")):
        return True
    # A provider can also fail part-way through a streamed reply. That arrives as an error event inside
    # the stream, which the SDK raises as a plain ValueError carrying the provider's code and message.
    text = str(exc).lower()
    return any(k in text for k in _TRANSIENT_MARKERS)


_TRANSIENT_MARKERS = ("code='api_error'", "code='server_error'", "code='rate_limit", "code='overloaded", "please retry",
                      "internal error occurred", "internal server error", "temporarily unavailable", "overloaded",
                      "rate limit", "too many requests", "upstream connect error", "bad gateway", "gateway timeout")


class FallbackChatModel(BaseChatModel):
    """Tries configured models in order, moving on only for availability errors."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    candidates: list[tuple[str, Any]]  # (spec string, runnable/chat model)
    on_fallback: Callable[[str, str, str], None] | None = None

    @property
    def _llm_type(self) -> str:
        return "openkg-fallback"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Runnable:
        return FallbackChatModel(candidates=[(n, m.bind_tools(tools, **kwargs)) for n, m in self.candidates],
                                 on_fallback=self.on_fallback)

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        # Inner calls inherit the run's callbacks so each real provider call is gated and metered.
        cfg = {"callbacks": list(run_manager.inheritable_handlers)} if run_manager else None
        last: BaseException | None = None
        for i, (name, model) in enumerate(self.candidates):
            try:
                msg = model.invoke(messages, config=cfg, stop=stop, **kwargs)
                if not isinstance(msg, AIMessage):
                    msg = AIMessage(content=str(msg))
                return ChatResult(generations=[ChatGeneration(message=msg)], llm_output={"served_by": name})
            except Exception as e:  # noqa: BLE001
                if not _retryable(e) or i == len(self.candidates) - 1:
                    raise
                last = e
                if self.on_fallback:
                    self.on_fallback(name, self.candidates[i + 1][0], f"{type(e).__name__}: {e}"[:200])
        raise last  # pragma: no cover


class MeterCallback(BaseCallbackHandler):
    """Budget gate before every model call and token accounting after it.

    `raise_error=True` makes LangChain propagate our BudgetExhausted/Cancel signals instead of
    swallowing them, so a capped run stops *before* the next paid call.
    """

    raise_error = True
    run_inline = True

    def __init__(self, before: Callable[[], None], after: Callable[[str, int, int], None],
                 max_call_seconds: float = 0, on_tick: Callable[[], None] | None = None):
        self.before, self.after = before, after
        self.max_call_seconds, self.on_tick = max_call_seconds, on_tick
        self._started: dict[Any, float] = {}
        self._last_tick = 0.0

    def on_chat_model_start(self, serialized, messages, *, run_id=None, **kwargs: Any) -> None:
        self.before()  # cheap gate; also fires for the fallback wrapper, which is harmless
        self._started[run_id] = time.monotonic()

    def on_llm_new_token(self, token, *, run_id=None, **kwargs: Any) -> None:
        """Runs while a reply streams: enforces the per-call time limit and lets pause/cancel
        interrupt a long generation instead of waiting for it to finish."""
        now = time.monotonic()
        started = self._started.get(run_id)
        if self.max_call_seconds and started and now - started > self.max_call_seconds:
            from ..worker.context import TransientError
            raise TransientError(f"Model call exceeded {self.max_call_seconds:.0f}s and was aborted")
        if self.on_tick and now - self._last_tick > 2.0:
            self._last_tick = now
            self.on_tick()

    def on_llm_error(self, error, *, run_id=None, **kwargs: Any) -> None:
        self._started.pop(run_id, None)

    def on_llm_end(self, response: LLMResult, *, run_id=None, **kwargs: Any) -> None:
        self._started.pop(run_id, None)
        for gens in response.generations:
            for gen in gens:
                msg = getattr(gen, "message", None)
                usage = getattr(msg, "usage_metadata", None)
                if (response.llm_output or {}).get("served_by"):
                    continue  # wrapper result; the inner call already reported
                model = (getattr(msg, "response_metadata", None) or {}).get("model_name") or \
                        (getattr(msg, "response_metadata", None) or {}).get("model") or ""
                self.after(model, int((usage or {}).get("input_tokens", 0)), int((usage or {}).get("output_tokens", 0)))


class ModelRouter:
    def __init__(self, settings: Settings | None = None, http_client: httpx.Client | None = None):
        self.st = settings or get_settings()
        self.http_client = http_client  # test hook for wire-level contract tests

    # --- resolution ---------------------------------------------------------------------------

    def primary_provider(self) -> str | None:
        configured = self.st.configured_providers()
        if self.st.model_provider:
            return self.st.model_provider if self.st.model_provider in configured else None
        return configured[0] if configured else None

    def _parse(self, text: str, role: str) -> ModelSpec:
        provider, _, model = text.strip().partition(":")
        return ModelSpec(provider, model or DEFAULT_MODELS[provider][role])

    def spec(self, role: str) -> ModelSpec:
        override = getattr(self.st, f"model_role_{role}", "")
        if override:
            return self._parse(override, role)
        provider = self.primary_provider()
        if provider is None:
            if self.st.model_provider:
                raise NoProviderError(f"MODEL_PROVIDER={self.st.model_provider} but its API key is not set")
            raise NoProviderError("No model provider key configured (OPENAI_API_KEY, ANTHROPIC_API_KEY or VALAR_API_KEY)")
        return ModelSpec(provider, self.st.model_name or DEFAULT_MODELS[provider][role])

    def fallbacks(self, role: str) -> list[ModelSpec]:
        out = []
        for item in filter(None, (x.strip() for x in self.st.model_fallbacks.split(","))):
            spec = self._parse(item, role)
            if spec.provider in DEFAULT_MODELS and self.st.provider_key(spec.provider):
                out.append(spec)  # unconfigured fallbacks are skipped, never a startup blocker
        return out

    # --- construction -------------------------------------------------------------------------

    def _build(self, spec: ModelSpec, max_tokens: int) -> BaseChatModel:
        key = self.st.provider_key(spec.provider)
        if key is None:
            raise NoProviderError(f"{spec.provider} is not configured")
        timeout = self.st.provider_timeout_seconds
        # Streaming keeps long generations (large tool-call payloads) alive: a non-streamed reply that
        # takes longer than the timeout is retried from scratch and can stall a run for minutes.
        stream = self.st.model_streaming
        if spec.provider == "anthropic":
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(model=spec.model, api_key=key, max_tokens=max_tokens, timeout=timeout, max_retries=1,
                                 streaming=stream)
        from langchain_openai import ChatOpenAI
        extra: dict[str, Any] = {"http_client": self.http_client} if self.http_client else {}
        # An explicit output cap matters: without one a model can generate (or reason) indefinitely.
        common = dict(model=spec.model, api_key=key, timeout=timeout, max_retries=1, streaming=stream,
                      max_tokens=max_tokens, **extra)
        if spec.provider == "openai":
            return ChatOpenAI(use_responses_api=False, stream_usage=True, **common)
        if spec.provider == "valar":
            common["base_url"] = self.st.valar_base_url
            if self.st.valar_api_mode == "chat":
                return ChatOpenAI(use_responses_api=False, stream_usage=True, **common)
            # Valar compatibility notes: send the whole conversation each turn, set store=false in
            # agent loops, and never rely on previous_response_id (it is silently dropped).
            return ChatOpenAI(use_responses_api=True, store=False, use_previous_response_id=False,
                              include=["reasoning.encrypted_content"], **common)
        raise NoProviderError(f"Unknown provider {spec.provider}")

    def model(self, role: str, *, max_tokens: int = 16000,
              on_fallback: Callable[[str, str, str], None] | None = None) -> BaseChatModel:
        primary = self.spec(role)
        chain = [primary, *[f for f in self.fallbacks(role) if f != primary]]
        if len(chain) == 1:
            return self._build(primary, max_tokens)  # native model keeps provider features (prompt caching)
        return FallbackChatModel(candidates=[(str(s), self._build(s, max_tokens)) for s in chain],
                                 on_fallback=on_fallback)

    # --- introspection (never returns secrets) --------------------------------------------------

    def status(self) -> list[dict]:
        primary = self.primary_provider()
        out = []
        for p in ("anthropic", "openai", "valar"):
            configured = bool(self.st.provider_key(p))
            note = ""
            if not configured:
                note = "Optional. Not configured." if p == "valar" else "Not configured."
            elif p == primary:
                note = "Primary provider."
            elif any(f.provider == p for f in self.fallbacks("researcher")):
                note = "Fallback for availability errors."
            else:
                note = "Configured, not in use (add to MODEL_FALLBACKS to enable as fallback)."
            model = None
            if configured:
                model = (self.st.model_name if p == primary and self.st.model_name else DEFAULT_MODELS[p]["researcher"])
            out.append({"provider": p, "configured": configured, "model": model,
                        "roles": list(ROLES) if p == primary else [], "capabilities": CAPABILITIES[p] if configured else [],
                        "note": note})
        return out

    def test(self, provider: str) -> tuple[bool, str]:
        """One tiny real request. Costs a few tokens; only called on explicit user action."""
        try:
            spec = ModelSpec(provider, self.st.model_name if provider == self.primary_provider() and self.st.model_name
                             else DEFAULT_MODELS[provider]["extractor"])
            msg = self._build(spec, 16).invoke("Reply with the single word: ok")
            return True, f"{spec} responded ({str(msg.content)[:40]!r})."
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {str(e)[:300]}"


def structured(model: BaseChatModel, schema: type, prompt: str):
    """Ask for output matching `schema` and return (instance, raw message).

    Providers differ in how they deliver structured output. Some return a parsed object; others
    return the JSON as plain text, sometimes after a reasoning block or inside a code fence. Both are
    accepted. A reply that is neither raises, so a caller never mistakes a parsing failure for "the
    model found nothing".
    """
    import json
    import re

    out = model.with_structured_output(schema, include_raw=True).invoke(prompt)
    raw = out.get("raw")
    if out.get("parsed") is not None:
        return out["parsed"], raw
    content = getattr(raw, "content", "")
    if isinstance(content, list):
        content = "\n".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") in ("text", "output_text"))
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(content).strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"The model did not return structured output ({str(out.get('parsing_error'))[:160]})")
    try:
        return schema.model_validate(json.loads(text[start:end + 1])), raw
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"The model returned output that does not match the expected shape ({type(e).__name__})") from e
