from __future__ import annotations

import secrets
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import Entity, Graph, Membership, Monitor, Relationship, Run, User, Workspace
from ..security import COOKIE, lookup_session

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_db() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except BaseException:
        s.rollback()
        raise
    finally:
        s.close()


@dataclass
class Auth:
    user: User
    workspace: Workspace
    csrf_token: str

    @property
    def workspace_id(self) -> str:
        return self.workspace.id


def get_auth(request: Request, s: Session = Depends(get_db), token: str | None = Cookie(default=None, alias=COOKIE),
             csrf: str | None = Header(default=None, alias="X-CSRF-Token")) -> Auth:
    """Every data endpoint depends on this: a valid session cookie, and for unsafe methods a
    matching CSRF token (the cookie is HttpOnly + SameSite=Lax; the token is a second factor
    a cross-site form cannot read)."""
    session = lookup_session(s, token)
    if session is None:
        raise HTTPException(401, "Sign in to continue")
    if request.method not in SAFE_METHODS and not (csrf and secrets.compare_digest(csrf, session.csrf_token)):
        raise HTTPException(403, "Missing or invalid CSRF token")
    user = s.get(User, session.user_id)
    ws = s.scalar(select(Workspace).join(Membership, Membership.workspace_id == Workspace.id)
                  .where(Membership.user_id == session.user_id).order_by(Workspace.created_at))
    if user is None or ws is None:
        raise HTTPException(401, "Sign in to continue")
    return Auth(user, ws, session.csrf_token)


# Graph-scoped loaders. They return 404 (never 403) for other tenants' objects so that IDs
# cannot be probed for existence.


def get_graph(graph_id: str, auth: Auth = Depends(get_auth), s: Session = Depends(get_db)) -> Graph:
    g = s.get(Graph, graph_id)
    if g is None or g.workspace_id != auth.workspace_id:
        raise HTTPException(404, "Graph not found")
    return g


def get_run(run_id: str, auth: Auth = Depends(get_auth), s: Session = Depends(get_db)) -> Run:
    run = s.get(Run, run_id)
    g = s.get(Graph, run.graph_id) if run else None
    if run is None or g is None or g.workspace_id != auth.workspace_id:
        raise HTTPException(404, "Run not found")
    return run


def get_monitor(monitor_id: str, auth: Auth = Depends(get_auth), s: Session = Depends(get_db)) -> Monitor:
    m = s.get(Monitor, monitor_id)
    g = s.get(Graph, m.graph_id) if m else None
    if m is None or g is None or g.workspace_id != auth.workspace_id:
        raise HTTPException(404, "Monitor not found")
    return m


def entity_in(s: Session, g: Graph, entity_id: str) -> Entity:
    ent = s.get(Entity, entity_id)
    if ent is None or ent.graph_id != g.id:
        raise HTTPException(404, "Entity not found")
    return ent


def relationship_in(s: Session, g: Graph, rel_id: str) -> Relationship:
    rel = s.get(Relationship, rel_id)
    if rel is None or rel.graph_id != g.id:
        raise HTTPException(404, "Relationship not found")
    return rel
