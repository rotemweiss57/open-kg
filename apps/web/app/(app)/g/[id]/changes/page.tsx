"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, BellOff, Eye, Pause, Play, RefreshCw, Trash2 } from "lucide-react";
import Link from "next/link";
import { useEffect } from "react";
import { SourceCard } from "@/components/evidence";
import { useGraphCtx } from "@/components/graph-context";
import { toast, toastError } from "@/components/toast";
import { Badge, Button, Card, Empty, Skeleton, Tip } from "@/components/ui";
import { del, patch, post, type Change, type Monitor, type MonitorRun } from "@/lib/api";
import { cadence, fmtDateTime, fmtValid, humanize, timeAgo, timeUntil } from "@/lib/format";
import { useChanges, useInvalidateGraph, useMonitors } from "@/lib/hooks";

const KIND: Record<string, { tone: "warn" | "info" | "accent" | "neutral"; label: string }> = {
  changed: { tone: "accent", label: "Changed" },
  new: { tone: "info", label: "New" },
  contradicted: { tone: "warn", label: "Conflict" },
  stale: { tone: "neutral", label: "Stale" },
};

export default function ChangesPage() {
  const { graph } = useGraphCtx();
  const monitors = useMonitors(graph.id);
  const changes = useChanges(graph.id);
  const qc = useQueryClient();
  useEffect(() => {
    // Opening this page is what "reading" a change notification means.
    post(`/notifications/read?graph_id=${graph.id}`).then(() => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
      qc.invalidateQueries({ queryKey: ["graph", graph.id] });
    }).catch(() => {});
  }, [graph.id, qc]);

  return (
    <div className="mx-auto grid h-full max-w-[1280px] gap-5 overflow-y-auto px-4 pb-16 pt-5 sm:px-6 lg:grid-cols-[minmax(0,1fr)_380px]">
      <section aria-labelledby="changes-h" className="min-w-0">
        <h2 id="changes-h" className="font-display text-[30px] leading-none">Changes</h2>
        {changes.isLoading && <div className="mt-5 space-y-3">{[0, 1].map((i) => <Skeleton key={i} className="h-40 w-full !rounded-xl" />)}</div>}
        {changes.data?.length === 0 && (
          <Card className="mt-5"><Empty icon={<BellOff className="h-5 w-5" />} title="No changes yet">Watch an entity to track it.</Empty></Card>
        )}
        <ol className="mt-5 space-y-3">
          {changes.data?.map((c) => <ChangeCard key={c.id} c={c} gid={graph.id} />)}
        </ol>
      </section>

      <aside aria-labelledby="monitors-h">
        <h2 id="monitors-h" className="label mb-2 mt-2 flex items-center gap-1.5"><Eye className="h-3 w-3" /> Watching <span className="tnum opacity-70">{monitors.data?.length ?? 0}</span></h2>
        {monitors.data?.length === 0 && <Card className="p-4 text-[13px] text-fg-3">Nothing watched yet.</Card>}
        <ul className="space-y-2.5">
          {monitors.data?.map((m) => <MonitorCard key={m.id} m={m} />)}
        </ul>
      </aside>
    </div>
  );
}

function ChangeCard({ c, gid }: { c: Change; gid: string }) {
  const k = KIND[c.kind] ?? { tone: "neutral" as const, label: c.kind };
  const pending = c.suggestion_status === "pending";
  return (
    <li className="rounded-xl bg-panel shadow-[0_0_0_1px_var(--line)]">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
        <Badge tone={k.tone}>{k.label}</Badge>
        <Link href={`/g/${gid}?entity=${c.entity_id}`} className="text-[14px] font-medium hover:text-accent-text">{c.entity_name}</Link>
        {c.predicate && <span className="text-[12.5px] text-fg-3">{humanize(c.predicate)}</span>}
        <span className="tnum ml-auto text-[11.5px] text-fg-3">{fmtDateTime(c.created_at)}</span>
      </div>
      {c.kind === "stale" ? <p className="p-4 text-[13px] text-fg-2">{c.summary}</p> : (
        <div className="grid gap-px bg-line sm:grid-cols-2">
          <div className="bg-panel p-4">
            <p className="label">Before</p>
            <p className="mt-1 text-[17px] text-fg-2 line-through decoration-line-2">{c.before ? String(c.before.value) : "—"}</p>
            {typeof c.before?.valid_from === "string" && <p className="tnum text-[11.5px] text-fg-3">since {fmtValid(c.before.valid_from)}</p>}
            <div className="mt-3 space-y-2">{c.before_evidence.slice(0, 2).map((e) => <SourceCard key={e.id} ev={e} />)}</div>
          </div>
          <div className="bg-panel p-4">
            <p className="label !text-accent-text">After</p>
            <p className="mt-1 text-[17px]">{c.after ? String(c.after.value) : "—"}</p>
            {typeof c.after?.valid_from === "string" && <p className="tnum text-[11.5px] text-fg-3">from {fmtValid(c.after.valid_from)}</p>}
            <div className="mt-3 space-y-2">{c.after_evidence.slice(0, 2).map((e) => <SourceCard key={e.id} ev={e} />)}</div>
          </div>
        </div>
      )}
      {c.suggestion_id && (
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-4 py-2.5 text-[12.5px]">
          <span className="text-fg-3">{pending ? "Not applied yet" : c.suggestion_status === "rejected" ? "Rejected" : "Applied"}</span>
          {pending && <Link href={`/g/${gid}/review`} className="inline-flex items-center gap-1 text-accent-text hover:underline">Review <ArrowRight className="h-3 w-3" /></Link>}
        </div>
      )}
    </li>
  );
}

