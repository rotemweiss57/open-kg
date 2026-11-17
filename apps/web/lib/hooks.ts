"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import {
  api, post, setCsrf,
  type Change, type EntityDetail, type Frontier, type Graph, type Me, type Monitor, type Notification,
  type RelationshipDetail, type Run, type RunEvent, type Snapshot, type SuggestionPage, type SystemStatus,
  type Timeline, type Usage,
} from "./api";
import { RUN_ACTIVE } from "./colors";

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: async () => { const me = await api<Me>("/auth/me"); setCsrf(me.csrf_token); return me; },
    retry: false, staleTime: 60_000,
  });
}
export const useSystem = () => useQuery({ queryKey: ["system"], queryFn: () => api<SystemStatus>("/system/status"), staleTime: 30_000 });
export const useGraphs = () => useQuery({ queryKey: ["graphs"], queryFn: () => api<Graph[]>("/graphs"), refetchInterval: 8_000 });
export const useGraph = (id: string) => useQuery({ queryKey: ["graph", id], queryFn: () => api<Graph>(`/graphs/${id}`), refetchInterval: 6_000 });

export function useSnapshot(id: string, asOf: string | null, includeProposed: boolean) {
  const qs = new URLSearchParams({ include_proposed: String(includeProposed) });
  if (asOf) qs.set("as_of", asOf);
  return useQuery({
    queryKey: ["snapshot", id, asOf, includeProposed],
    queryFn: () => api<Snapshot>(`/graphs/${id}/snapshot?${qs}`),
    placeholderData: (prev) => prev,
  });
}
export const useTimeline = (id: string) => useQuery({ queryKey: ["timeline", id], queryFn: () => api<Timeline>(`/graphs/${id}/timeline`) });
export const useEntity = (gid: string, id: string | null) => useQuery({
  queryKey: ["entity", gid, id], queryFn: () => api<EntityDetail>(`/graphs/${gid}/entities/${id}`), enabled: !!id });
export const useRelationship = (gid: string, id: string | null) => useQuery({
  queryKey: ["relationship", gid, id], queryFn: () => api<RelationshipDetail>(`/graphs/${gid}/relationships/${id}`), enabled: !!id });
export const useSuggestions = (gid: string, status = "pending") => useQuery({
  queryKey: ["suggestions", gid, status], queryFn: () => api<SuggestionPage>(`/graphs/${gid}/suggestions?status=${status}&limit=200`) });
export const useRuns = (gid: string) => useQuery({
  queryKey: ["runs", gid], queryFn: () => api<Run[]>(`/graphs/${gid}/runs`),
  refetchInterval: (q) => (q.state.data?.some((r) => RUN_ACTIVE.includes(r.status)) ? 2_000 : 15_000) });
export const useFrontier = (gid: string) => useQuery({ queryKey: ["frontier", gid], queryFn: () => api<Frontier[]>(`/graphs/${gid}/frontier`) });
export const useMonitors = (gid: string) => useQuery({ queryKey: ["monitors", gid], queryFn: () => api<Monitor[]>(`/graphs/${gid}/monitors`) });
export const useChanges = (gid: string) => useQuery({ queryKey: ["changes", gid], queryFn: () => api<Change[]>(`/graphs/${gid}/changes`) });
export const useUsage = (gid: string) => useQuery({ queryKey: ["usage", gid], queryFn: () => api<Usage>(`/graphs/${gid}/usage`) });
export const useNotifications = () => useQuery({ queryKey: ["notifications"], queryFn: () => api<Notification[]>("/notifications"), refetchInterval: 20_000 });

/** Invalidate every read model of a graph after a mutation that changes facts. */
export function useInvalidateGraph(gid: string) {
  const qc = useQueryClient();
  return () => {
    for (const key of ["snapshot", "graph", "suggestions", "entity", "relationship", "timeline", "monitors", "changes", "frontier", "runs", "usage"]) {
      qc.invalidateQueries({ queryKey: [key, gid] });
    }
    qc.invalidateQueries({ queryKey: ["graphs"] });
    qc.invalidateQueries({ queryKey: ["notifications"] });
  };
}

export function useDecide(gid: string) {
  const invalidate = useInvalidateGraph(gid);
  return useMutation({
    mutationFn: (v: { id: string; action: "approve" | "reject"; note?: string }) =>
      post(`/graphs/${gid}/suggestions/${v.id}/${v.action}`, { note: v.note ?? "" }),
    onSettled: invalidate,
  });
}

/**
 * Live run events over SSE. The server log is durable and ordered, so this hook only has to
 * (1) dedupe by seq and (2) reconnect; EventSource re-sends Last-Event-ID automatically and a
 * page refresh simply replays from 0. Falls back to JSON polling if SSE keeps failing.
 */
export function useRunStream(runId: string | null, onEvent?: (e: RunEvent) => void, restartKey: string = "") {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [state, setState] = useState<"idle" | "connecting" | "live" | "reconnecting" | "polling" | "ended">("idle");
  const cb = useRef(onEvent);
  useEffect(() => { cb.current = onEvent; });

  useEffect(() => {
    if (!runId) return;
    let last = 0, errors = 0, closed = false, source: EventSource | null = null, timer: ReturnType<typeof setTimeout> | null = null;
    queueMicrotask(() => { if (!closed) { setEvents([]); setState("connecting"); } });
    const push = (batch: RunEvent[]) => {
      const fresh = batch.filter((e) => e.seq > last);
      if (!fresh.length) return;
      last = fresh[fresh.length - 1].seq;
      setEvents((prev) => [...prev, ...fresh]);
      fresh.forEach((e) => cb.current?.(e));
    };
    const poll = async () => {
      if (closed) return;
      try {
        const res = await api<{ events: RunEvent[]; done: boolean }>(`/runs/${runId}/events?format=json&after=${last}`);
        push(res.events);
        if (res.done) { setState("ended"); return; }
      } catch { /* keep polling */ }
      timer = setTimeout(poll, 1500);
    };
    const connect = () => {
      source = new EventSource(`/api/v1/runs/${runId}/events?after=${last}`);
      source.onopen = () => { errors = 0; setState("live"); };
      source.onmessage = (m) => push([JSON.parse(m.data)]);
      source.addEventListener("end", () => { closed = true; source?.close(); setState("ended"); });
      source.onerror = () => {
        if (closed) return;
        errors += 1;
        setState("reconnecting");
        if (errors >= 4) { source?.close(); setState("polling"); poll(); }
      };
    };
    connect();
    return () => { closed = true; source?.close(); if (timer) clearTimeout(timer); };
    // restartKey reopens the stream when a finished run becomes active again (resume / retry).
  }, [runId, restartKey]);

  return { events, state };
}

export function useTheme() {
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  useEffect(() => { queueMicrotask(() => setTheme((document.documentElement.dataset.theme as "dark" | "light") || "dark")); }, []);
  const toggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("openkg-theme", next); } catch { /* private mode */ }
    setTheme(next);
  };
  return { theme, toggle };
}

/** A clock for "elapsed" displays. Ticks only while `active`. */
export function useNow(intervalMs = 1000, active = true): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs, active]);
  return now;
}
