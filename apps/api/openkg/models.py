"""Claim-centric property graph schema.

Entities and relationships are readable projections; `claims` + `claim_evidence` + `sources`
are the trust substrate. Every row that belongs to a graph carries `graph_id` so tenancy can be
enforced in queries and constraints.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, UTCDateTime, new_id, utcnow

Json = JSON().with_variant(JSONB(), "postgresql")


def pk() -> Mapped[str]:
    return mapped_column(String(32), primary_key=True, default=new_id)


def fk(target: str, nullable: bool = False, index: bool = True, ondelete: str = "CASCADE") -> Mapped[Any]:
    return mapped_column(String(32), ForeignKey(target, ondelete=ondelete), nullable=nullable, index=index)


def ts(nullable: bool = False) -> Mapped[Any]:
    return mapped_column(UTCDateTime, nullable=nullable, default=None if nullable else utcnow)


# --- identity & tenancy -------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = pk()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(300))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = ts()


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = pk()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[str] = fk("users.id")
    expires_at: Mapped[datetime] = ts()
    created_at: Mapped[datetime] = ts()


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = ts()


class Membership(Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id"),)
    id: Mapped[str] = pk()
    workspace_id: Mapped[str] = fk("workspaces.id")
    user_id: Mapped[str] = fk("users.id")
    role: Mapped[str] = mapped_column(String(20), default="owner")


class Graph(Base):
    __tablename__ = "graphs"
    id: Mapped[str] = pk()
    workspace_id: Mapped[str] = fk("workspaces.id")
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    charter: Mapped[dict] = mapped_column(Json, default=dict)
    ontology: Mapped[dict] = mapped_column(Json, default=dict)
    ontology_version: Mapped[int] = mapped_column(Integer, default=1)
    autonomy_mode: Mapped[str] = mapped_column(String(30), default="review_first")
    budget_usd: Mapped[float] = mapped_column(Float, default=5.0)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    scenario: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = ts()
    updated_at: Mapped[datetime] = ts()


# --- graph projection ----------------------------------------------------------------------


class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint("graph_id", "canonical_key"),
        Index("ix_entities_graph_status", "graph_id", "status"),
        Index("ix_entities_graph_name", "graph_id", "name_norm"),
    )
    id: Mapped[str] = pk()
    graph_id: Mapped[str] = fk("graphs.id")
    type: Mapped[str] = mapped_column(String(60))
    canonical_key: Mapped[str] = mapped_column(String(400))
    name: Mapped[str] = mapped_column(String(300))
    name_norm: Mapped[str] = mapped_column(String(300))
    aliases: Mapped[list] = mapped_column(Json, default=list)
    description: Mapped[str] = mapped_column(Text, default="")
    # proposed | accepted | rejected | merged
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    merged_into_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    discovered_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    discovered_via: Mapped[str] = mapped_column(Text, default="")
    first_seen_at: Mapped[datetime] = ts()
    last_verified_at: Mapped[datetime | None] = ts(nullable=True)


class Relationship(Base):
    __tablename__ = "relationships"
    __table_args__ = (
        UniqueConstraint("graph_id", "dedup_key"),
        Index("ix_rel_graph_from", "graph_id", "from_id"),
        Index("ix_rel_graph_to", "graph_id", "to_id"),
    )
    id: Mapped[str] = pk()
    graph_id: Mapped[str] = fk("graphs.id")
    from_id: Mapped[str] = fk("entities.id")
    to_id: Mapped[str] = fk("entities.id")
    type: Mapped[str] = mapped_column(String(60))
    dedup_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    valid_from: Mapped[str | None] = mapped_column(String(10), nullable=True)
    valid_to: Mapped[str | None] = mapped_column(String(10), nullable=True)
    discovered_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    first_seen_at: Mapped[datetime] = ts()
    last_verified_at: Mapped[datetime | None] = ts(nullable=True)


class Claim(Base):
    """A time-qualified proposition about an entity attribute or a relationship."""

    __tablename__ = "claims"
    __table_args__ = (
        UniqueConstraint("graph_id", "claim_key"),
        Index("ix_claims_entity", "graph_id", "entity_id", "predicate"),
        Index("ix_claims_rel", "graph_id", "relationship_id"),
    )
    id: Mapped[str] = pk()
    graph_id: Mapped[str] = fk("graphs.id")
    subject_kind: Mapped[str] = mapped_column(String(20))  # entity | relationship
    entity_id: Mapped[str | None] = fk("entities.id", nullable=True)
    relationship_id: Mapped[str | None] = fk("relationships.id", nullable=True)
    predicate: Mapped[str] = mapped_column(String(80))
    value: Mapped[Any] = mapped_column(Json, nullable=True)
    value_norm: Mapped[str] = mapped_column(String(400), default="")
    claim_key: Mapped[str] = mapped_column(String(64))
    # valid time: when the fact was true in the world
    valid_from: Mapped[str | None] = mapped_column(String(10), nullable=True)
    valid_to: Mapped[str | None] = mapped_column(String(10), nullable=True)
    confidence: Mapped[str] = mapped_column(String(10), default="medium")  # high | medium | low
    is_inferred: Mapped[bool] = mapped_column(Boolean, default=False)
    # proposed | accepted | contested | superseded | retracted | rejected
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    superseded_by_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # observation time: when we learned / confirmed it
    observed_at: Mapped[datetime] = ts()
    verified_at: Mapped[datetime | None] = ts(nullable=True)
    accepted_at: Mapped[datetime | None] = ts(nullable=True)


class Source(Base):
    """A web document. Evidence, never automatically a graph entity."""

    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("graph_id", "canonical_url"),)
    id: Mapped[str] = pk()
    graph_id: Mapped[str] = fk("graphs.id")
    url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(String(700))
    domain: Mapped[str] = mapped_column(String(255), default="")
    title: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[str | None] = mapped_column(String(10), nullable=True)
    fetched_at: Mapped[datetime] = ts()
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    # capped retrieved text, kept server-side only for excerpt verification / change hashing
    content: Mapped[str] = mapped_column(Text, default="")
    # press releases syndicated across sites share a cluster => not independent corroboration
    syndication_key: Mapped[str] = mapped_column(String(64), default="")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    meta: Mapped[dict] = mapped_column(Json, default=dict)


class ClaimEvidence(Base):
    __tablename__ = "claim_evidence"
    __table_args__ = (UniqueConstraint("claim_id", "source_id", "stance"),)
    id: Mapped[str] = pk()
    graph_id: Mapped[str] = fk("graphs.id")
    claim_id: Mapped[str] = fk("claims.id")
    source_id: Mapped[str] = fk("sources.id")
    excerpt: Mapped[str] = mapped_column(Text, default="")
    locator: Mapped[str] = mapped_column(String(200), default="")
    excerpt_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    stance: Mapped[str] = mapped_column(String(15), default="supports")  # supports | contradicts
    strength: Mapped[str] = mapped_column(String(10), default="medium")
    created_at: Mapped[datetime] = ts()


class Suggestion(Base):
    __tablename__ = "suggestions"
    __table_args__ = (
        UniqueConstraint("graph_id", "idempotency_key"),
        Index("ix_sugg_graph_status", "graph_id", "status"),
    )
    id: Mapped[str] = pk()
    graph_id: Mapped[str] = fk("graphs.id")
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # entity | relationship | claim | merge | contradiction
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(Text, default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[str] = mapped_column(String(10), default="medium")
    risk: Mapped[str] = mapped_column(String(10), default="low")  # low | elevated
    payload: Mapped[dict] = mapped_column(Json, default=dict)
    entity_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    relationship_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    claim_ids: Mapped[list] = mapped_column(Json, default=list)
    target_claim_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    merge_target_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # pending | approved | auto_approved | rejected | superseded
    status: Mapped[str] = mapped_column(String(20), default="pending")
    decided_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decided_at: Mapped[datetime | None] = ts(nullable=True)
    decision_note: Mapped[str] = mapped_column(Text, default="")
    idempotency_key: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = ts()


# --- runs ----------------------------------------------------------------------------------


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (Index("ix_runs_status", "status"),)
    id: Mapped[str] = pk()
    graph_id: Mapped[str] = fk("graphs.id")
    kind: Mapped[str] = mapped_column(String(20), default="research")  # research | monitor
    engine: Mapped[str] = mapped_column(String(20), default="fixture")  # fixture | deep_agent
    status: Mapped[str] = mapped_column(String(20), default="queued")
    objective: Mapped[str] = mapped_column(Text, default="")
    config: Mapped[dict] = mapped_column(Json, default=dict)
    budget: Mapped[dict] = mapped_column(Json, default=dict)
    cursor: Mapped[dict] = mapped_column(Json, default=dict)
    thread_id: Mapped[str] = mapped_column(String(80), default="")
    requested_action: Mapped[str] = mapped_column(String(10), default="")  # "" | pause | cancel
    lease_owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_expires_at: Mapped[datetime | None] = ts(nullable=True)
    dispatched_at: Mapped[datetime | None] = ts(nullable=True)
    not_before: Mapped[datetime | None] = ts(nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    last_event_seq: Mapped[int] = mapped_column(Integer, default=0)
    tavily_calls: Mapped[int] = mapped_column(Integer, default=0)
    tavily_credits: Mapped[float] = mapped_column(Float, default=0)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    est_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    current_focus: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = ts()
    started_at: Mapped[datetime | None] = ts(nullable=True)
    finished_at: Mapped[datetime | None] = ts(nullable=True)


class RunStep(Base):
    __tablename__ = "run_steps"
    __table_args__ = (UniqueConstraint("run_id", "key"),)
    id: Mapped[str] = pk()
    run_id: Mapped[str] = fk("runs.id")
    key: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(40), default="")
    result: Mapped[dict] = mapped_column(Json, default=dict)
    created_at: Mapped[datetime] = ts()


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq"),)
    id: Mapped[str] = pk()
    run_id: Mapped[str] = fk("runs.id")
    graph_id: Mapped[str] = fk("graphs.id")
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(Json, default=dict)
    created_at: Mapped[datetime] = ts()


class ResearchNote(Base):
    __tablename__ = "research_notes"
    id: Mapped[str] = pk()
    graph_id: Mapped[str] = fk("graphs.id")
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(30), default="note")  # note | decision | tried_query | dossier | steer
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = ts()


class FrontierQuestion(Base):
    __tablename__ = "frontier_questions"
    __table_args__ = (UniqueConstraint("graph_id", "question_key"),)
    id: Mapped[str] = pk()
    graph_id: Mapped[str] = fk("graphs.id")
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    question: Mapped[str] = mapped_column(Text)
    question_key: Mapped[str] = mapped_column(String(64))
    rationale: Mapped[str] = mapped_column(Text, default="")
    info_gain: Mapped[float] = mapped_column(Float, default=0.5)
    est_calls: Mapped[int] = mapped_column(Integer, default=3)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | launched | dismissed | answered
    created_at: Mapped[datetime] = ts()


# --- monitoring ------------------------------------------------------------------------------


class Monitor(Base):
    __tablename__ = "monitors"
    id: Mapped[str] = pk()
    graph_id: Mapped[str] = fk("graphs.id")
    entity_id: Mapped[str | None] = fk("entities.id", nullable=True)
    relationship_id: Mapped[str | None] = fk("relationships.id", nullable=True)
    question: Mapped[str] = mapped_column(Text, default="")
    predicates: Mapped[list] = mapped_column(Json, default=list)
    cadence_minutes: Mapped[int] = mapped_column(Integer, default=1440)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | paused | muted
    max_tavily_calls: Mapped[int] = mapped_column(Integer, default=4)
    check_count: Mapped[int] = mapped_column(Integer, default=0)
    last_checked_at: Mapped[datetime | None] = ts(nullable=True)
    last_changed_at: Mapped[datetime | None] = ts(nullable=True)
    next_run_at: Mapped[datetime | None] = ts(nullable=True)
    last_outcome: Mapped[str] = mapped_column(String(20), default="")
    failure: Mapped[str] = mapped_column(Text, default="")
    locked_until: Mapped[datetime | None] = ts(nullable=True)
    created_at: Mapped[datetime] = ts()


class MonitorRun(Base):
    __tablename__ = "monitor_runs"
    id: Mapped[str] = pk()
    monitor_id: Mapped[str] = fk("monitors.id")
    graph_id: Mapped[str] = fk("graphs.id")
    outcome: Mapped[str] = mapped_column(String(20), default="")  # no_change | changed | new | contradicted | stale | failed
    summary: Mapped[str] = mapped_column(Text, default="")
    sources_seen: Mapped[int] = mapped_column(Integer, default=0)
    sources_new: Mapped[int] = mapped_column(Integer, default=0)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = ts()
    finished_at: Mapped[datetime | None] = ts(nullable=True)


class ChangeEvent(Base):
    __tablename__ = "change_events"
    id: Mapped[str] = pk()
    graph_id: Mapped[str] = fk("graphs.id")
    monitor_id: Mapped[str | None] = fk("monitors.id", nullable=True, ondelete="SET NULL")
    monitor_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(20))  # new | changed | contradicted | stale
    entity_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    relationship_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    predicate: Mapped[str] = mapped_column(String(80), default="")
    before: Mapped[dict | None] = mapped_column(Json, nullable=True)
    after: Mapped[dict | None] = mapped_column(Json, nullable=True)
    before_claim_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    after_claim_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    suggestion_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = ts()


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = pk()
    workspace_id: Mapped[str] = fk("workspaces.id")
    graph_id: Mapped[str] = fk("graphs.id")
    change_event_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, default="")
    read_at: Mapped[datetime | None] = ts(nullable=True)
    created_at: Mapped[datetime] = ts()


# --- ledgers ---------------------------------------------------------------------------------


class ProviderUsage(Base):
    __tablename__ = "provider_usage"
    id: Mapped[str] = pk()
    graph_id: Mapped[str] = fk("graphs.id")
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(30))
    operation: Mapped[str] = mapped_column(String(40))
    units: Mapped[dict] = mapped_column(Json, default=dict)
    est_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = ts()


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = pk()
    workspace_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    graph_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_kind: Mapped[str] = mapped_column(String(20), default="user")  # user | policy | system
    action: Mapped[str] = mapped_column(String(60))
    target: Mapped[str] = mapped_column(String(120), default="")
    detail: Mapped[dict] = mapped_column(Json, default=dict)
    created_at: Mapped[datetime] = ts()


# --- integrations ----------------------------------------------------------------------------


class TavilyConnection(Base):
    """The server-wide "Sign in with Tavily" (MCP OAuth). A single row, id="default"."""

    __tablename__ = "tavily_connections"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="default")
    client_id: Mapped[str] = mapped_column(String(200), default="")
    redirect_uri: Mapped[str] = mapped_column(String(500), default="")
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = ts(nullable=True)
    # An in-progress sign-in; promoted to the fields above only once its token exchange succeeds.
    pending_client_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    pending_redirect_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pending_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pending_state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pending_verifier: Mapped[str | None] = mapped_column(String(200), nullable=True)
    connected_by_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    connected_at: Mapped[datetime | None] = ts(nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