function MonitorCard({ m }: { m: Monitor }) {
  const invalidate = useInvalidateGraph(m.graph_id);
  const check = useMutation({
    mutationFn: () => post<MonitorRun>(`/monitors/${m.id}/check`),
    onSuccess: (r) => { toast(r.outcome === "no_change" ? "No change" : r.outcome === "failed" ? "Check failed" : r.summary, r.outcome === "failed" ? "error" : "ok"); invalidate(); },
    onError: toastError,
  });
  const set = useMutation({ mutationFn: (status: string) => patch(`/monitors/${m.id}`, { status }), onSuccess: invalidate, onError: toastError });
  const remove = useMutation({ mutationFn: () => del(`/monitors/${m.id}`), onSuccess: () => { toast("Removed"); invalidate(); }, onError: toastError });
  const outcome = m.last_outcome === "no_change" ? "no change" : humanize(m.last_outcome || "not checked yet");
  return (
    <li className="rounded-xl bg-panel p-3.5 shadow-[0_0_0_1px_var(--line)]">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <Link href={`/g/${m.graph_id}?entity=${m.entity_id}`} className="block truncate text-[14px] font-medium hover:text-accent-text">{m.target_name}</Link>
        </div>
        {m.status === "active" ? <Badge tone="ok">Active</Badge> : m.status === "paused" ? <Badge tone="warn">Paused</Badge> : <Badge>Muted</Badge>}
      </div>
      <div className="mt-2 flex flex-wrap gap-1">{m.predicates.map((p) => <span key={p} className="rounded-md bg-panel-2 px-1.5 py-0.5 text-[11.5px] text-fg-2">{humanize(p)}</span>)}</div>
      <p className="tnum mt-2.5 text-[12px] text-fg-3" title={`Last result: ${outcome}`}>
        {cadence(m.cadence_minutes)} · checked {timeAgo(m.last_checked_at)}{m.status !== "paused" && <> · next {timeUntil(m.next_run_at)}</>}
      </p>
      {m.failure && <p className="mt-2 rounded-md bg-danger-soft px-2 py-1 text-[12px] text-danger">{m.failure}</p>}
      <div className="mt-3 flex items-center gap-1.5">
        <Button size="sm" variant="outline" loading={check.isPending} onClick={() => check.mutate()}>
          <RefreshCw className="h-3.5 w-3.5" /> Check now
        </Button>
        <div className="flex-1" />
        <Tip label={m.status === "muted" ? "Unmute" : "Mute"}>
          <Button size="icon" variant="ghost" aria-label={m.status === "muted" ? "Unmute monitor" : "Mute monitor"} onClick={() => set.mutate(m.status === "muted" ? "active" : "muted")}><BellOff className="h-3.5 w-3.5" /></Button>
        </Tip>
        <Tip label={m.status === "paused" ? "Resume" : "Pause"}>
          <Button size="icon" variant="ghost" aria-label={m.status === "paused" ? "Resume monitor" : "Pause monitor"} onClick={() => set.mutate(m.status === "paused" ? "active" : "paused")}>
            {m.status === "paused" ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
          </Button>
        </Tip>
        <Tip label="Remove"><Button size="icon" variant="ghost" aria-label="Delete monitor" loading={remove.isPending} onClick={() => remove.mutate()}><Trash2 className="h-3.5 w-3.5" /></Button></Tip>
      </div>
    </li>
  );
}
