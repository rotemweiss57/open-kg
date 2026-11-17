"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Ban, Compass, Pause, Play, Plus, Send, Telescope, Wifi, WifiOff, X } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useGraphCtx } from "@/components/graph-context";
import { EventRow, HIDDEN_EVENTS } from "@/components/run/events";
import { RunPill } from "@/components/run-pill";
import { toast, toastError } from "@/components/toast";
import { Badge, Button, Card, cn, Dialog, Empty, Field, Input, Meter, Tip } from "@/components/ui";
import { post, type Run, type RunEvent } from "@/lib/api";
import { RUN_ACTIVE } from "@/lib/colors";
import { duration, firstLine, fmtDateTime, timeAgo, usd } from "@/lib/format";
import { useFrontier, useInvalidateGraph, useNow, useRunStream } from "@/lib/hooks";

const FILTERS: { id: string; label: string; match: (t: string) => boolean }[] = [
  { id: "all", label: "Everything", match: () => true },
  { id: "found", label: "Discoveries", match: (t) => t.startsWith("candidate.") || t === "suggestion.decided" },
  { id: "sources", label: "Sources", match: (t) => ["search.performed", "source.read", "site.map", "site.crawl"].includes(t) },
  { id: "decisions", label: "Plan & decisions", match: (t) => ["plan.created", "slice.started", "note", "verify.result", "run.summary", "frontier.added", "steer.received"].includes(t) },
];

function ResearchPage() {
  const { graph, runs, activeRun, latestRun, startRun } = useGraphCtx();
  const params = useSearchParams();
  const router = useRouter();
  const wanted = params.get("run");
  const run = runs.find((r) => r.id === wanted) ?? activeRun ?? latestRun;
  const [filter, setFilter] = useState("all");
  const invalidate = useInvalidateGraph(graph.id);
  const qc = useQueryClient();
  const stream = useRunStream(run?.id ?? null, (ev) => {
    if (ev.type === "run.status" || ev.type === "frontier.added") qc.invalidateQueries({ queryKey: ["runs", graph.id] });
    if (ev.type === "run.status" && !RUN_ACTIVE.includes(ev.payload.status)) invalidate();
  }, run && RUN_ACTIVE.includes(run.status) ? "active" : "idle");
  const events = useMemo(() => stream.events.filter((e) => !HIDDEN_EVENTS.has(e.type)), [stream.events]);
  const shown = useMemo(() => { const f = FILTERS.find((x) => x.id === filter)!; return events.filter((e) => f.match(e.type)); }, [events, filter]);

  if (!run) {
    return (
      <div className="grid-bg flex h-full items-center justify-center">
        <Empty icon={<Telescope className="h-5 w-5" />} title="No research yet" action={<Button variant="primary" onClick={() => startRun()}><Play className="h-4 w-4" /> Start research</Button>} />
      </div>
    );
  }
  const live = RUN_ACTIVE.includes(run.status);
  return (
    <div className="mx-auto h-full max-w-[1280px] overflow-y-auto px-4 py-5 sm:px-6">
      <RunHeader run={run} connection={stream.state} live={live} lastEventAt={stream.events.at(-1)?.created_at ?? null} />
      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
        <Card className="min-w-0">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-2">
            <div className="flex gap-1" role="tablist" aria-label="Filter events">
              {FILTERS.map((f) => (
                <button key={f.id} role="tab" aria-selected={filter === f.id} onClick={() => setFilter(f.id)}
                  className={cn("h-7 rounded-md px-2.5 text-[12.5px]", filter === f.id ? "bg-panel-3 text-fg" : "text-fg-3 hover:text-fg")}>{f.label}</button>
              ))}
            </div>
          </div>
          <Feed events={shown} live={live} onPick={(sel) => router.push(`/g/${graph.id}?${sel.kind === "entity" ? "entity" : "rel"}=${sel.id}`)} />
        </Card>
        <div className="space-y-4">
          {live && <Steer run={run} />}
          <Missing />
          <History runs={runs} currentId={run.id} onPick={(id) => router.replace(`/g/${graph.id}/research?run=${id}`)} />
        </div>
      </div>
    </div>
  );
}

