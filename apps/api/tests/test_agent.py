"""Live-path contracts without network or spend: Tavily client, tools, Deep Agents loop, model routing.

These tests use a mock HTTP transport and a scripted chat model. They prove the wiring and the
safety properties; they do NOT prove behaviour against the real providers (see docs/live-verification.md).
"""
import json
import sys

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select

sys.path.insert(0, "tests")
from fakes import NEWS, PAGES, FakeRouter, ScriptedChatModel, fake_tavily, research_script, tavily_handler  # noqa: E402

from openkg.agent.deep import DeepAgentEngine  # noqa: E402
from openkg.agent.models import FallbackChatModel, ModelRouter, NoProviderError  # noqa: E402
from openkg.agent.tavily import CircuitBreaker, ProviderAuthError, TavilyClient  # noqa: E402
from openkg.agent.tools import Toolbox  # noqa: E402
from openkg.config import Settings, get_settings  # noqa: E402
from openkg.db import session_scope  # noqa: E402
from openkg.domain import runs as run_cmd  # noqa: E402
from openkg.models import (  # noqa: E402
    Claim,
    Entity,
    FrontierQuestion,
    Graph,
    ProviderUsage,
    Run,
    RunEvent,
    Source,
    Suggestion,
)
from openkg.worker.context import BudgetExhausted, CancelRequested, RunContext, TransientError  # noqa: E402
from openkg.worker.executor import claim, execute_run  # noqa: E402

ONTOLOGY = {"entity_types": [{"type": "Company", "attributes": [{"name": "hq_location"}, {"name": "funding_stage"}]},
                             {"type": "Person", "attributes": [{"name": "title"}]}],
            "relationship_types": [{"type": "WORKS_AT", "source": "Person", "target": "Company"}]}


