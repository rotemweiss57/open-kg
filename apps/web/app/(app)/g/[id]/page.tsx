"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ArrowRight, History, Play, Radio, Telescope, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { EntityList } from "@/components/entity-list";
import { GraphCanvas } from "@/components/graph/canvas";
import { TimeMachine } from "@/components/graph/timeline";
import { useGraphCtx } from "@/components/graph-context";
import { Inspector, type Selection } from "@/components/inspector";
import { EventRow, HIDDEN_EVENTS } from "@/components/run/events";
import { toastError } from "@/components/toast";
import { Button, Empty, ErrorNote, Skeleton } from "@/components/ui";
import { api, type RunEvent, type Snapshot } from "@/lib/api";
import { fmtDateTime, firstLine } from "@/lib/format";
import { useInvalidateGraph, useRunStream, useSnapshot, useTimeline } from "@/lib/hooks";

function CanvasPage() {
  const { graph, activeRun, startRun } = useGraphCtx();
  const router = useRouter();
  const path = usePathname();
  const params = useSearchParams();
  const qc = useQueryClient();
  const invalidate = useInvalidateGraph(graph.id);

  const selection: Selection = params.get("entity") ? { kind: "entity", id: params.get("entity")! }
    : params.get("rel") ? { kind: "relationship", id: params.get("rel")! } : null;
  const select = useCallback((s: Selection) => {
    const qs = new URLSearchParams(params.toString());
    qs.delete("entity"); qs.delete("rel");
    if (s) qs.set(s.kind === "entity" ? "entity" : "rel", s.id);
    router.replace(`${path}${qs.size ? `?${qs}` : ""}`, { scroll: false });
  }, [params, path, router]);

  const [showProposed, setShowProposed] = useState(true);
  const [timeIndex, setTimeIndex] = useState<number | null>(null);
  const [listOpen, setListOpen] = useState(false);
  const [extra, setExtra] = useState<Snapshot | null>(null);
  const [pulse, setPulse] = useState<Set<string>>(new Set());
  const timeline = useTimeline(graph.id);
  const asOf = timeIndex !== null ? timeline.data?.points[timeIndex]?.at ?? null : null;
  const snap = useSnapshot(graph.id, asOf, showProposed);

  // Live run: events drive Graph Bloom (via refetch) and Research Pulse (via focus events).
  const refetchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onEvent = useCallback((ev: RunEvent) => {
    if (ev.type === "focus") {
      const ids: string[] = ev.payload.entity_ids ?? [];
      setPulse(new Set(ids));
      setTimeout(() => setPulse((cur) => (ids.every((i) => cur.has(i)) && cur.size === ids.length ? new Set() : cur)), 5200);
    }
    if (ev.type.startsWith("candidate.") || ev.type === "suggestion.decided" || ev.type === "run.status" || ev.type === "frontier.added") {
      if (refetchTimer.current) clearTimeout(refetchTimer.current);
      refetchTimer.current = setTimeout(() => {
        for (const k of ["snapshot", "graph", "timeline", "suggestions", "runs"]) qc.invalidateQueries({ queryKey: [k, graph.id] });
      }, 180);
    }
  }, [qc, graph.id]);
  const streamId = activeRun?.id ?? null;
  const stream = useRunStream(streamId, onEvent, activeRun?.status === "paused" ? "paused" : "active");
  const ticker = useMemo(() => stream.events.filter((e) => !HIDDEN_EVENTS.has(e.type) && e.type !== "run.status" && e.type !== "slice.started").slice(-3), [stream.events]);

  const merged = useMemo(() => {
    if (!snap.data) return null;
    if (!extra || asOf) return snap.data;
    const ids = new Set(snap.data.nodes.map((n) => n.id)), eids = new Set(snap.data.edges.map((e) => e.id));
    return { ...snap.data, nodes: [...snap.data.nodes, ...extra.nodes.filter((n) => !ids.has(n.id))], edges: [...snap.data.edges, ...extra.edges.filter((e) => !eids.has(e.id))] };
  }, [snap.data, extra, asOf]);

  const expand = async (entityId: string) => {
    try {
      const hood = await api<Snapshot>(`/graphs/${graph.id}/neighborhood?entity_id=${entityId}&depth=2`);
      setExtra((prev) => prev ? { ...prev, nodes: [...prev.nodes, ...hood.nodes], edges: [...prev.edges, ...hood.edges] } : hood);
    } catch (e) { toastError(e); }
  };
  useEffect(() => { if (!activeRun) invalidate(); /* settle read models when a run ends */ // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeRun?.id]);

  if (snap.isError) return <div className="p-6"><ErrorNote error={snap.error} retry={() => snap.refetch()} /></div>;
  if (!merged) return <div className="h-full p-4"><Skeleton className="h-full w-full !rounded-xl" /></div>;

  const empty = merged.nodes.length === 0 && !asOf;
  const selectedEntityId = selection?.kind === "entity" ? selection.id : null;
  return (
    <div className="flex h-full min-h-0 flex-col md:flex-row">
      <div className="relative h-[46dvh] min-h-[300px] shrink-0 md:h-auto md:min-w-0 md:flex-1">
        {empty ? (
          <div className="grid-bg flex h-full flex-col items-center justify-center">
            <Empty icon={<Telescope className={activeRun ? "anim-blink h-5 w-5 text-accent-text" : "h-5 w-5"} />} title={activeRun ? "Researching" : "Nothing mapped yet"}
              action={activeRun ? <Link href={`/g/${graph.id}/research`}><Button variant="primary">Watch <ArrowRight className="h-4 w-4" /></Button></Link>
                : <Button variant="primary" onClick={() => startRun()}><Play className="h-4 w-4" /> Start research</Button>} />
            {activeRun && (
              <div className="w-full max-w-xl px-6" aria-live="polite" aria-label="Live research activity">
                <div className="rounded-xl bg-panel/90 p-2 shadow-[0_0_0_1px_var(--line)]">
                  <p className="truncate px-1 pb-1 text-[12px] text-accent-text">{firstLine(activeRun.current_focus) || "Planning"}</p>
                  {stream.events.filter((e) => !HIDDEN_EVENTS.has(e.type) && e.type !== "run.status" && e.type !== "slice.started").slice(-5).map((ev) => <div key={ev.seq} className="anim-fade-up"><EventRow ev={ev} compact /></div>)}
                  {stream.events.length < 3 && <p className="px-1 py-1 text-[12.5px] text-fg-3">Starting…</p>}
                </div>
              </div>
            )}
          </div>
        ) : (
          <GraphCanvas graph={graph} snapshot={merged} selection={selection} onSelect={select} pulseIds={pulse}
            showProposed={showProposed} onShowProposed={setShowProposed} onToggleList={() => setListOpen((v) => !v)} historical={!!asOf}>
            {asOf && (
              <div className="absolute left-1/2 top-3 flex -translate-x-1/2 items-center gap-2 rounded-full bg-accent px-3 py-1 text-[12px] font-medium text-accent-ink shadow-panel">
                <History className="h-3.5 w-3.5" /> As of {fmtDateTime(asOf)}
              </div>
            )}
            <div className="pointer-events-none absolute inset-x-3 bottom-3 flex flex-col items-center gap-2 md:flex-row md:items-end md:justify-between">
              <div className="pointer-events-auto order-2 w-full max-w-[420px] md:order-1 [&:not(:empty)~*]:z-10">
                {activeRun && ticker.length > 0 && (
                  <div className="relative z-10 rounded-xl bg-panel/95 p-2 shadow-panel shadow-[0_0_0_1px_var(--line-2)] backdrop-blur" aria-live="polite" aria-label="Live research activity">
                    <div className="mb-0.5 flex items-center justify-between px-1">
                      <span className="flex min-w-0 items-center gap-1.5 text-[12px] text-accent-text"><Radio className="anim-blink h-3 w-3 shrink-0" /> <span className="truncate">{firstLine(activeRun.current_focus) || "Starting"}</span></span>
                      <Link href={`/g/${graph.id}/research`} className="ml-3 shrink-0 whitespace-nowrap text-[11.5px] text-fg-2 hover:text-fg">Open →</Link>
                    </div>
                    {ticker.map((ev) => <div key={ev.seq} className="anim-fade-up"><EventRow ev={ev} compact onPick={select} /></div>)}
                  </div>
                )}
              </div>
              <div className="pointer-events-auto order-1 md:order-2">
                {timeline.data && <TimeMachine timeline={timeline.data} index={timeIndex} onChange={setTimeIndex} />}
              </div>
            </div>
            {listOpen && (
              <div className="absolute bottom-3 left-3 top-[88px] z-20 hidden w-[280px] flex-col overflow-hidden rounded-xl bg-panel shadow-panel shadow-[0_0_0_1px_var(--line-2)] md:flex">
                <div className="flex items-center justify-between border-b border-line px-3 py-2"><span className="label">Entities</span>
                  <Button variant="ghost" size="icon" aria-label="Close entity list" onClick={() => setListOpen(false)}><X className="h-4 w-4" /></Button></div>
                <EntityList graph={graph} nodes={merged.nodes} selectedId={selectedEntityId} onSelect={(id) => select({ kind: "entity", id })} className="min-h-0 flex-1" />
              </div>
            )}
          </GraphCanvas>
        )}
      </div>

      {/* Small screens: the list is the primary way to browse; the canvas above is a preview. */}
      {!empty && !selection && (
        <div className="min-h-0 flex-1 border-t border-line bg-panel md:hidden">
          <EntityList graph={graph} nodes={merged.nodes} selectedId={null} onSelect={(id) => select({ kind: "entity", id })} className="h-full" />
        </div>
      )}
      {selection && (
        <div className="min-h-0 flex-1 border-t border-line md:w-[400px] md:flex-none md:border-l md:border-t-0 xl:w-[430px]">
          <Inspector graph={graph} selection={selection} onSelect={select} onExpand={expand} onInvestigate={(focus) => startRun(focus)} />
        </div>
      )}
    </div>
  );
}

export default function Page() {
  return <Suspense><CanvasPage /></Suspense>;
}