function RunHeader({ run, connection, live, lastEventAt }: { run: Run; connection: string; live: boolean; lastEventAt: string | null }) {
  const { graph, startRun } = useGraphCtx();
  const qc = useQueryClient();
  const [extend, setExtend] = useState(false);
  const now = useNow(1000, live);
  const control = useMutation({
    mutationFn: (v: { action: "pause" | "resume" | "cancel"; body?: unknown }) => post<Run>(`/runs/${run.id}/${v.action}`, v.body),
    onSuccess: (_, v) => { qc.invalidateQueries({ queryKey: ["runs", graph.id] }); toast({ pause: "Pausing", resume: "Resuming", cancel: "Cancelling" }[v.action]); },
    onError: toastError,
  });
  const b = run.budget;
  const sim = run.simulated;
  const stat = (label: string, value: string, of: string, v: number, max: number, hint?: string) => (
    <div className="min-w-0" title={hint}>
      <p className="label">{label}</p>
      <p className="tnum mt-1.5 text-[18px] font-medium leading-none tracking-tight">{value}<span className="ml-1 text-[12px] font-normal tracking-normal text-fg-3">/ {of}</span></p>
      <div className="mt-2.5"><Meter value={v} max={max} label={label} /></div>
    </div>
  );
  // Honest liveness: if nothing has been logged for a while the run is waiting on a provider, and says so.
  const quietSec = live && lastEventAt ? Math.max(0, (now - new Date(lastEventAt).getTime()) / 1000) : 0;
  const elapsedSec = run.started_at ? ((run.finished_at ? new Date(run.finished_at).getTime() : now) - new Date(run.started_at).getTime()) / 1000 : 0;
  return (
    <Card className="overflow-hidden">
      <div className="grid-bg flex flex-wrap items-start gap-x-6 gap-y-3 border-b border-line p-4 sm:p-5">
        <div className="min-w-0 flex-1 basis-[320px]">
          <div className="flex flex-wrap items-center gap-2">
            <RunPill status={run.status} />
            <Badge tone={sim ? "muted" : "accent"} dashed={sim} title={sim ? "Synthetic sources; no real spend" : "Live web research"}>{sim ? "Simulation" : "Live"}</Badge>
            <Tip label={connection === "live" ? "Streaming events live" : connection === "ended" ? "Run finished; full event log replayed from the server" : connection === "polling" ? "Streaming unavailable; polling for events" : "Reconnecting. Missed events will be replayed."}>
              <span className="inline-flex items-center text-fg-3" aria-label={`Connection: ${connection}`}>
                {connection === "live" || connection === "ended" ? <Wifi className="h-3.5 w-3.5" /> : <WifiOff className="h-3.5 w-3.5 text-warn" />}
              </span>
            </Tip>
          </div>
          {quietSec > 25 && run.status !== "pausing" && (
            <p role="status" className={cn("mt-2 inline-flex items-center gap-2 rounded-md px-2 py-1 text-[12px]", quietSec > 120 ? "bg-warn-soft text-warn" : "bg-panel-2 text-fg-2")}>
              <span className="anim-blink h-1.5 w-1.5 rounded-full bg-current" />
              {quietSec > 120
                ? `Quiet for ${duration(lastEventAt)} · retrying automatically`
                : `Waiting on the ${run.simulated ? "worker" : "model"} · ${Math.floor(quietSec)}s`}
            </p>
          )}
          <h2 className="mt-2.5 font-display text-[26px] leading-tight sm:text-[30px]">{run.objective}</h2>
          <p className="mt-1.5 line-clamp-2 text-[13px] text-fg-2">
            {live ? firstLine(run.current_focus, 160) || "Getting started" : run.summary?.replace(/\*\*|^#+\s+/gm, "") || (run.error ? <span className="text-danger">{run.error}</span> : "")}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
          {["planning", "researching", "verifying", "queued", "resuming"].includes(run.status) && (
            <Button variant="outline" size="sm" loading={control.isPending && control.variables?.action === "pause"} onClick={() => control.mutate({ action: "pause" })}><Pause className="h-3.5 w-3.5" /> Pause</Button>)}
          {run.status === "paused" && <Button variant="primary" size="sm" loading={control.isPending} onClick={() => control.mutate({ action: "resume" })}><Play className="h-3.5 w-3.5" /> Resume</Button>}
          {run.status === "failed" && <Button variant="outline" size="sm" loading={control.isPending} onClick={() => control.mutate({ action: "resume" })} title="Continue from the last completed step"><Play className="h-3.5 w-3.5" /> Retry</Button>}
          {run.status === "budget_exhausted" && <Button variant="primary" size="sm" onClick={() => setExtend(true)}><Plus className="h-3.5 w-3.5" /> Extend</Button>}
          {(live || run.status === "paused" || run.status === "budget_exhausted") && run.status !== "cancel_requested" && (
            <Button variant="danger" size="sm" loading={control.isPending && control.variables?.action === "cancel"} onClick={() => control.mutate({ action: "cancel" })}><Ban className="h-3.5 w-3.5" /> Cancel</Button>)}
          {!live && run.status !== "paused" && run.status !== "budget_exhausted" && <Button variant="primary" size="sm" onClick={() => startRun()}><Play className="h-3.5 w-3.5" /> New run</Button>}
        </div>
      </div>
      {!live && run.suggestions_created > 0 && (
        <div className="flex flex-wrap items-center gap-2 border-b border-line bg-accent-soft px-4 py-2.5 sm:px-5">
          <span className="tnum mr-auto text-[13px]">
            <strong className="font-medium">{run.suggestions_created}</strong> found
            {run.suggestions_accepted > 0 && <> · <strong className="font-medium">{run.suggestions_accepted}</strong> approved</>}
            {graph.stats.pending_suggestions > 0 && <> · <strong className="font-medium">{graph.stats.pending_suggestions}</strong> to review</>}
          </span>
          {graph.stats.pending_suggestions > 0 && <Link href={`/g/${graph.id}/review`}><Button size="sm" variant="primary">Review <ArrowRight className="h-3.5 w-3.5" /></Button></Link>}
          <Link href={`/g/${graph.id}/table`}><Button size="sm" variant="outline">Table</Button></Link>
          <Link href={`/g/${graph.id}`}><Button size="sm" variant="outline">Graph</Button></Link>
        </div>
      )}
      <div className="grid grid-cols-2 gap-x-8 gap-y-4 p-4 sm:grid-cols-4 sm:p-5">
        {stat("Time", duration(run.started_at, run.finished_at ?? new Date(now).toISOString()), `${b.deadline_minutes}m`, elapsedSec / 60, b.deadline_minutes, run.started_at ? `Started ${fmtDateTime(run.started_at)}` : undefined)}
        {stat("Cost", usd(run.est_cost_usd), usd(b.max_usd), run.est_cost_usd, b.max_usd, sim ? "Simulated; no real spend" : "Estimated from usage")}
        {stat("Web calls", String(run.tavily_calls), String(b.max_tavily_calls), run.tavily_calls, b.max_tavily_calls, `${run.tavily_credits.toFixed(0)} credits`)}
        {stat("Model calls", String(run.model_calls), String(b.max_model_calls), run.model_calls, b.max_model_calls, run.model_calls ? `${((run.input_tokens + run.output_tokens) / 1000).toFixed(1)}k tokens` : undefined)}
      </div>
      <ExtendDialog run={run} open={extend} onOpenChange={setExtend} onSubmit={(body) => control.mutate({ action: "resume", body }, { onSuccess: () => setExtend(false) })} busy={control.isPending} />
    </Card>
  );
}

function ExtendDialog({ run, open, onOpenChange, onSubmit, busy }: { run: Run; open: boolean; onOpenChange: (v: boolean) => void; onSubmit: (b: unknown) => void; busy: boolean }) {
  const [b, setB] = useState(run.budget);
  useEffect(() => { if (open) queueMicrotask(() => setB({ max_usd: run.budget.max_usd * 2, max_tavily_calls: run.budget.max_tavily_calls * 2, max_model_calls: run.budget.max_model_calls * 2, deadline_minutes: run.budget.deadline_minutes })); }, [open, run.budget]);
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Extend budget">
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); onSubmit(b); }}>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Cost (USD)"><Input type="number" step={0.25} min={0} value={b.max_usd} onChange={(e) => setB({ ...b, max_usd: +e.target.value })} /></Field>
          <Field label="Web calls"><Input type="number" min={0} value={b.max_tavily_calls} onChange={(e) => setB({ ...b, max_tavily_calls: +e.target.value })} /></Field>
          <Field label="Model calls"><Input type="number" min={0} value={b.max_model_calls} onChange={(e) => setB({ ...b, max_model_calls: +e.target.value })} /></Field>
          <Field label="Deadline (min)"><Input type="number" min={1} value={b.deadline_minutes} onChange={(e) => setB({ ...b, deadline_minutes: +e.target.value })} /></Field>
        </div>
        <div className="flex justify-end gap-2 pt-1"><Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button><Button type="submit" variant="primary" loading={busy}>Resume</Button></div>
      </form>
    </Dialog>
  );
}

