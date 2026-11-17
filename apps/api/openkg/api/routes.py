from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from .. import schemas as S
from ..agent import charter as charter_mod
from ..agent.models import ROLES, ModelRouter
from ..config import get_settings
from ..db import session_scope, utcnow
from ..domain import read
from ..domain import runs as run_cmd
from ..domain.canonical import norm_text
from ..domain.events import TERMINAL, audit, emit, events_after
from ..domain.export import export_graph
from ..domain.monitors import run_monitor_check
from ..domain.review import ReviewError, apply_policy, decide, qualifies_for_auto_approval
from ..models import (
    ChangeEvent,
    Entity,
    FrontierQuestion,
    Graph,
    Monitor,
    MonitorRun,
    Notification,
    ResearchNote,
    Run,
    Suggestion,
    User,
)
from ..security import (
    COOKIE,
    RateLimiter,
    create_session,
    create_user,
    destroy_session,
    lookup_session,
    verify_password,
)
from .deps import Auth, entity_in, get_auth, get_db, get_graph, get_monitor, get_run, relationship_in

router = APIRouter(prefix="/api/v1")
_login_limiter = RateLimiter(limit=10, window=60)
_run_limiter = RateLimiter(limit=20, window=60)


def _set_cookie(resp: Response, token: str) -> None:
    st = get_settings()
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=st.cookie_secure or st.is_production,
                    max_age=st.session_ttl_hours * 3600, path="/")


def _me(auth: Auth) -> S.Me:
    return S.Me(id=auth.user.id, email=auth.user.email, name=auth.user.name, is_demo=auth.user.is_demo,
                workspace_id=auth.workspace.id, workspace_name=auth.workspace.name, csrf_token=auth.csrf_token)


def _dispatch(run_id: str) -> None:
    """Fast-path publish after commit. If it fails the reconciler still finds the run."""
    if get_settings().execution_mode == "celery":
        from ..worker.celery_app import publish_run
        publish_run(run_id)


# --- auth ---------------------------------------------------------------------------------------


@router.post("/auth/login", response_model=S.Me, tags=["auth"])
def login(body: S.LoginIn, request: Request, response: Response, s: Session = Depends(get_db)):
    ip = request.client.host if request.client else "?"
    if not _login_limiter.allow(f"{ip}:{body.email.lower()}"):
        raise HTTPException(429, "Too many attempts. Try again in a minute.")
    user = s.scalar(select(User).where(User.email == body.email.strip().lower()))
    if not verify_password(user.password_hash if user and not user.is_demo else None, body.password) or user is None:
        raise HTTPException(401, "Incorrect email or password")
    token, session = create_session(s, user)
    _set_cookie(response, token)
    s.commit()
    return _me(get_auth(request, s, token, session.csrf_token))


@router.post("/auth/register", response_model=S.Me, tags=["auth"])
def register(body: S.RegisterIn, request: Request, response: Response, s: Session = Depends(get_db)):
    if not get_settings().allow_registration:
        raise HTTPException(403, "Registration is disabled on this server")
    ip = request.client.host if request.client else "?"
    if not _login_limiter.allow(f"reg:{ip}"):
        raise HTTPException(429, "Too many attempts. Try again in a minute.")
    email = body.email.strip().lower()
    if "@" not in email or s.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(422, "That email cannot be used")
    user, _ = create_user(s, email, body.password, body.name)
    token, session = create_session(s, user)
    _set_cookie(response, token)
    s.commit()
    return _me(get_auth(request, s, token, session.csrf_token))


@router.post("/auth/demo", response_model=S.Me, tags=["auth"])
def demo_login(request: Request, response: Response, s: Session = Depends(get_db)):
    """Local demo only: signs into the seeded synthetic workspace. Disabled in production."""
    st = get_settings()
    if not st.demo_mode or st.is_production:
        raise HTTPException(404, "Demo mode is disabled")
    from ..seed import DEMO_EMAIL
    user = s.scalar(select(User).where(User.email == DEMO_EMAIL))
    if user is None:
        raise HTTPException(503, "Demo workspace is still being prepared")
    token, session = create_session(s, user)
    _set_cookie(response, token)
    s.commit()
    return _me(get_auth(request, s, token, session.csrf_token))


