"""Versioned API contracts. The web client's types are generated from these via OpenAPI."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- auth / system --------------------------------------------------------------------------


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=1, max_length=200)


class RegisterIn(LoginIn):
    name: str = Field(default="", max_length=200)
    password: str = Field(min_length=10, max_length=200)


class Me(Out):
    id: str
    email: str
    name: str
    is_demo: bool
    workspace_id: str
    workspace_name: str
    csrf_token: str


class ProviderStatus(Out):
    provider: str
    configured: bool
    model: str | None = None
    roles: list[str] = []
    capabilities: list[str] = []
    note: str = ""


class SystemStatus(Out):
    demo_mode: bool
    environment: str
    execution_mode: str
    allow_registration: bool
    tavily_configured: bool
    tavily_auth: Literal["api_key", "oauth"] | None = None
    live_research_available: bool
    live_unavailable_reason: str = ""
    primary_provider: str | None = None
    fallbacks: list[str] = []
    role_models: dict[str, str] = {}
    providers: list[ProviderStatus] = []
    defaults: dict[str, float] = {}
    scenarios: list[dict[str, str]] = []
    database: str = ""
    worker: dict[str, Any] = {}


class TavilyConnectIn(BaseModel):
    origin: str = Field(pattern=r"^https?://[^/\s?#]+$", max_length=200)


class TavilyConnectOut(Out):
    authorize_url: str


class ProviderTestOut(Out):
    ok: bool
    message: str


# --- graphs -----------------------------------------------------------------------------------


class AttributeDef(BaseModel):
    name: str
    description: str = ""


class EntityTypeDef(BaseModel):
    type: str
    description: str = ""
    color: str = ""
    attributes: list[AttributeDef] = []


class RelationshipTypeDef(BaseModel):
    type: str
    source: str = ""
    target: str = ""
    description: str = ""
    temporal: bool = True


class Ontology(BaseModel):
    entity_types: list[EntityTypeDef] = []
    relationship_types: list[RelationshipTypeDef] = []
    multi_valued_predicates: list[str] = []


class Charter(BaseModel):
    question: str = ""
    scope: str = ""
    inclusion: list[str] = []
    exclusion: list[str] = []
    preferred_domains: list[str] = []
    excluded_domains: list[str] = []
    time_horizon: str = ""
    evidence_standard: str = ""
    ambiguities: list[str] = []
    stop_conditions: list[str] = []
    watch_defaults: list[str] = []
    coverage_note: str = ""


class CharterProposalIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    scenario: str | None = None


class CharterProposal(Out):
    name: str
    description: str
    charter: Charter
    ontology: Ontology
    generated_by: str  # heuristic | model:<provider>
    scenario: str | None = None
    estimate: dict[str, Any] = {}


class GraphCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    charter: Charter = Charter()
    ontology: Ontology = Ontology()
    autonomy_mode: Literal["review_first", "auto_accept_high"] = "auto_accept_high"
    budget_usd: float = Field(default=5.0, ge=0, le=1000)
    scenario: str | None = None


class GraphPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    charter: Charter | None = None
    ontology: Ontology | None = None
    autonomy_mode: Literal["review_first", "auto_accept_high"] | None = None
    budget_usd: float | None = Field(default=None, ge=0, le=1000)


class GraphStats(Out):
    entities: int = 0
    relationships: int = 0
    proposed_entities: int = 0
    pending_suggestions: int = 0
    contested_claims: int = 0
    sources: int = 0
    active_monitors: int = 0
    unread_changes: int = 0
    spent_usd: float = 0
    accepted_claims: int = 0
    type_counts: dict[str, int] = {}


class RunBrief(Out):
    id: str
    kind: str
    engine: str
    status: str
    objective: str
    current_focus: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class GraphOut(Out):
    id: str
    name: str
    description: str
    charter: Charter
    ontology: Ontology
    autonomy_mode: str
    budget_usd: float
    is_synthetic: bool
    scenario: str | None
    created_at: datetime
    updated_at: datetime
    stats: GraphStats = GraphStats()
    latest_run: RunBrief | None = None
    last_researched_at: datetime | None = None
    recent_discoveries: list[str] = []


# --- graph read models --------------------------------------------------------------------------


class NodeOut(Out):
    id: str
    name: str
    type: str
    status: str
    degree: int = 0
    contested: bool = False
    watched: bool = False
    description: str = ""
    first_seen_at: datetime


class EdgeOut(Out):
    id: str
    from_id: str
    to_id: str
    type: str
    status: str
    valid_from: str | None
    valid_to: str | None
    confidence: str = "medium"
    ended: bool = False
    first_seen_at: datetime


class Snapshot(Out):
    graph_id: str
    as_of: datetime | None = None
    nodes: list[NodeOut]
    edges: list[EdgeOut]
    total_nodes: int
    truncated: bool = False


class TimelinePoint(Out):
    at: datetime
    entities: int
    relationships: int
    label: str = ""


class Timeline(Out):
    start: datetime | None
    end: datetime | None
    points: list[TimelinePoint]


class SourceOut(Out):
    id: str
    url: str
    domain: str
    title: str
    published_at: str | None
    fetched_at: datetime
    is_synthetic: bool
    snippet: str = ""


class EvidenceOut(Out):
    id: str
    excerpt: str
    stance: str
    excerpt_verified: bool
    source: SourceOut


class ClaimOut(Out):
    id: str
    predicate: str
    value: Any
    valid_from: str | None
    valid_to: str | None
    confidence: str
    is_inferred: bool
    status: str
    observed_at: datetime
    verified_at: datetime | None
    accepted_at: datetime | None
    independent_sources: int = 0
    evidence: list[EvidenceOut] = []


class AttributeOut(Out):
    predicate: str
    current: ClaimOut | None
    history: list[ClaimOut] = []


class NeighborOut(Out):
    relationship_id: str
    type: str
    direction: Literal["out", "in"]
    status: str
    valid_from: str | None
    valid_to: str | None
    entity: NodeOut


class MonitorOut(Out):
    id: str
    graph_id: str
    entity_id: str | None
    relationship_id: str | None
    target_name: str = ""
    question: str
    predicates: list[str]
    cadence_minutes: int
    status: str
    check_count: int
    last_checked_at: datetime | None
    last_changed_at: datetime | None
    next_run_at: datetime | None
    last_outcome: str
    failure: str


class SuggestionOut(Out):
    id: str
    graph_id: str
    run_id: str | None
    kind: str
    title: str
    rationale: str
    confidence: str
    risk: str
    payload: dict[str, Any]
    entity_id: str | None
    relationship_id: str | None
    merge_target_id: str | None
    status: str
    decided_at: datetime | None
    decision_note: str
    decided_by_kind: str = ""
    created_at: datetime
    claims: list[ClaimOut] = []
    target_claim: ClaimOut | None = None


class EntityDetail(Out):
    id: str
    graph_id: str
    name: str
    type: str
    status: str
    aliases: list[str]
    description: str
    canonical_key: str
    discovered_via: str
    discovered_run_id: str | None
    first_seen_at: datetime
    last_verified_at: datetime | None
    merged_into_id: str | None
    existence: ClaimOut | None
    attributes: list[AttributeOut]
    neighbors: list[NeighborOut]
    unknowns: list[str]
    pending_suggestions: list[SuggestionOut]
    monitors: list[MonitorOut]


class RelationshipDetail(Out):
    id: str
    graph_id: str
    type: str
    status: str
    valid_from: str | None
    valid_to: str | None
    first_seen_at: datetime
    last_verified_at: datetime | None
    discovered_run_id: str | None
    source: NodeOut
    target: NodeOut
    description: str = ""
    claims: list[ClaimOut]
    pending_suggestions: list[SuggestionOut]
    monitors: list[MonitorOut]


class DecisionIn(BaseModel):
    note: str = Field(default="", max_length=1000)


class BulkDecisionIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)
    action: Literal["approve", "reject"]
    note: str = Field(default="", max_length=1000)


class BulkDecisionOut(Out):
    decided: list[str]
    failed: dict[str, str]


class SuggestionPage(Out):
    items: list[SuggestionOut]
    total: int
    counts: dict[str, int]
    policy_eligible: int = 0


# --- runs -------------------------------------------------------------------------------------


class RunBudget(BaseModel):
    max_usd: float = Field(default=1.0, ge=0, le=1000)
    max_tavily_calls: int = Field(default=30, ge=0, le=2000)
    max_model_calls: int = Field(default=60, ge=0, le=5000)
    deadline_minutes: int = Field(default=30, ge=1, le=24 * 60)


class RunCreate(BaseModel):
    engine: Literal["fixture", "deep_agent"] = "fixture"
    objective: str = Field(default="", max_length=4000)
    scenario: str | None = None
    focus: str | None = Field(default=None, max_length=1000)
    frontier_id: str | None = None
    budget: RunBudget | None = None
    confirm_spend: bool = Field(default=False, description="Required for live (paid) runs")


class SteerIn(BaseModel):
    instruction: str = Field(min_length=2, max_length=1000)


class RunOut(Out):
    id: str
    graph_id: str
    kind: str
    engine: str
    status: str
    objective: str
    config: dict[str, Any]
    budget: RunBudget
    requested_action: str
    attempt: int
    last_event_seq: int
    tavily_calls: int
    tavily_credits: float
    model_calls: int
    input_tokens: int
    output_tokens: int
    est_cost_usd: float
    current_focus: str
    summary: str
    error: str
    simulated: bool = False
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    suggestions_created: int = 0
    suggestions_accepted: int = 0


class EventOut(Out):
    seq: int
    type: str
    payload: dict[str, Any]
    created_at: datetime


class FrontierOut(Out):
    id: str
    question: str
    rationale: str
    info_gain: float
    est_calls: int
    status: str
    entity_id: str | None
    created_at: datetime


# --- monitoring ---------------------------------------------------------------------------------


class MonitorCreate(BaseModel):
    entity_id: str | None = None
    relationship_id: str | None = None
    question: str = Field(default="", max_length=1000)
    predicates: list[str] = Field(default_factory=list, max_length=20)
    cadence_minutes: int = Field(default=1440, ge=5, le=60 * 24 * 90)


class MonitorPatch(BaseModel):
    status: Literal["active", "paused", "muted"] | None = None
    cadence_minutes: int | None = Field(default=None, ge=5, le=60 * 24 * 90)
    question: str | None = Field(default=None, max_length=1000)


class MonitorRunOut(Out):
    id: str
    monitor_id: str
    outcome: str
    summary: str
    sources_seen: int
    sources_new: int
    simulated: bool
    started_at: datetime
    finished_at: datetime | None


class ChangeOut(Out):
    id: str
    graph_id: str
    monitor_id: str | None
    kind: str
    entity_id: str | None
    entity_name: str = ""
    relationship_id: str | None
    predicate: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    suggestion_id: str | None
    suggestion_status: str = ""
    summary: str
    created_at: datetime
    before_evidence: list[EvidenceOut] = []
    after_evidence: list[EvidenceOut] = []


class NotificationOut(Out):
    id: str
    graph_id: str
    graph_name: str = ""
    change_event_id: str | None
    title: str
    body: str
    read_at: datetime | None
    created_at: datetime


class UsageRow(Out):
    provider: str
    operation: str
    calls: int
    est_cost_usd: float
    simulated: bool
    units: dict[str, float] = {}


class UsageOut(Out):
    budget_usd: float
    spent_usd: float
    accepted_claims: int
    cost_per_accepted_claim: float | None
    rows: list[UsageRow]
    is_estimate: bool = True


class SearchHit(Out):
    id: str
    name: str
    type: str
    status: str
    graph_id: str
    graph_name: str = ""


class TableCell(Out):
    value: Any
    status: str
    confidence: str
    valid_from: str | None = None
    sources: int = 0


class TableRow(Out):
    id: str
    name: str
    type: str
    status: str
    description: str = ""
    connections: int = 0
    sources: int = 0
    confidence: str = "medium"
    first_seen_at: datetime
    last_verified_at: datetime | None = None
    cells: dict[str, TableCell] = {}
    links: dict[str, list[str]] = {}


class TableOut(Out):
    columns: list[str]
    link_columns: list[str]
    rows: list[TableRow]
    total: int