function Feed({ events, live, onPick }: { events: RunEvent[]; live: boolean; onPick: (s: { kind: "entity" | "relationship"; id: string }) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  useEffect(() => { const el = box.current; if (el && stick.current) el.scrollTop = el.scrollHeight; }, [events.length]);
  return (
    <div ref={box} onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }}
      className="max-h-[calc(100dvh-380px)] min-h-[320px] overflow-y-auto p-1.5" role="log" aria-label="Research activity" aria-live={live ? "polite" : "off"}>
      {events.length === 0 && <p className="px-3 py-10 text-center text-[13px] text-fg-3">{live ? "Starting…" : "Nothing here."}</p>}
      <ol>
        {events.map((ev) => <li key={ev.seq} className={cn("anim-fade-up", ev.type === "slice.started" && "mt-2 border-t border-line pt-2 first:mt-0 first:border-0 first:pt-0")}><EventRow ev={ev} onPick={onPick} /></li>)}
      </ol>
      {live && <p className="flex items-center gap-2 px-3 py-2 text-[12px] text-fg-3"><span className="anim-blink h-1.5 w-1.5 rounded-full bg-accent" /> Working</p>}
    </div>
  );
}

function Steer({ run }: { run: Run }) {
  const [text, setText] = useState("");
  const send = useMutation({ mutationFn: () => post(`/runs/${run.id}/steer`, { instruction: text.trim() }), onSuccess: () => { setText(""); toast("Steering sent"); }, onError: toastError });
  return (
    <Card className="p-3.5">
      <h3 className="label mb-2">Steer</h3>
      <form onSubmit={(e) => { e.preventDefault(); if (text.trim().length > 1) send.mutate(); }} className="flex gap-1.5">
        <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="Prioritise the newest companies" aria-label="Steering instruction" />
        <Button type="submit" variant="outline" size="icon" className="!h-9 !w-9" aria-label="Send steering instruction" loading={send.isPending} disabled={text.trim().length < 2}>{!send.isPending && <Send className="h-4 w-4" />}</Button>
      </form>
      {run.simulated && <p className="mt-2 text-[11.5px] text-fg-3">Simulations ignore steering.</p>}
    </Card>
  );
}