@router.post("/auth/logout", status_code=204, tags=["auth"])
def logout(request: Request, response: Response, s: Session = Depends(get_db), auth: Auth = Depends(get_auth)):
    destroy_session(s, request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")


@router.get("/auth/me", response_model=S.Me, tags=["auth"])
def me(auth: Auth = Depends(get_auth)):
    return _me(auth)


# --- system -------------------------------------------------------------------------------------


@router.get("/system/public", tags=["system"])
def public_status() -> dict:
    st = get_settings()
    return {"demo_mode": st.demo_mode and not st.is_production, "allow_registration": st.allow_registration}


@router.get("/system/status", response_model=S.SystemStatus, tags=["system"])
def system_status(request: Request, auth: Auth = Depends(get_auth)):
    st = get_settings()
    mr = ModelRouter()
    reason = ""
    if not st.tavily_api_key:
        reason = "TAVILY_API_KEY is not set on the server."
    elif mr.primary_provider() is None:
        reason = "No model provider key is set (OPENAI_API_KEY, ANTHROPIC_API_KEY or VALAR_API_KEY)."
    worker = getattr(request.app.state, "worker", None)
    return S.SystemStatus(
        demo_mode=st.demo_mode, environment=st.environment, execution_mode=st.execution_mode,
        allow_registration=st.allow_registration, tavily_configured=bool(st.tavily_api_key),
        live_research_available=not reason, live_unavailable_reason=reason, primary_provider=mr.primary_provider(),
        fallbacks=[str(f) for f in (mr.fallbacks("researcher") if mr.primary_provider() else [])],
        providers=[S.ProviderStatus(**p) for p in mr.status()],
        role_models={r: str(mr.spec(r)) for r in ROLES} if mr.primary_provider() else {},
        defaults={"run_budget_usd": st.default_run_budget_usd, "max_tavily_calls": st.default_max_tavily_calls,
                  "max_model_calls": st.default_max_model_calls, "deadline_minutes": st.default_run_deadline_minutes,
                  "max_run_budget_usd": st.max_run_budget_usd, "model_input_usd_per_mtok": st.model_input_usd_per_mtok,
                  "model_output_usd_per_mtok": st.model_output_usd_per_mtok},
        scenarios=charter_mod.templates(), database=st.database_url.split(":", 1)[0].split("+")[0],
        worker={"mode": st.execution_mode,
                "embedded_alive": bool(worker and worker.thread and worker.thread.is_alive()) if worker else None})


@router.post("/system/providers/{provider}/test", response_model=S.ProviderTestOut, tags=["system"])
def test_provider(provider: Literal["openai", "anthropic", "valar", "tavily"], auth: Auth = Depends(get_auth)):
    """Sends one tiny real request. Costs a negligible amount; only runs on explicit user action."""
    st = get_settings()
    if provider == "tavily":
        if not st.tavily_api_key:
            return S.ProviderTestOut(ok=False, message="TAVILY_API_KEY is not set.")
        from ..agent.tavily import TavilyClient
        try:
            res = TavilyClient(max_retries=0).search("OpenKG connection test", max_results=1)
            return S.ProviderTestOut(ok=True, message=f"Tavily responded ({res.credits:g} credit used).")
        except Exception as e:  # noqa: BLE001
            return S.ProviderTestOut(ok=False, message=f"{type(e).__name__}: {str(e)[:200]}")
    if not st.provider_key(provider):
        return S.ProviderTestOut(ok=False, message=f"{provider.upper()}_API_KEY is not set.")
    ok, msg = ModelRouter().test(provider)
    return S.ProviderTestOut(ok=ok, message=msg)


@router.get("/templates", tags=["graphs"])
def templates(auth: Auth = Depends(get_auth)) -> list[dict]:
    return charter_mod.templates()


@router.post("/charter/propose", response_model=S.CharterProposal, tags=["graphs"])
def propose_charter(body: S.CharterProposalIn, use_model: bool = False, auth: Auth = Depends(get_auth)):
    if body.scenario:
        from ..agent.fixture import get_scenario
        try:
            sc = get_scenario(body.scenario)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return S.CharterProposal(name=sc.name, description=sc.description, charter=S.Charter.model_validate(sc.charter),
                                 ontology=S.Ontology.model_validate(sc.ontology), generated_by="template",
                                 scenario=sc.id, estimate=charter_mod.estimate())
    if use_model:
        if ModelRouter().primary_provider() is None:
            raise HTTPException(422, "No model provider is configured; using the built-in draft instead.")
        try:
            return charter_mod.with_model(body.prompt)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"The model could not draft a charter ({type(e).__name__}). "
                                     f"The built-in draft is still available.") from e
    return charter_mod.heuristic(body.prompt)


