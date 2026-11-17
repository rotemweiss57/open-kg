import type { components } from "./schema";

type Sch = components["schemas"];
export type Me = Sch["Me"];
export type Graph = Sch["GraphOut"];
export type Charter = Sch["Charter"];
export type Ontology = Sch["Ontology"];
export type CharterProposal = Sch["CharterProposal"];
export type Snapshot = Sch["Snapshot"];
export type KGNode = Sch["NodeOut"];
export type KGEdge = Sch["EdgeOut"];
export type EntityDetail = Sch["EntityDetail"];
export type RelationshipDetail = Sch["RelationshipDetail"];
export type Claim = Sch["ClaimOut"];
export type Evidence = Sch["EvidenceOut"];
export type Source = Sch["SourceOut"];
export type Suggestion = Sch["SuggestionOut"];
export type SuggestionPage = Sch["SuggestionPage"];
export type Run = Sch["RunOut"];
export type RunBudget = Sch["RunBudget"];
export type Frontier = Sch["FrontierOut"];
export type Monitor = Sch["MonitorOut"];
export type MonitorRun = Sch["MonitorRunOut"];
export type Change = Sch["ChangeOut"];
export type Notification = Sch["NotificationOut"];
export type SystemStatus = Sch["SystemStatus"];
export type Usage = Sch["UsageOut"];
export type Timeline = Sch["Timeline"];
export type SearchHit = Sch["SearchHit"];
export type RunEvent = { seq: number; type: string; payload: Record<string, any>; created_at: string }; // eslint-disable-line @typescript-eslint/no-explicit-any

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) {
    super(message);
  }
}

let csrf: string | null = null;
export const setCsrf = (token: string | null) => { csrf = token; };

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  const method = (rest.method ?? "GET").toUpperCase();
  const res = await fetch(`/api/v1${path}`, {
    ...rest,
    credentials: "same-origin",
    headers: {
      ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(method !== "GET" && csrf ? { "X-CSRF-Token": csrf } : {}),
      ...headers,
    },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* non-JSON error body */ }
  if (!res.ok) {
    const d = data as { detail?: string; title?: string } | null;
    const fallback = res.status >= 500 ? "The server could not be reached. Is the API running?" : `Request failed (${res.status})`;
    throw new ApiError(res.status, d?.detail || d?.title || fallback, data);
  }
  return data as T;
}

export const post = <T,>(path: string, json?: unknown) => api<T>(path, { method: "POST", json: json ?? {} });
export const patch = <T,>(path: string, json: unknown) => api<T>(path, { method: "PATCH", json });
export const del = (path: string) => api<void>(path, { method: "DELETE" });