@pytest.fixture
def live(make_graph, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    get_settings.cache_clear()
    gid = make_graph(None, ontology=ONTOLOGY, charter={"question": "Who leads product at robotics Series B startups?"})

    def start(**budget) -> str:
        with session_scope() as s:
            return run_cmd.create_run(s, s.get(Graph, gid), engine="deep_agent", confirm_spend=True, budget=budget or None).id
    return gid, start


# --- Tavily client --------------------------------------------------------------------------------


def test_tavily_request_contract_and_credit_accounting():
    calls: list = []
    t = fake_tavily(calls)
    res = t.search("x" * 500, search_depth="advanced", max_results=50, include_domains=["a.com"], time_range="week")
    body = calls[0]["body"]
    assert calls[0]["path"] == "/search" and calls[0]["auth"] == "Bearer tvly-test"
    assert len(body["query"]) == 400 and body["max_results"] == 20 and body["search_depth"] == "advanced"
    assert body["include_domains"] == ["a.com"] and body["time_range"] == "week" and body["include_usage"] is True
    assert "exclude_domains" not in body  # empty/None parameters are not sent
    assert (res.credits, res.credits_reported) == (2.0, True)
    t.extract([NEWS], query="funding")
    assert calls[1]["path"] == "/extract" and calls[1]["body"]["urls"] == [NEWS] and calls[1]["body"]["query"] == "funding"
    assert "chunks_per_source" not in calls[1]["body"]  # only sent when the caller asks for chunking
    assert t.map("https://acme-robotics.example.com", limit=500).credits_reported is False  # estimated when not reported
    assert calls[2]["body"]["limit"] == 100


def test_tavily_retries_transient_errors_then_gives_up_as_transient():
    calls: list = []
    ok = fake_tavily(calls, fail_first=2, status=429).search("q")
    assert len(calls) == 3 and ok.data["results"]
    calls.clear()
    with pytest.raises(TransientError):
        fake_tavily(calls, fail_first=99, status=503).search("q")
    assert len(calls) == 4  # 1 try + 3 bounded retries


def test_tavily_auth_and_bad_request_are_not_retried():
    def handler(status):
        return httpx.MockTransport(lambda r: httpx.Response(status, json={"detail": "nope"}))
    with pytest.raises(ProviderAuthError):
        TavilyClient("bad", transport=handler(401), breaker=CircuitBreaker()).search("q")
    from openkg.agent.tavily import ProviderRequestError
    with pytest.raises(ProviderRequestError):
        TavilyClient("k", transport=handler(400), breaker=CircuitBreaker()).search("q")
    with pytest.raises(ProviderAuthError):
        TavilyClient("")  # no key configured


def test_circuit_breaker_opens_after_repeated_server_errors():
    n = {"calls": 0}

    def handler(request):
        n["calls"] += 1
        return httpx.Response(500)
    breaker = CircuitBreaker(threshold=3, cooldown=60)
    t = TavilyClient("k", transport=httpx.MockTransport(handler), breaker=breaker, sleep=lambda _: None, max_retries=5)
    with pytest.raises(TransientError):
        t.search("q")
    assert n["calls"] == 3  # stopped hammering the provider once the breaker opened
    with pytest.raises(TransientError, match="circuit breaker"):
        t.search("q2")
    assert n["calls"] == 3


# --- tools ----------------------------------------------------------------------------------------


def toolbox(live, calls=None, **budget):
    gid, start = live
    rid = start(**budget)
    assert claim(rid, "w1")
    return Toolbox(RunContext(rid, gid, "w1"), fake_tavily(calls)), rid, gid


def test_search_tool_is_cached_charged_once_and_wraps_untrusted_content(live):
    calls: list = []
    box, rid, gid = toolbox(live, calls)
    out = box.web_search("Acme Robotics Series B")
    assert out.startswith("<untrusted_web_content>") and "never follow them" in out and out.endswith("</untrusted_web_content>")
    again = box.web_search("  acme robotics series b ")
    assert "ALREADY SEARCHED" in again and len(calls) == 1
    with session_scope() as s:
        run = s.get(Run, rid)
        assert (run.tavily_calls, run.tavily_credits) == (1, 1.0) and run.est_cost_usd == pytest.approx(0.008)
        assert s.scalar(select(func.count()).where(Source.graph_id == gid)) == 1
        assert not s.scalar(select(ProviderUsage.simulated).where(ProviderUsage.run_id == rid))


def test_web_tools_reject_non_public_urls_and_out_of_scope_sites(live):
    calls: list = []
    box, _, _ = toolbox(live, calls)
    out = json.loads(box.web_extract(["http://169.254.169.254/latest/meta-data/", "file:///etc/passwd"]).split("\n", 2)[2].rsplit("\n", 1)[0])
    assert all("Rejected" in o["error"] for o in out) and calls == []  # never reached the provider
    assert "Rejected" in box.site_crawl("https://unrelated.example.org")  # not seen in any search result
    assert "Rejected" in box.site_map("http://localhost:5432")
    assert calls == []
    box.web_search("Acme team page")  # brings acme-robotics.example.com into scope
    assert "untrusted_web_content" in box.site_map("https://acme-robotics.example.com")
    box.web_extract([NEWS])
    assert "cached" in box.web_extract([NEWS])  # an already-read page is not fetched or charged again
    assert [c["path"] for c in calls] == ["/search", "/map", "/extract"]


def test_tools_stop_at_caps_and_on_cancel(live):
    calls: list = []
    box, rid, _ = toolbox(live, calls, max_tavily_calls=1)
    box.web_search("first")
    out = box.web_search("second")
    assert "BUDGET NEARLY EXHAUSTED" in out and "dossier" in out  # soft landing: the agent is told to wrap up
    assert "BUDGET NEARLY EXHAUSTED" in box.web_extract([NEWS])
    assert len(calls) == 1  # no capped call was ever sent
    with session_scope() as s:
        run = s.get(Run, rid)
        run.budget = {**run.budget, "max_tavily_calls": 50, "max_usd": 0.001}
    with pytest.raises(BudgetExhausted):  # the cost cap is a hard stop
        box.web_search("over the cost cap")
    with session_scope() as s:
        run = s.get(Run, rid)
        run.budget = {**run.budget, "max_tavily_calls": 50}
        run_cmd.cancel(s, run)
    with pytest.raises(CancelRequested):
        box.web_search("third")
    with pytest.raises(CancelRequested):
        box.record_candidate_entities([])
    assert len(calls) == 1


def test_recording_tool_enforces_ontology_and_evidence(live):
    from fakes import ACME

    from openkg.domain.recorder import CandidateEntity
    box, _, gid = toolbox(live)
    out = box.record_candidate_entities([CandidateEntity.model_validate(ACME)])
    assert "skipped" in out and "read the page first" in out  # cited page was never retrieved
    box.web_extract([NEWS])
    out = box.record_candidate_entities([CandidateEntity.model_validate(ACME),
                                         CandidateEntity.model_validate({**ACME, "type": "Weapon", "name": "X"})])
    assert "Acme Robotics: created" in out and "not in the ontology" in out
    with session_scope() as s:
        assert [e.name for e in s.scalars(select(Entity).where(Entity.graph_id == gid))] == ["Acme Robotics"]


# --- the Deep Agents loop ---------------------------------------------------------------------------


def engine(script=research_script, calls=None, saver=None):
    router = FakeRouter(script)
    return DeepAgentEngine(router=router, tavily=fake_tavily(calls), checkpointer=saver or InMemorySaver()), router


def test_deep_agent_run_produces_cited_candidates_and_meters_usage(live):
    gid, start = live
    rid, calls = start(), []
    eng, router = engine(calls=calls)
    assert execute_run(rid, engine=eng) == "done"
    with session_scope() as s:
        run = s.get(Run, rid)
        assert run.status == "awaiting_review" and "sample" in run.summary
        assert run.model_calls == len(router.llm.calls) > 10 and run.input_tokens == run.model_calls * 1000
        assert run.tavily_calls == 4 and run.est_cost_usd > 0
        types = [e.type for e in s.scalars(select(RunEvent).where(RunEvent.run_id == rid).order_by(RunEvent.seq))]
        for expected in ("slice.started", "search.performed", "source.read", "candidate.entity", "candidate.relationship", "frontier.added", "run.summary"):
            assert expected in types
        sugs = list(s.scalars(select(Suggestion).where(Suggestion.graph_id == gid)))
        assert sorted(x.kind for x in sugs) == ["entity", "entity", "relationship"] and all(x.status == "pending" for x in sugs)
        title = s.scalar(select(Claim).where(Claim.predicate == "title"))
        assert title.confidence == "high" and title.valid_from == "2025-01"  # verifier's independent source lifted it
        assert s.scalar(select(func.count()).select_from(FrontierQuestion)) == 1
        # the prompt injection planted in the page stayed data: nothing about "Evil Corp" was recorded
        assert s.scalar(select(func.count()).where(Entity.name.like("%Evil%"))) == 0
    injected = [c for c in calls if c["path"] == "/extract"]
    assert injected and "IGNORE ALL PREVIOUS INSTRUCTIONS" in PAGES[NEWS]["text"]


def test_deep_agent_stops_at_model_call_cap_before_the_next_paid_call(live):
    _, start = live
    rid = start(max_model_calls=4)
    eng, router = engine()
    assert execute_run(rid, engine=eng) == "budget_exhausted"
    with session_scope() as s:
        run = s.get(Run, rid)
        assert run.status == "budget_exhausted" and run.model_calls == 4 == len(router.llm.calls)


def test_deep_agent_cancel_mid_run_starts_no_new_steps(live):
    _, start = live
    rid = start()

    def script(system, done, messages):
        if "entity investigator" in system and len(done) == 1:  # user hits cancel while the investigator works
            with session_scope() as s:
                run_cmd.cancel(s, s.get(Run, rid))
        return research_script(system, done, messages)
    calls: list = []
    eng, router = engine(script, calls)
    assert execute_run(rid, engine=eng) == "cancelled"
    with session_scope() as s:
        assert s.get(Run, rid).status == "cancelled"
        assert s.scalar(select(func.count()).select_from(Suggestion)) == 0  # the record tool refused to run
    n_model, n_http = len(router.llm.calls), len(calls)
    assert execute_run(rid, engine=eng) == "skipped" and (len(router.llm.calls), len(calls)) == (n_model, n_http)


def test_deep_agent_resumes_from_checkpoint_after_worker_death_without_duplicates(live):
    gid, start = live
    rid, saver, calls = start(), InMemorySaver(), []
    state = {"armed": True}

    def dying(system, done, messages):
        if state["armed"] and "skeptical verifier" in system and len(done) == 2:
            state["armed"] = False
            raise RuntimeError("worker process killed")
        return research_script(system, done, messages)
    eng, _ = engine(dying, calls, saver)
    assert execute_run(rid, engine=eng) == "failed"
    with session_scope() as s:
        before = s.scalar(select(func.count()).select_from(Suggestion))
        searches = s.get(Run, rid).tavily_calls
        assert before == 3
        run = s.get(Run, rid)
        run.status, run.finished_at, run.error = "resuming", None, ""  # reconciler re-dispatch
    eng2, _ = engine(research_script, calls, saver)  # a different worker, same Postgres-backed checkpoint
    assert execute_run(rid, engine=eng2) == "done"
    with session_scope() as s:
        run = s.get(Run, rid)
        assert run.status == "awaiting_review"
        assert s.scalar(select(func.count()).select_from(Suggestion)) == 3  # no duplicate facts
        assert s.scalar(select(func.count()).where(Entity.graph_id == gid)) == 2
        assert run.tavily_calls == searches  # the re-issued verifier task hit the per-run search/read cache
        assert s.scalar(select(func.count()).where(RunEvent.type == "run.resumed")) == 1


def test_steering_reaches_the_coordinator(live):
    from openkg.models import ResearchNote
    gid, start = live
    rid = start()
    with session_scope() as s:
        s.add(ResearchNote(graph_id=gid, run_id=rid, kind="steer", content="Prioritise Denver companies"))
    seen: list[str] = []

    def script(system, done, messages):
        if "research coordinator" in system:
            seen.extend(m.content for m in messages if isinstance(m, HumanMessage))
        return research_script(system, done, messages)
    eng, _ = engine(script)
    execute_run(rid, engine=eng)
    assert any("Prioritise Denver companies" in t and "graph owner" in t for t in seen)
    with session_scope() as s:
        assert s.scalar(select(ResearchNote.kind).where(ResearchNote.content.like("Prioritise%"))) == "steer_delivered"


# --- model routing ------------------------------------------------------------------------------------


def settings(**kw) -> Settings:
    base = dict(tavily_api_key="t", openai_api_key=None, anthropic_api_key=None, valar_api_key=None, model_provider="",
                model_name="", model_fallbacks="", model_streaming=False)
    return Settings(_env_file=None, **{**base, **kw})


def test_works_with_any_single_provider_and_valar_is_optional():
    assert ModelRouter(settings(openai_api_key="sk-o")).spec("planner").provider == "openai"
    a = ModelRouter(settings(anthropic_api_key="sk-a"))
    assert str(a.spec("extractor")) == "anthropic:claude-haiku-5-5" and a.model("planner").__class__.__name__ == "ChatAnthropic"
    assert ModelRouter(settings(valar_api_key="v")).spec("researcher").model == "openai/gpt-oss-120b"
    none = ModelRouter(settings())
    with pytest.raises(NoProviderError):
        none.spec("planner")
    # a broken/missing Valar key never blocks another provider, even when listed as a fallback
    r = ModelRouter(settings(anthropic_api_key="sk-a", model_fallbacks="valar,openai"))
    assert r.fallbacks("planner") == [] and r.model("planner").__class__.__name__ == "ChatAnthropic"
    wrong = ModelRouter(settings(model_provider="valar", anthropic_api_key="sk-a"))
    with pytest.raises(NoProviderError, match="MODEL_PROVIDER=valar"):
        wrong.spec("planner")  # explicit choice is honoured, not silently replaced
    assert [p["configured"] for p in r.status()] == [True, False, False]
    assert str(ModelRouter(settings(openai_api_key="k", model_role_verifier="openai:gpt-x")).spec("verifier")) == "openai:gpt-x"


class Flaky(ScriptedChatModel):
    error: Exception | None = None
    calls: list = []

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        if self.error:
            raise self.error
        return super()._generate(messages, stop, run_manager, **kw)

    def bind_tools(self, tools, **kw):
        return self


class Status(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.status_code = code


def test_fallback_only_on_availability_errors_and_is_reported():
    seen: list = []
    ok = Flaky(script=lambda *a: "from fallback")
    chain = FallbackChatModel(candidates=[("openai:a", Flaky(script=lambda *a: "primary", error=Status(429))), ("anthropic:b", ok)],
                              on_fallback=lambda *a: seen.append(a))
    assert chain.invoke("hi").content == "from fallback" and seen[0][:2] == ("openai:a", "anthropic:b")
    assert chain.bind_tools([]).invoke("hi").content == "from fallback"
    for code in (400, 401, 403):  # the request itself is wrong: switching providers would hide a real problem
        bad = FallbackChatModel(candidates=[("openai:a", Flaky(script=lambda *a: "x", error=Status(code))), ("anthropic:b", ok)])
        with pytest.raises(Status):
            bad.invoke("hi")
    both = FallbackChatModel(candidates=[("a", Flaky(script=lambda *a: "x", error=Status(503))), ("b", Flaky(script=lambda *a: "x", error=Status(503)))])
    with pytest.raises(Status):
        both.invoke("hi")
    r = ModelRouter(settings(openai_api_key="k", anthropic_api_key="a", model_provider="openai", model_fallbacks="anthropic"))
    assert isinstance(r.model("planner"), FallbackChatModel) and [str(f) for f in r.fallbacks("planner")] == ["anthropic:claude-sonnet-5-5"]


def test_valar_wire_format_matches_its_compatibility_notes():
    """Verifies what actually goes over the wire, instead of assuming ChatOpenAI picks the right API mode."""
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get("authorization"), json.loads(request.content)))
        if request.url.path.endswith("/responses"):
            return httpx.Response(200, json={"id": "resp_1", "object": "response", "created_at": 1, "status": "completed", "model": "openai/gpt-oss-120b",
                                             "output": [{"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "web_search", "arguments": "{\"query\": \"acme\"}", "status": "completed"}],
                                             "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}, "parallel_tool_calls": True, "tool_choice": "auto", "tools": []})
        return httpx.Response(200, json={"id": "c1", "object": "chat.completion", "created": 1, "model": "m",
                                         "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
                                         "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    tool = {"type": "function", "function": {"name": "web_search", "description": "search", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}}

    msg = ModelRouter(settings(valar_api_key="valar-key"), http_client=client).model("researcher").bind_tools([tool]).invoke(
        [HumanMessage("find acme"), AIMessage("thinking"), HumanMessage("go on")])
    url, auth, body = seen[-1]
    assert url == "https://api.valarhq.ai/v1/responses" and auth == "Bearer valar-key"
    assert body["store"] is False and "previous_response_id" not in body and "conversation" not in body
    assert len(body["input"]) == 3  # the whole conversation is sent explicitly on every turn
    assert body["tools"][0]["name"] == "web_search" and body["model"] == "openai/gpt-oss-120b"
    assert msg.tool_calls[0]["name"] == "web_search" and msg.usage_metadata["input_tokens"] == 11

    chat = ModelRouter(settings(valar_api_key="valar-key", valar_api_mode="chat"), http_client=client).model("researcher").invoke("hi")
    assert seen[-1][0] == "https://api.valarhq.ai/v1/chat/completions" and chat.content == "ok"
    ModelRouter(settings(openai_api_key="sk-o"), http_client=client).model("researcher").invoke("hi")
    assert seen[-1][0] == "https://api.openai.com/v1/chat/completions" and seen[-1][1] == "Bearer sk-o"


def test_tavily_mock_handler_is_exercised():
    assert tavily_handler()(httpx.Request("POST", "https://api.tavily.com/nope", json={})).status_code == 404


def test_fallback_wrapper_meters_the_provider_that_actually_served():
    from openkg.agent.models import MeterCallback
    gates, usage = [], []
    meter = MeterCallback(before=lambda: gates.append(1), after=lambda model, tin, tout: usage.append((tin, tout)))
    chain = FallbackChatModel(candidates=[("openai:a", Flaky(script=lambda *a: "x", error=Status(500))),
                                          ("anthropic:b", Flaky(script=lambda *a: "served"))])
    assert chain.invoke("hi", config={"callbacks": [meter]}).content == "served"
    assert usage == [(1000, 100)] and len(gates) >= 2  # one metered call, gated before each attempt


def test_valar_calls_are_priced_from_the_published_table(monkeypatch):
    from openkg.agent import pricing
    monkeypatch.setenv("VALAR_API_KEY", "valar-key")
    get_settings.cache_clear()
    pricing.reset()
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        return httpx.Response(200, json={"data": [
            {"id": "cheap/model", "pricing": [{"tier": "standard", "input_per_1m_tokens": 0.1, "output_per_1m_tokens": 0.4},
                                              {"tier": "realtime", "input_per_1m_tokens": 0.2, "output_per_1m_tokens": 0.8}]},
            {"id": "valar-auto-balanced", "pricing": []}]})
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(pricing.time, "monotonic", lambda: 5.0)  # a freshly booted machine, e.g. a CI runner
    assert pricing.rates("valar", "cheap/model", transport) == (0.2, 0.8, "valar_published_realtime")  # upper-bound tier
    assert pricing.rates("valar", "cheap/model", transport)[2] == "valar_published_realtime" and len(seen) == 1  # cached
    assert pricing.rates("valar", "valar-auto-balanced", transport)[2] == "configured"  # router alias has no price
    assert pricing.rates("anthropic", "cheap/model")[2] == "configured"
    assert pricing.estimate("valar", "cheap/model", 1_000_000, 500_000) == (0.6, "valar_published_realtime")
    pricing.reset()


def test_only_the_coordinator_can_write_the_run_dossier(live):
    box, rid, _ = toolbox(live)
    box.scoped("scout").record_research_note("scout scratch", kind="dossier")
    with session_scope() as s:
        assert s.get(Run, rid).summary == ""
    box.record_research_note("final dossier", kind="dossier")
    with session_scope() as s:
        assert s.get(Run, rid).summary == "final dossier"


def test_models_stream_by_default_so_long_generations_do_not_time_out():
    r = ModelRouter(Settings(_env_file=None, valar_api_key="v", anthropic_api_key="a", model_provider="valar"))
    assert r.model("planner").streaming is True
    assert ModelRouter(Settings(_env_file=None, anthropic_api_key="a")).model("planner").streaming is True


def test_timeouts_are_retryable_whatever_the_sdk_calls_them():
    from openkg.agent.models import _retryable
    for name in ("OpenAITimeoutError", "APITimeoutError", "APIConnectionError", "RateLimitError", "OverloadedError"):
        assert _retryable(type(name, (Exception,), {})("boom")), name
    assert not _retryable(type("BadRequestError", (Exception,), {})("boom"))
    assert not _retryable(ValueError("nope"))


def test_provider_timeout_becomes_a_backed_off_retry_not_a_failure(live):
    _, start = live
    rid = start()
    state = {"armed": True}

    def script(system, done, messages):
        if state["armed"] and "entity investigator" in system:
            state["armed"] = False
            raise type("OpenAITimeoutError", (Exception,), {})("Request timed out.")
        return research_script(system, done, messages)
    saver = InMemorySaver()
    eng, _ = engine(script, saver=saver)
    assert execute_run(rid, engine=eng) == "retry"
    with session_scope() as s:
        run = s.get(Run, rid)
        assert run.status == "resuming" and run.attempt == 1
        run.not_before = None
    assert execute_run(rid, engine=engine(research_script, saver=saver)[0]) == "done"


def test_runaway_generation_is_aborted_and_cancel_interrupts_streaming():
    from openkg.agent.models import MeterCallback
    ticks = []
    meter = MeterCallback(before=lambda: None, after=lambda *a: None, max_call_seconds=0.05, on_tick=lambda: ticks.append(1))
    meter.on_chat_model_start({}, [], run_id="r1")
    meter.on_llm_new_token("a", run_id="r1")
    assert ticks == [1]  # control check runs while tokens stream
    import time as _t
    _t.sleep(0.08)
    with pytest.raises(TransientError, match="exceeded"):
        meter.on_llm_new_token("b", run_id="r1")

    def cancelled():
        raise CancelRequested()
    stop = MeterCallback(before=lambda: None, after=lambda *a: None, on_tick=cancelled)
    stop.on_chat_model_start({}, [], run_id="r2")
    with pytest.raises(CancelRequested):
        stop.on_llm_new_token("a", run_id="r2")
    assert meter.raise_error is True


def test_agent_is_told_which_quotes_failed_verification(live):
    from fakes import ACME

    from openkg.domain.recorder import CandidateEntity
    box, _, _ = toolbox(live)
    box.web_extract([NEWS])
    bad = {**ACME, "claims": [{"predicate": "employee_count", "value": 500, "evidence": [{"url": NEWS, "excerpt": "Acme Robotics employs five hundred people worldwide"}]}]}
    out = box.record_candidate_entities([CandidateEntity.model_validate(bad)])
    assert "WARNING: 1 quote(s) were NOT found verbatim" in out and "five hundred people" in out
    assert "WARNING" not in box.record_candidate_entities([CandidateEntity.model_validate(ACME)])


def test_web_tools_land_softly_when_cost_or_model_budget_is_nearly_spent(live):
    """Regression from live runs: every run died at the cost cap before the agent could summarise."""
    calls: list = []
    box, rid, _ = toolbox(live, calls, max_tavily_calls=50)
    with session_scope() as s:
        run = s.get(Run, rid)
        run.budget = {**run.budget, "max_usd": 1.0, "max_model_calls": 100}
        run.est_cost_usd = 0.86
    out = box.web_search("anything")
    assert "BUDGET NEARLY EXHAUSTED" in out and calls == []  # no paid call, and the run is not killed
    with session_scope() as s:
        run = s.get(Run, rid)
        run.est_cost_usd, run.model_calls = 0.10, 85
        note = [e.payload["text"] for e in s.scalars(select(RunEvent).where(RunEvent.run_id == rid, RunEvent.type == "note"))]
    assert any("cost cap is spent" in t for t in note)  # the reason is visible in mission control
    assert "BUDGET NEARLY EXHAUSTED" in box.web_extract([NEWS]) and calls == []
    box.record_research_note("still allowed to write the summary", kind="dossier")  # recording tools stay open


def test_subagents_hand_back_control_at_the_budget_reserve(live):
    """So the coordinator can still rank open questions and write the summary before the hard cap."""
    from openkg.agent.deep import ReserveMiddleware
    box, rid, _ = toolbox(live, [], max_tavily_calls=50)
    guard = ReserveMiddleware(box.ctx)
    with session_scope() as s:
        run = s.get(Run, rid)
        run.budget = {**run.budget, "max_usd": 1.0, "max_model_calls": 100}
        run.est_cost_usd = 0.5
    assert guard.before_model({"messages": []}, None) is None
    with session_scope() as s:
        s.get(Run, rid).est_cost_usd = 0.93
    stop = guard.before_model({"messages": []}, None)
    assert stop["jump_to"] == "end" and "dossier" in stop["messages"][0].content
    with session_scope() as s:
        run = s.get(Run, rid)
        run.est_cost_usd, run.model_calls = 0.1, 95
    assert guard.before_model({"messages": []}, None)["jump_to"] == "end"
    assert "Do not delegate again" in box.get_remaining_budget()


def test_graph_titles_never_end_mid_phrase():
    from openkg.agent.charter import heuristic
    assert heuristic("Companies founded on Mars in 1802 and their investors").name == "Companies founded on Mars in 1802 and their investors"
    assert not heuristic("Map every company that was founded on Mars in the year 1802 and all of their many investors and the").name.endswith(("the", "and"))
    assert heuristic("Map the executive leadership team of Datadog: who holds each role").name == "The executive leadership team of Datadog"
    assert heuristic("x y").name == "X y"
    assert heuristic("Account map of Stripe for an enterprise sales team: executives and investors").name == "Account map of Stripe for an enterprise sales team"
    assert heuristic("AI coding assistant products, the companies behind them, their founders and their latest funding").name == "AI coding assistant products"


def test_deal_questions_get_an_ontology_that_can_hold_a_deal():
    """Regression from a live run: an acquisitions question got a generic ontology with no way to say who bought whom."""
    from openkg.agent.charter import heuristic
    o = heuristic("AI company acquisitions announced in 2026: the acquirer, the target, the price and the date").ontology
    assert {"ACQUIRED", "BUYER_IN", "TARGET_IN"} <= {r.type for r in o.relationship_types}
    assert "price" in {a.name for t in o.entity_types if t.type == "Deal" for a in t.attributes}
    people = heuristic("Figma's leadership team and their previous companies").ontology
    assert "PREVIOUSLY_AT" in {r.type for r in people.relationship_types}


def test_default_database_is_one_absolute_path():
    """Regression: the CLI and `make dev` used different SQLite files depending on the working directory."""
    from openkg.config import DEFAULT_SQLITE_URL
    assert DEFAULT_SQLITE_URL.startswith("sqlite:////") and DEFAULT_SQLITE_URL.endswith("/.data/openkg.db")


def test_page_cache_is_question_aware(live):
    """Regression from a live run: a page read for one question was served from cache for another,
    so the model saw passages that did not answer it and quoted from memory instead."""
    calls: list = []
    box, _, _ = toolbox(live, calls)
    box.web_extract([NEWS], question="who led the round")
    assert "cached" in box.web_extract([NEWS], question="Who led the round")      # same question: free
    assert "cached" not in box.web_extract([NEWS], question="who joined as VP of Product")  # new question: fetched
    assert [c["path"] for c in calls] == ["/extract", "/extract"]
    box.web_extract([NEWS])                                                       # the whole page...
    assert "cached" in box.web_extract([NEWS], question="anything else at all")   # ...answers any question
    assert [c["path"] for c in calls] == ["/extract"] * 3


def test_cached_long_pages_show_the_passage_that_answers_the_question():
    filler = "\n\n".join(f"Paragraph {i} about unrelated logistics and weather conditions." * 6 for i in range(40))
    page = f"Acme Robotics company profile.\n\n{filler}\n\nThe Series B was led by Summit Peak Ventures in March 2026.\n\n{filler}"
    shown = Toolbox._focus(page, "who led the Series B round")
    assert "Summit Peak Ventures" in shown and shown.startswith("Acme Robotics company profile.") and len(shown) <= 5000
    assert "Summit Peak Ventures" not in Toolbox._focus(page, None)  # without a question: the start of the page
    assert Toolbox._focus("short page", "anything") == "short page"


def test_mid_stream_provider_errors_are_retried_not_fatal():
    """Regression from a live run: an error event inside a streamed reply failed the run outright."""
    from openkg.agent.models import _retryable
    assert _retryable(ValueError("ResponseError(code='api_error', message='An internal error occurred during generation. Please retry.')"))
    assert _retryable(RuntimeError("upstream connect error or disconnect/reset before headers"))
    assert not _retryable(ValueError("1 validation error for CandidateEntity: name field required"))
    assert not _retryable(KeyError("missing"))


def test_reworded_searches_are_answered_from_the_earlier_one(live):
    """Regression from a live run: ten rephrasings of one search used most of the web budget."""
    calls: list = []
    box, _, _ = toolbox(live, calls)
    box.web_search("London B2B fintech Series A 2026")
    out = box.web_search('"Series A" "2026" "London" fintech "B2B" startups')
    assert "TOO SIMILAR" in out and "different angle" in out and len(calls) == 1  # no second paid call
    box.web_search("Wealthyhood founders")  # a new angle goes through
    assert len(calls) == 2