# --- graphs -------------------------------------------------------------------------------------


@router.get("/graphs", response_model=list[S.GraphOut], tags=["graphs"])
def list_graphs(auth: Auth = Depends(get_auth), s: Session = Depends(get_db)):
    graphs = s.scalars(select(Graph).where(Graph.workspace_id == auth.workspace_id).order_by(Graph.updated_at.desc()))
    return [read.graph_out(s, g) for g in graphs]


@router.post("/graphs", response_model=S.GraphOut, status_code=201, tags=["graphs"])
def create_graph(body: S.GraphCreate, auth: Auth = Depends(get_auth), s: Session = Depends(get_db)):
    scenario = None
    if body.scenario:
        from ..agent.fixture import get_scenario
        try:
            scenario = get_scenario(body.scenario)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
    g = Graph(workspace_id=auth.workspace_id, name=body.name.strip(), description=body.description,
              charter=body.charter.model_dump(), ontology=body.ontology.model_dump(),
              autonomy_mode=body.autonomy_mode, budget_usd=body.budget_usd,
              is_synthetic=scenario is not None, scenario=scenario.id if scenario else None)
    if scenario and not body.ontology.entity_types:
        g.charter, g.ontology = scenario.charter, scenario.ontology
    s.add(g)
    s.flush()
    audit(s, graph_id=g.id, workspace_id=g.workspace_id, actor_user_id=auth.user.id, action="graph.created",
          target=f"graph:{g.id}", detail={"name": g.name, "synthetic": g.is_synthetic})
    return read.graph_out(s, g)


@router.get("/graphs/{graph_id}", response_model=S.GraphOut, tags=["graphs"])
def get_graph_route(g: Graph = Depends(get_graph), s: Session = Depends(get_db)):
    return read.graph_out(s, g)


@router.patch("/graphs/{graph_id}", response_model=S.GraphOut, tags=["graphs"])
def patch_graph(body: S.GraphPatch, g: Graph = Depends(get_graph), auth: Auth = Depends(get_auth),
                s: Session = Depends(get_db)):
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        if k == "ontology":
            g.ontology_version += 1
        setattr(g, k, v)
    if data.get("autonomy_mode") == "auto_accept_high":
        apply_policy(s, g)  # switching the policy on applies it to what is already waiting
    g.updated_at = utcnow()
    audit(s, graph_id=g.id, workspace_id=g.workspace_id, actor_user_id=auth.user.id, action="graph.updated",
          target=f"graph:{g.id}", detail={"fields": sorted(data)})
    return read.graph_out(s, g)


@router.delete("/graphs/{graph_id}", status_code=204, tags=["graphs"])
def delete_graph(g: Graph = Depends(get_graph), auth: Auth = Depends(get_auth), s: Session = Depends(get_db)):
    """Deletes the graph and every artifact derived from it (claims, sources, runs, monitors)."""
    for run in s.scalars(select(Run).where(Run.graph_id == g.id, Run.finished_at.is_(None))):
        run.requested_action = "cancel"
    s.flush()
    audit(s, graph_id=None, workspace_id=g.workspace_id, actor_user_id=auth.user.id, action="graph.deleted",
          target=f"graph:{g.id}", detail={"name": g.name})
    s.delete(g)


@router.get("/graphs/{graph_id}/snapshot", response_model=S.Snapshot, tags=["graph"])
def snapshot(as_of: datetime | None = None, include_proposed: bool = True,
             limit: int = Query(default=400, ge=10, le=2000), g: Graph = Depends(get_graph), s: Session = Depends(get_db)):
    return read.snapshot(s, g, as_of=as_of, include_proposed=include_proposed, limit=limit)