function Missing() {
  const { graph, startRun, activeRun } = useGraphCtx();
  const frontier = useFrontier(graph.id);
  const qc = useQueryClient();
  const dismiss = useMutation({ mutationFn: (id: string) => post(`/graphs/${graph.id}/frontier/${id}/dismiss`), onSuccess: () => qc.invalidateQueries({ queryKey: ["frontier", graph.id] }), onError: toastError });
  return (
    <Card className="p-3.5">
      <h3 className="label mb-2.5 flex items-center gap-1.5"><Compass className="h-3 w-3" /> Open questions</h3>
      {frontier.data?.length === 0 && <p className="text-[13px] text-fg-3">None yet.</p>}
      <ul className="space-y-2">
        {frontier.data?.slice(0, 6).map((q) => (
          <li key={q.id} title={q.rationale || undefined} className="group flex items-start gap-2 rounded-lg bg-bg-2 p-2.5 shadow-[0_0_0_1px_var(--line)] transition-shadow hover:shadow-[0_0_0_1px_var(--line-2)]">
            <p className="min-w-0 flex-1 text-[13px] leading-snug">{q.question}</p>
            <span className="flex shrink-0 items-center gap-0.5">
              <Tip label="Dismiss"><button aria-label="Dismiss question" onClick={() => dismiss.mutate(q.id)} className="flex h-7 w-7 items-center justify-center rounded-md text-fg-3 opacity-0 transition-opacity hover:text-fg focus-visible:opacity-100 group-hover:opacity-100"><X className="h-3.5 w-3.5" /></button></Tip>
              <Tip label={activeRun ? "A run is already in progress" : "Research this"}><Button size="icon" variant="outline" className="!h-7 !w-7" aria-label={`Investigate: ${q.question}`} disabled={!!activeRun} onClick={() => startRun(q.question, q.id)}><ArrowRight className="h-3.5 w-3.5" /></Button></Tip>
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function History({ runs, currentId, onPick }: { runs: Run[]; currentId: string; onPick: (id: string) => void }) {
  if (runs.length < 2) return null;
  return (
    <Card className="p-3.5">
      <h3 className="label mb-2">Runs</h3>
      <ul className="space-y-0.5">
        {runs.map((r) => (
          <li key={r.id}>
            <button onClick={() => onPick(r.id)} aria-current={r.id === currentId} className={cn("flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-panel-2", r.id === currentId && "bg-panel-2")}>
              <RunPill status={r.status} className="!shadow-none !bg-transparent !px-0" />
              <span className="tnum ml-auto shrink-0 text-[11.5px] text-fg-3">{r.suggestions_created} found · {timeAgo(r.created_at)}</span>
            </button>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export default function Page() {
  return <Suspense><ResearchPage /></Suspense>;
}