@router.get("/graphs/{graph_id}/neighborhood", response_model=S.Snapshot, tags=["graph"])
def neighborhood(entity_id: str, depth: int = Query(default=1, ge=1, le=3), g: Graph = Depends(get_graph),
                 s: Session = Depends(get_db)):
    entity_in(s, g, entity_id)
    return read.neighborhood(s, g, entity_id, depth)


@router.get("/graphs/{graph_id}/timeline", response_model=S.Timeline, tags=["graph"])
def timeline(g: Graph = Depends(get_graph), s: Session = Depends(get_db)):
    return read.timeline(s, g)


@router.get("/graphs/{graph_id}/entities/{entity_id}", response_model=S.EntityDetail, tags=["graph"])
def entity_detail(entity_id: str, g: Graph = Depends(get_graph), s: Session = Depends(get_db)):
    return read.entity_detail(s, g, entity_in(s, g, entity_id))


@router.get("/graphs/{graph_id}/relationships/{relationship_id}", response_model=S.RelationshipDetail, tags=["graph"])
def relationship_detail(relationship_id: str, g: Graph = Depends(get_graph), s: Session = Depends(get_db)):
    return read.relationship_detail(s, g, relationship_in(s, g, relationship_id))


@router.get("/graphs/{graph_id}/table", response_model=S.TableOut, tags=["graph"])
def table(type: str | None = Query(default=None, max_length=60), include_proposed: bool = True,
          g: Graph = Depends(get_graph), s: Session = Depends(get_db)):
    return read.table(s, g, type, include_proposed)


@router.get("/search", response_model=list[S.SearchHit], tags=["graph"])
def search(q: str = Query(min_length=1, max_length=100), graph_id: str | None = None, auth: Auth = Depends(get_auth),
           s: Session = Depends(get_db)):
    like = f"%{norm_text(q)}%"
    stmt = (select(Entity, Graph.name).join(Graph, Graph.id == Entity.graph_id)
            .where(Graph.workspace_id == auth.workspace_id, Entity.status.in_(("accepted", "proposed")),
                   Entity.name_norm.like(like)).order_by(Entity.name).limit(20))
    if graph_id:
        stmt = stmt.where(Entity.graph_id == graph_id)
    return [S.SearchHit(id=e.id, name=e.name, type=e.type, status=e.status, graph_id=e.graph_id, graph_name=name)
            for e, name in s.execute(stmt).all()]


@router.get("/graphs/{graph_id}/export", tags=["graph"])
def export(format: Literal["json", "csv", "graphml", "markdown"] = "json", include_proposed: bool = False,
           g: Graph = Depends(get_graph), s: Session = Depends(get_db)):
    """Accepted facts by default. `include_proposed` adds unreviewed suggestions, each marked as such."""
    body, media, filename = export_graph(s, g, format, include_proposed)
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/graphs/{graph_id}/usage", response_model=S.UsageOut, tags=["graph"])
def usage(g: Graph = Depends(get_graph), s: Session = Depends(get_db)):
    return read.usage_out(s, g)


@router.get("/graphs/{graph_id}/audit", tags=["graph"])
def audit_log(g: Graph = Depends(get_graph), s: Session = Depends(get_db)) -> list[dict]:
    return [{"id": a.id, "action": a.action, "actor_kind": a.actor_kind, "target": a.target, "detail": a.detail,
             "created_at": a.created_at.isoformat()} for a in read.audit_trail(s, g.id)]


# --- suggestions ----------------------------------------------------------------------------------


@router.get("/graphs/{graph_id}/suggestions", response_model=S.SuggestionPage, tags=["review"])
def list_suggestions(status: str = "pending", kind: str | None = None, limit: int = Query(default=100, ge=1, le=300),
                     offset: int = Query(default=0, ge=0), g: Graph = Depends(get_graph), s: Session = Depends(get_db)):
    base = select(Suggestion).where(Suggestion.graph_id == g.id)
    if status == "decided":
        base = base.where(Suggestion.status.in_(("approved", "auto_approved", "rejected", "superseded")))
    elif status != "all":
        base = base.where(Suggestion.status == status)
    counts = dict(s.execute(select(Suggestion.kind, func.count()).where(
        Suggestion.graph_id == g.id, Suggestion.status == "pending").group_by(Suggestion.kind)).all())
    if kind:
        base = base.where(Suggestion.kind == kind)
    total = s.scalar(select(func.count()).select_from(base.subquery())) or 0
    order = Suggestion.decided_at.desc() if status == "decided" else Suggestion.created_at.desc()
    rows = list(s.scalars(base.order_by(order, Suggestion.id).limit(limit).offset(offset)))
    eligible = 0
    if status == "pending":
        eligible = sum(1 for r in s.scalars(select(Suggestion).where(
            Suggestion.graph_id == g.id, Suggestion.status == "pending", Suggestion.risk == "low")) if
            qualifies_for_auto_approval(s, r))
    return S.SuggestionPage(items=read.suggestions_out(s, rows), total=total, counts=counts, policy_eligible=eligible)


def _decide(s: Session, g: Graph, suggestion_id: str, action: str, auth: Auth, note: str) -> Suggestion:
    sug = s.get(Suggestion, suggestion_id)
    if sug is None or sug.graph_id != g.id:
        raise HTTPException(404, "Suggestion not found")
    try:
        with s.begin_nested():
            out = decide(s, g, sug, action, actor_user_id=auth.user.id, note=note)
            apply_policy(s, g)  # a human approval can make dependent edges qualify
            return out
    except ReviewError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/graphs/{graph_id}/suggestions/{suggestion_id}/approve", response_model=S.SuggestionOut, tags=["review"])
def approve(suggestion_id: str, body: S.DecisionIn | None = None, g: Graph = Depends(get_graph),
            auth: Auth = Depends(get_auth), s: Session = Depends(get_db)):
    return read.suggestions_out(s, [_decide(s, g, suggestion_id, "approve", auth, body.note if body else "")])[0]


@router.post("/graphs/{graph_id}/suggestions/{suggestion_id}/reject", response_model=S.SuggestionOut, tags=["review"])
def reject(suggestion_id: str, body: S.DecisionIn | None = None, g: Graph = Depends(get_graph),
           auth: Auth = Depends(get_auth), s: Session = Depends(get_db)):
    return read.suggestions_out(s, [_decide(s, g, suggestion_id, "reject", auth, body.note if body else "")])[0]


@router.post("/graphs/{graph_id}/policy/apply", tags=["review"])
def apply_evidence_policy(g: Graph = Depends(get_graph), auth: Auth = Depends(get_auth), s: Session = Depends(get_db)) -> dict:
    """Approve every pending low-risk suggestion backed by two or more independent verified sources.
    Works as a one-off even when the graph is in review-first mode; each approval is logged as policy."""
    original = g.autonomy_mode
    g.autonomy_mode = "auto_accept_high"
    try:
        decided = apply_policy(s, g)
    finally:
        g.autonomy_mode = original
    return {"approved": len(decided), "titles": [d.title for d in decided][:50]}


@router.post("/graphs/{graph_id}/suggestions/bulk", response_model=S.BulkDecisionOut, tags=["review"])
def bulk(body: S.BulkDecisionIn, g: Graph = Depends(get_graph), auth: Auth = Depends(get_auth), s: Session = Depends(get_db)):
    decided, failed = [], {}
    for sid in body.ids:
        try:
            _decide(s, g, sid, body.action, auth, body.note)
            decided.append(sid)
        except HTTPException as e:
            failed[sid] = str(e.detail)
    return S.BulkDecisionOut(decided=decided, failed=failed)


# --- runs -----------------------------------------------------------------------------------------


def _run_out(s: Session, run: Run) -> S.RunOut:
    out = S.RunOut.model_validate(run)
    out.simulated = run.engine == "fixture"
    rows = dict(s.execute(select(Suggestion.status, func.count()).where(Suggestion.run_id == run.id)
                          .group_by(Suggestion.status)).all())
    out.suggestions_created = sum(rows.values())
    out.suggestions_accepted = rows.get("approved", 0) + rows.get("auto_approved", 0)
    return out


@router.get("/graphs/{graph_id}/runs", response_model=list[S.RunOut], tags=["runs"])
def list_runs(g: Graph = Depends(get_graph), s: Session = Depends(get_db)):
    return [_run_out(s, r) for r in s.scalars(select(Run).where(Run.graph_id == g.id).order_by(Run.created_at.desc()).limit(50))]


@router.post("/graphs/{graph_id}/runs", response_model=S.RunOut, status_code=202, tags=["runs"])
def start_run(body: S.RunCreate, g: Graph = Depends(get_graph), auth: Auth = Depends(get_auth), s: Session = Depends(get_db)):
    """Returns immediately with a durable run id; the work continues server-side."""
    if not _run_limiter.allow(auth.workspace_id):
        raise HTTPException(429, "Too many runs started. Try again shortly.")
    try:
        run = run_cmd.create_run(s, g, engine=body.engine, objective=body.objective, scenario=body.scenario,
                                 focus=body.focus, frontier_id=body.frontier_id,
                                 budget=body.budget.model_dump() if body.budget else None, user_id=auth.user.id,
                                 confirm_spend=body.confirm_spend)
    except run_cmd.RunError as e:
        raise HTTPException(e.status, str(e)) from e
    s.commit()
    _dispatch(run.id)
    return _run_out(s, run)


@router.get("/runs/{run_id}", response_model=S.RunOut, tags=["runs"])
def get_run_route(run: Run = Depends(get_run), s: Session = Depends(get_db)):
    return _run_out(s, run)


def _control(fn, run: Run, s: Session, auth: Auth, action: str, **kw) -> S.RunOut:
    try:
        fn(s, run, **kw)
    except run_cmd.RunError as e:
        raise HTTPException(e.status, str(e)) from e
    audit(s, graph_id=run.graph_id, actor_user_id=auth.user.id, action=f"run.{action}", target=f"run:{run.id}")
    s.commit()
    if action == "resume":
        _dispatch(run.id)
    return _run_out(s, run)


@router.post("/runs/{run_id}/pause", response_model=S.RunOut, tags=["runs"])
def pause_run(run: Run = Depends(get_run), auth: Auth = Depends(get_auth), s: Session = Depends(get_db)):
    return _control(run_cmd.pause, run, s, auth, "pause")


@router.post("/runs/{run_id}/resume", response_model=S.RunOut, tags=["runs"])
def resume_run(body: S.RunBudget | None = None, run: Run = Depends(get_run), auth: Auth = Depends(get_auth),
               s: Session = Depends(get_db)):
    st = get_settings()
    if body and run.engine == "deep_agent" and body.max_usd > st.max_run_budget_usd:
        raise HTTPException(422, f"Budget exceeds the server limit of ${st.max_run_budget_usd:.2f}.")
    return _control(run_cmd.resume, run, s, auth, "resume", extend=body.model_dump() if body else None)


@router.post("/runs/{run_id}/cancel", response_model=S.RunOut, tags=["runs"])
def cancel_run(run: Run = Depends(get_run), auth: Auth = Depends(get_auth), s: Session = Depends(get_db)):
    return _control(run_cmd.cancel, run, s, auth, "cancel")


@router.post("/runs/{run_id}/steer", response_model=S.RunOut, tags=["runs"])
def steer_run(body: S.SteerIn, run: Run = Depends(get_run), auth: Auth = Depends(get_auth), s: Session = Depends(get_db)):
    if run.status in TERMINAL:
        raise HTTPException(409, "This run has finished. Start a follow-up run with a focus instead.")
    s.add(ResearchNote(graph_id=run.graph_id, run_id=run.id, kind="steer", content=body.instruction))
    emit(s, run, "steer.received", {"instruction": body.instruction, "note": (
        "Delivered to the coordinator before its next decision." if run.engine == "deep_agent" else
        "Recorded. The simulation follows a fixed script, so steering does not change it.")})
    return _run_out(s, run)


def _event_dict(ev) -> dict:
    return {"seq": ev.seq, "type": ev.type, "payload": ev.payload, "created_at": ev.created_at.isoformat()}


def _can_read_run(token: str | None, run_id: str) -> bool:
    """Tenancy check in its own short transaction (404 for anything not in the caller's workspace)."""
    with session_scope() as s:
        session = lookup_session(s, token)
        if session is None:
            raise HTTPException(401, "Sign in to continue")
        run = s.get(Run, run_id)
        graph = s.get(Graph, run.graph_id) if run else None
        if graph is None:
            return False
        from ..models import Membership
        return s.scalar(select(Membership.id).where(
            Membership.user_id == session.user_id, Membership.workspace_id == graph.workspace_id)) is not None


@router.get("/runs/{run_id}/events", tags=["runs"], response_model=None)
async def run_events(request: Request, run_id: str, after: int = Query(default=0, ge=0),
                     format: Literal["sse", "json"] = "sse", last_event_id: str | None = Header(default=None)):
    """Durable, replayable event stream. Reconnect with `Last-Event-ID` (sent automatically by
    EventSource) or `?after=<seq>` and you receive exactly the events you missed.
    `format=json` is the polling fallback.

    The stream holds no database session or transaction while it is open: authorization and
    each poll of the event log run in their own short transactions."""
    if not await run_in_threadpool(_can_read_run, request.cookies.get(COOKIE), run_id):
        raise HTTPException(404, "Run not found")
    if last_event_id and last_event_id.isdigit():
        after = max(after, int(last_event_id))

    def fetch(cursor: int):
        with session_scope() as s:
            r = s.get(Run, run_id)
            if r is None:
                return [], True
            return [_event_dict(e) for e in events_after(s, run_id, cursor)], r.status in TERMINAL

    if format == "json":
        events, done = await run_in_threadpool(fetch, after)
        return {"events": events, "done": done and len(events) < 500}

    async def stream():
        cursor, idle = after, 0.0
        yield "retry: 1500\n\n"
        while True:
            if await request.is_disconnected():
                return
            events, done = await run_in_threadpool(fetch, cursor)
            for ev in events:
                cursor = ev["seq"]
                yield f"id: {ev['seq']}\ndata: {json.dumps(ev)}\n\n"
            if events:
                idle = 0.0
                continue  # drain fully before checking for completion
            if done:
                yield f"event: end\ndata: {json.dumps({'last_seq': cursor})}\n\n"
                return
            await asyncio.sleep(0.4)
            idle += 0.4
            if idle >= 10:
                idle = 0.0
                yield ": heartbeat\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


# --- frontier ("What's missing?") -----------------------------------------------------------------


@router.get("/graphs/{graph_id}/frontier", response_model=list[S.FrontierOut], tags=["runs"])
def frontier(g: Graph = Depends(get_graph), s: Session = Depends(get_db)):
    rows = s.scalars(select(FrontierQuestion).where(FrontierQuestion.graph_id == g.id, FrontierQuestion.status == "open"))
    return sorted(rows, key=lambda q: -(q.info_gain / max(1, q.est_calls)))


@router.post("/graphs/{graph_id}/frontier/{frontier_id}/dismiss", status_code=204, tags=["runs"])
def dismiss_frontier(frontier_id: str, g: Graph = Depends(get_graph), auth: Auth = Depends(get_auth),
                     s: Session = Depends(get_db)):
    fq = s.get(FrontierQuestion, frontier_id)
    if fq is None or fq.graph_id != g.id:
        raise HTTPException(404, "Question not found")
    fq.status = "dismissed"


# --- monitors & changes ---------------------------------------------------------------------------


@router.get("/graphs/{graph_id}/monitors", response_model=list[S.MonitorOut], tags=["monitoring"])
def list_monitors(g: Graph = Depends(get_graph), s: Session = Depends(get_db)):
    return read.monitors_out(s, list(s.scalars(select(Monitor).where(Monitor.graph_id == g.id).order_by(Monitor.created_at))))


@router.post("/graphs/{graph_id}/monitors", response_model=S.MonitorOut, status_code=201, tags=["monitoring"])
def create_monitor(body: S.MonitorCreate, g: Graph = Depends(get_graph), auth: Auth = Depends(get_auth),
                   s: Session = Depends(get_db)):
    if not body.entity_id:
        raise HTTPException(422, "Choose an entity to watch")
    ent = entity_in(s, g, body.entity_id)
    if ent.status != "accepted":
        raise HTTPException(422, "Only accepted entities can be watched. Review the suggestion first.")
    if s.scalar(select(Monitor.id).where(Monitor.graph_id == g.id, Monitor.entity_id == ent.id)):
        raise HTTPException(409, "This entity is already being watched")
    preds = body.predicates or (g.charter or {}).get("watch_defaults", [])
    m = Monitor(graph_id=g.id, entity_id=ent.id, question=body.question or "Material changes", predicates=preds,
                cadence_minutes=body.cadence_minutes, next_run_at=utcnow() + timedelta(minutes=body.cadence_minutes))
    s.add(m)
    s.flush()
    audit(s, graph_id=g.id, workspace_id=g.workspace_id, actor_user_id=auth.user.id, action="monitor.created",
          target=f"monitor:{m.id}", detail={"entity": ent.name})
    return read.monitors_out(s, [m])[0]


@router.patch("/monitors/{monitor_id}", response_model=S.MonitorOut, tags=["monitoring"])
def patch_monitor(body: S.MonitorPatch, m: Monitor = Depends(get_monitor), auth: Auth = Depends(get_auth),
                  s: Session = Depends(get_db)):
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(m, k, v)
    if m.status == "active" and (m.next_run_at is None or m.next_run_at < utcnow()):
        m.next_run_at = utcnow() + timedelta(minutes=m.cadence_minutes)
    return read.monitors_out(s, [m])[0]


@router.delete("/monitors/{monitor_id}", status_code=204, tags=["monitoring"])
def delete_monitor(m: Monitor = Depends(get_monitor), auth: Auth = Depends(get_auth), s: Session = Depends(get_db)):
    s.delete(m)


@router.post("/monitors/{monitor_id}/check", response_model=S.MonitorRunOut, tags=["monitoring"])
def check_monitor(m: Monitor = Depends(get_monitor), auth: Auth = Depends(get_auth), s: Session = Depends(get_db)):
    """Manual refresh. Synthetic graphs replay their scripted world; live graphs make one small
    paid search (and a model call only if new evidence appears)."""
    g = s.get(Graph, m.graph_id)
    if not g.is_synthetic and not get_settings().live_research_available:
        raise HTTPException(422, "Live monitoring needs TAVILY_API_KEY and a model provider key on the server.")
    s.commit()
    run_id = run_monitor_check(m.id)
    if run_id is None:
        raise HTTPException(409, "A check for this monitor is already running")
    return s.get(MonitorRun, run_id)


@router.get("/monitors/{monitor_id}/runs", response_model=list[S.MonitorRunOut], tags=["monitoring"])
def monitor_runs(m: Monitor = Depends(get_monitor), s: Session = Depends(get_db)):
    return list(s.scalars(select(MonitorRun).where(MonitorRun.monitor_id == m.id).order_by(MonitorRun.started_at.desc()).limit(30)))


@router.get("/graphs/{graph_id}/changes", response_model=list[S.ChangeOut], tags=["monitoring"])
def changes(limit: int = Query(default=50, ge=1, le=200), g: Graph = Depends(get_graph), s: Session = Depends(get_db)):
    rows = list(s.scalars(select(ChangeEvent).where(ChangeEvent.graph_id == g.id).order_by(ChangeEvent.created_at.desc()).limit(limit)))
    return read.changes_out(s, rows)


@router.get("/notifications", response_model=list[S.NotificationOut], tags=["monitoring"])
def notifications(unread_only: bool = False, auth: Auth = Depends(get_auth), s: Session = Depends(get_db)):
    stmt = (select(Notification, Graph.name).join(Graph, Graph.id == Notification.graph_id)
            .where(Notification.workspace_id == auth.workspace_id).order_by(Notification.created_at.desc()).limit(50))
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    out = []
    for n, name in s.execute(stmt).all():
        o = S.NotificationOut.model_validate(n)
        o.graph_name = name
        out.append(o)
    return out


@router.post("/notifications/read", status_code=204, tags=["monitoring"])
def mark_read(graph_id: str | None = None, auth: Auth = Depends(get_auth), s: Session = Depends(get_db)):
    stmt = update(Notification).where(Notification.workspace_id == auth.workspace_id, Notification.read_at.is_(None))
    if graph_id:
        stmt = stmt.where(Notification.graph_id == graph_id)
    s.execute(stmt.values(read_at=utcnow()))


_ = or_
