"use client";

import { useMutation } from "@tanstack/react-query";
import { ArrowRight, Check, ChevronDown, GitMerge, Inbox, Network, ShieldCheck, Sparkles, TriangleAlert, Waypoints } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ClaimBlock, predicateLabel } from "@/components/evidence";
import { useGraphCtx } from "@/components/graph-context";
import { SuggestionActions, SuggestionTitle } from "@/components/suggestion-actions";
import { toast, toastError } from "@/components/toast";
import { Badge, Button, Card, cn, Confidence, Empty, ErrorNote, Kbd, Skeleton, StatusBadge } from "@/components/ui";
import { patch, post, type Suggestion } from "@/lib/api";
import { fmtDateTime, fmtValid, plural, prettyTitle } from "@/lib/format";
import { useDecide, useInvalidateGraph, useSuggestions } from "@/lib/hooks";

const GROUPS: { kind: string; label: string; icon: React.ReactNode; blurb: string }[] = [
  { kind: "contradiction", label: "Conflicts", icon: <TriangleAlert className="h-3.5 w-3.5 text-warn" />, blurb: "Sources disagree. Decide which value the graph should hold." },
  { kind: "claim", label: "Facts", icon: <Waypoints className="h-3.5 w-3.5" />, blurb: "New or changed facts about entities already in the graph." },
  { kind: "merge", label: "Duplicates", icon: <GitMerge className="h-3.5 w-3.5 text-warn" />, blurb: "Same name, different identity keys. Never merged automatically." },
  { kind: "entity", label: "Entities", icon: <Sparkles className="h-3.5 w-3.5" />, blurb: "Candidates discovered by research." },
  { kind: "relationship", label: "Links", icon: <Network className="h-3.5 w-3.5" />, blurb: "Approving a relationship also accepts its endpoints." },
];
const bulkable = (s: Suggestion) => s.risk === "low" && s.kind !== "merge" && s.kind !== "contradiction";
// "Approve all" in a bundle never includes low-confidence items (quote not found in the page, or inferred).
const bundleSafe = (s: Suggestion) => bulkable(s) && s.confidence !== "low";

export default function ReviewPage() {
  const { graph } = useGraphCtx();
  const [tab, setTab] = useState<"pending" | "decided">("pending");
  const q = useSuggestions(graph.id, tab);
  const [open, setOpen] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState<"entity" | "kind">("entity");
  const invalidate = useInvalidateGraph(graph.id);
  const decide = useDecide(graph.id);
  const items = useMemo(() => q.data?.items ?? [], [q.data]);
  const ordered = useMemo(() => GROUPS.flatMap((g) => items.filter((s) => s.kind === g.kind)), [items]);

  const bulk = useMutation({
    mutationFn: (action: "approve" | "reject") => post<{ decided: string[]; failed: Record<string, string> }>(`/graphs/${graph.id}/suggestions/bulk`, { ids: [...picked], action }),
    onSuccess: (res, action) => {
      const failed = Object.keys(res.failed).length;
      toast(`${res.decided.length} ${action === "approve" ? "approved" : "rejected"}${failed ? `, ${failed} failed` : ""}`, failed ? "error" : "ok");
      setPicked(new Set()); invalidate();
    },
    onError: toastError,
  });

  // Keyboard triage: j/k to move, a to approve, r to reject, enter to open.
  useEffect(() => {
    if (tab !== "pending") return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, select, [role=dialog]") || e.metaKey || e.ctrlKey || e.altKey) return;
      const idx = ordered.findIndex((s) => s.id === open);
      if (e.key === "j" || e.key === "k") {
        e.preventDefault();
        const next = ordered[Math.min(ordered.length - 1, Math.max(0, idx + (e.key === "j" ? 1 : -1)))] ?? ordered[0];
        if (next) { setOpen(next.id); document.getElementById(`sug-${next.id}`)?.scrollIntoView({ block: "nearest" }); }
      }
      if ((e.key === "a" || e.key === "r") && idx >= 0 && !decide.isPending) {
        const cur = ordered[idx];
        decide.mutate({ id: cur.id, action: e.key === "a" ? "approve" : "reject" }, {
          onSuccess: () => { toast(e.key === "a" ? "Approved" : "Rejected"); setOpen(ordered[idx + 1]?.id ?? ordered[idx - 1]?.id ?? null); },
          onError: toastError });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ordered, open, decide, tab]);

  // Bundle suggestions around their hub entity so one decision can cover a company together with
  // its people, investors and facts. A relationship joins the busier of its two endpoints.
  const bundles = useMemo(() => {
    const weight = new Map<string, number>();
    const bump = (id?: string | null) => { if (id) weight.set(id, (weight.get(id) ?? 0) + 1); };
    const ends = (x: Suggestion) => [(x.payload as any).from?.id as string | undefined, (x.payload as any).to?.id as string | undefined]; // eslint-disable-line @typescript-eslint/no-explicit-any
    items.forEach((x) => { if (x.kind === "relationship") ends(x).forEach(bump); });
    const names = new Map<string, string>();
    items.forEach((x) => {
      if (x.kind === "entity" && x.entity_id) names.set(x.entity_id, String((x.payload as any).name ?? x.title)); // eslint-disable-line @typescript-eslint/no-explicit-any
      if (x.kind === "relationship") { const p = x.payload as any; if (p.from?.id) names.set(p.from.id, names.get(p.from.id) ?? p.from.name); if (p.to?.id) names.set(p.to.id, names.get(p.to.id) ?? p.to.name); } // eslint-disable-line @typescript-eslint/no-explicit-any
    });
    const hubOf = (x: Suggestion): string => {
      if (x.kind === "relationship") { const [a, b] = ends(x); return (weight.get(a ?? "") ?? 0) >= (weight.get(b ?? "") ?? 0) ? a ?? "" : b ?? ""; }
      return x.entity_id ?? "";
    };
    // a person/investor entity with a single link is filed under the hub it links to
    const linkHub = new Map<string, string>();
    items.forEach((x) => { if (x.kind === "relationship") { const h = hubOf(x); ends(x).forEach((e) => { if (e && e !== h && (weight.get(e) ?? 0) <= 2 && !linkHub.has(e)) linkHub.set(e, h); }); } });
    const by = new Map<string, Suggestion[]>();
    items.forEach((x) => {
      let h = hubOf(x);
      if (x.kind !== "relationship" && linkHub.has(h)) h = linkHub.get(h)!;
      by.set(h, [...(by.get(h) ?? []), x]);
    });
    return [...by.entries()].map(([id, list]) => ({ id, name: names.get(id) ?? list[0].title, list,
      risky: list.filter((x) => !bundleSafe(x)).length })).sort((a, b) => b.list.length - a.list.length);
  }, [items]);
  const lowRisk = items.filter((s) => bulkable(s) && s.confidence === "high");
  const approveBundle = useMutation({
    mutationFn: (ids: string[]) => post<{ decided: string[]; failed: Record<string, string> }>(`/graphs/${graph.id}/suggestions/bulk`, { ids, action: "approve" }),
    onSuccess: (r) => { toast(`${r.decided.length} approved`); invalidate(); },
    onError: toastError,
  });
  const eligible = q.data?.policy_eligible ?? 0;
  const applyPolicy = useMutation({
    mutationFn: () => post<{ approved: number }>(`/graphs/${graph.id}/policy/apply`),
    onSuccess: (r) => { toast(`${r.approved} approved`); invalidate(); },
    onError: toastError,
  });
  const setAuto = useMutation({
    mutationFn: () => patch(`/graphs/${graph.id}`, { autonomy_mode: "auto_accept_high" }),
    onSuccess: () => { toast("Auto-approve is on"); invalidate(); },
    onError: toastError,
  });
  return (
    <div className="mx-auto h-full max-w-[920px] overflow-y-auto px-4 pb-28 pt-5 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h2 className="font-display text-[30px] leading-none">Review</h2>
        <div className="flex gap-1 rounded-lg bg-panel p-0.5 shadow-[0_0_0_1px_var(--line)]" role="tablist">
          {(["pending", "decided"] as const).map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} onClick={() => { setTab(t); setPicked(new Set()); setOpen(null); }}
              className={cn("h-7 rounded-md px-3 text-[12.5px] capitalize", tab === t ? "bg-panel-3 text-fg" : "text-fg-3 hover:text-fg")}>
              {t === "pending" ? `Pending${graph.stats.pending_suggestions ? ` · ${graph.stats.pending_suggestions}` : ""}` : "Decided"}
            </button>
          ))}
        </div>
      </div>

      {q.isError && <div className="mt-4"><ErrorNote error={q.error} retry={() => q.refetch()} /></div>}
      {q.isLoading && <div className="mt-5 space-y-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-16 w-full !rounded-xl" />)}</div>}

      {tab === "pending" && q.data && items.length === 0 && (
        <Card className="mt-5"><Empty icon={<Inbox className="h-5 w-5" />} title="Inbox clear"
          action={<Link href={`/g/${graph.id}`}><Button variant="outline">Open graph <ArrowRight className="h-4 w-4" /></Button></Link>} /></Card>
      )}

      {tab === "pending" && eligible > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-3 rounded-xl bg-accent-soft py-2.5 pl-3.5 pr-2.5"
          title="Low-risk additions backed by two independent sources, or by an organisation's own site. Changes, conflicts, merges and inferred links are never included.">
          <ShieldCheck className="h-4 w-4 shrink-0 text-accent-text" />
          <p className="min-w-0 flex-1 text-[13.5px]"><strong className="tnum font-medium">{eligible}</strong> {eligible === 1 ? "has" : "have"} enough evidence</p>
          <div className="flex gap-2">
            {graph.autonomy_mode !== "auto_accept_high" && <Button size="sm" variant="outline" loading={setAuto.isPending} onClick={() => setAuto.mutate()}>Always</Button>}
            <Button size="sm" variant="primary" loading={applyPolicy.isPending} onClick={() => applyPolicy.mutate()}><Check className="h-3.5 w-3.5" /> Approve {eligible}</Button>
          </div>
        </div>
      )}
      {tab === "pending" && items.length > 0 && (
        <>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
            <div className="inline-flex gap-0.5 rounded-lg bg-panel p-0.5 shadow-[0_0_0_1px_var(--line)]" role="tablist" aria-label="Group suggestions">
              {([["entity", "By entity"], ["kind", "By type"]] as const).map(([id, label]) => (
                <button key={id} role="tab" aria-selected={mode === id} onClick={() => setMode(id)} className={cn("h-7 rounded-md px-3 text-[12.5px] transition-colors", mode === id ? "bg-panel-3 text-fg" : "text-fg-3 hover:text-fg")}>{label}</button>
              ))}
            </div>
            <div className="flex items-center gap-4 text-[12px] text-fg-3">
              {lowRisk.length > 0 && (
                <button className="text-fg-2 hover:text-fg" onClick={() => setPicked(new Set(lowRisk.map((s) => s.id)))}>Select {lowRisk.length} high-confidence</button>
              )}
              <span className="hidden items-center gap-1 sm:flex" title="j / k move · a approve · r reject"><Kbd>j</Kbd><Kbd>k</Kbd><Kbd>a</Kbd><Kbd>r</Kbd></span>
            </div>
          </div>
          {mode === "entity" && (
            <ul className="mt-4 space-y-2.5">
              {bundles.map((b) => {
                const safe = b.list.filter(bundleSafe);
                return (
                  <li key={b.id} className="rounded-xl bg-panel shadow-[0_0_0_1px_var(--line)]">
                    <details className="group/b" open={bundles.length <= 3}>
                      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3 [&::-webkit-details-marker]:hidden">
                        <ChevronDown className="h-4 w-4 shrink-0 -rotate-90 text-fg-3 transition-transform group-open/b:rotate-0" />
                        <span className="min-w-0 flex-1 basis-[200px]">
                          <span className="block truncate text-[15px] font-medium leading-tight"><SuggestionTitle title={b.name} /></span>
                          <span className="text-[12px] text-fg-3">
                            {Object.entries(b.list.reduce<Record<string, number>>((a, x) => ({ ...a, [x.kind]: (a[x.kind] ?? 0) + 1 }), {})).map(([k, n]) => `${n} ${k === "entity" ? (n === 1 ? "entity" : "entities") : k === "relationship" ? (n === 1 ? "link" : "links") : k === "claim" ? (n === 1 ? "fact" : "facts") : k}`).join(" · ")}
                            {b.risky > 0 && <span className="text-warn"> · {b.risky} to check</span>}
                          </span>
                        </span>
                        {safe.length > 0 && (
                          <Button size="sm" variant="primary" loading={approveBundle.isPending && approveBundle.variables?.[0] === safe[0].id}
                            onClick={(e) => { e.preventDefault(); approveBundle.mutate(safe.map((x) => x.id)); }}>
                            <Check className="h-3.5 w-3.5" /> Approve {safe.length}
                          </Button>
                        )}
                      </summary>
                      <ul className="space-y-1.5 border-t border-line p-2">
                        {b.list.map((x) => (
                          <SuggestionCard key={x.id} s={x} gid={graph.id} open={open === x.id} onToggle={() => setOpen(open === x.id ? null : x.id)}
                            picked={picked.has(x.id)} onPick={bulkable(x) ? () => setPicked((p) => { const n = new Set(p); if (n.has(x.id)) n.delete(x.id); else n.add(x.id); return n; }) : undefined} />
                        ))}
                      </ul>
                    </details>
                  </li>
                );
              })}
            </ul>
          )}
          {mode === "kind" && GROUPS.map((g) => {
            const group = items.filter((s) => s.kind === g.kind);
            if (!group.length) return null;
            return (
              <section key={g.kind} className="mt-6" aria-label={g.label}>
                <h3 className="flex items-center gap-2 text-[13px] font-medium">{g.icon}{g.label}<span className="tnum text-fg-3">{group.length}</span>
                  {group.some(bulkable) && (
                    <button className="ml-auto text-[12px] font-normal text-fg-2 hover:text-fg" onClick={() => setPicked((p) => {
                      const ids = group.filter(bulkable).map((x) => x.id); const all = ids.every((i) => p.has(i)); const n = new Set(p);
                      ids.forEach((i) => (all ? n.delete(i) : n.add(i))); return n; })}>
                      {group.filter(bulkable).every((x) => picked.has(x.id)) ? "Clear" : "Select all"}
                    </button>
                  )}</h3>
                <ul className="mt-2 space-y-1.5">
                  {group.map((s) => (
                    <SuggestionCard key={s.id} s={s} gid={graph.id} open={open === s.id} onToggle={() => setOpen(open === s.id ? null : s.id)}
                      picked={picked.has(s.id)} onPick={bulkable(s) ? () => setPicked((p) => { const n = new Set(p); if (n.has(s.id)) n.delete(s.id); else n.add(s.id); return n; }) : undefined} />
                  ))}
                </ul>
              </section>
            );
          })}
        </>
      )}

      {tab === "decided" && q.data && (
        items.length === 0 ? <Card className="mt-5"><Empty title="No decisions yet" /></Card> : (
          <ul className="mt-5 space-y-1.5">
            {items.map((s) => (
              <li key={s.id} className="rounded-xl bg-panel p-3.5 shadow-[0_0_0_1px_var(--line)]">
                <div className="flex flex-wrap items-center gap-2">
                  {s.status === "rejected" ? <Badge tone="danger">Rejected</Badge> : s.status === "auto_approved" ? <Badge tone="info"><ShieldCheck className="h-3 w-3" /> Auto</Badge> : s.status === "superseded" ? <Badge>Superseded</Badge> : <Badge tone="ok"><Check className="h-3 w-3" /> Approved</Badge>}
                  <span className="min-w-0 flex-1 truncate text-[13.5px]"><SuggestionTitle title={s.title} /></span>
                  <span className="tnum text-[11.5px] text-fg-3">{fmtDateTime(s.decided_at)}</span>
                </div>
                {s.decision_note && <p className="mt-1.5 text-[12.5px] text-fg-2">“{s.decision_note}”</p>}
              </li>
            ))}
          </ul>
        )
      )}

      {picked.size > 0 && (
        <div className="fixed inset-x-0 bottom-4 z-40 flex justify-center px-4">
          <div className="anim-fade-up flex items-center gap-3 rounded-full bg-panel-3 py-1.5 pl-4 pr-1.5 shadow-panel shadow-[0_0_0_1px_var(--line-2)]">
            <span className="tnum text-[13px]">{picked.size} selected</span>
            <Button size="sm" variant="ghost" onClick={() => setPicked(new Set())}>Clear</Button>
            <Button size="sm" variant="outline" loading={bulk.isPending && bulk.variables === "reject"} onClick={() => bulk.mutate("reject")}>Reject</Button>
            <Button size="sm" variant="primary" className="!rounded-full" loading={bulk.isPending && bulk.variables === "approve"} onClick={() => bulk.mutate("approve")}><Check className="h-3.5 w-3.5" /> Approve</Button>
          </div>
        </div>
      )}
    </div>
  );
}

function SuggestionCard({ s, gid, open, onToggle, picked, onPick }: { s: Suggestion; gid: string; open: boolean; onToggle: () => void; picked: boolean; onPick?: () => void }) {
  const p = s.payload as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  const sources = new Set(s.claims.flatMap((c) => c.evidence.filter((e) => e.stance === "supports").map((e) => e.source.id))).size;
  const head = s.claims[0];
  const graphHref = s.relationship_id ? `/g/${gid}?rel=${s.relationship_id}` : s.entity_id ? `/g/${gid}?entity=${s.entity_id}` : null;
  return (
    <li id={`sug-${s.id}`} className={cn("rounded-xl bg-panel transition-shadow", open ? "shadow-[0_0_0_1.5px_var(--accent-text)]" : "shadow-[0_0_0_1px_var(--line)] hover:shadow-[0_0_0_1px_var(--line-2)]")}>
      <div className="flex items-center gap-2.5 px-3 py-2.5">
        {onPick
          ? <input type="checkbox" checked={picked} onChange={onPick} aria-label={`Select: ${s.title}`} title={s.title} className="h-4 w-4 shrink-0 accent-[var(--accent)]" />
          : <span className="h-4 w-4 shrink-0" title="Needs an individual decision" />}
        <button onClick={onToggle} aria-expanded={open} aria-label={prettyTitle(s.title)} className="flex min-w-0 flex-1 items-center gap-3 text-left">
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[14px]"><SuggestionTitle title={s.title} /></span>
            <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
              <Confidence level={s.confidence} sources={head?.independent_sources} />
              <span className="text-[11px] text-fg-3">{plural(sources, "source")}</span>
              {s.risk === "elevated" && <span className="text-[11px] text-warn">check</span>}
              {p.inferred && <span className="text-[11px] text-warn">inferred</span>}
            </span>
          </span>
          <ChevronDown className={cn("h-4 w-4 shrink-0 text-fg-3 transition-transform", open && "rotate-180")} />
        </button>
      </div>
      {open && (
        <div className="border-t border-line px-3 pb-3 pt-3 anim-fade-up sm:pl-[42px]">
          {s.rationale && (s.kind === "contradiction" || s.payload.change === "supersede") && <p className="mb-3 text-[13px] leading-relaxed text-fg-2">{s.rationale}</p>}

          {p.before && p.after && (
            <div className="mb-3 grid gap-2 sm:grid-cols-2">
              <div className="rounded-lg bg-bg-2 p-3 shadow-[0_0_0_1px_var(--line)]">
                <p className="label">Current</p>
                <p className="mt-1 text-[14px]">{String(p.before.value)}</p>
                {p.before.valid_from && <p className="tnum text-[11.5px] text-fg-3">since {fmtValid(p.before.valid_from)}</p>}
              </div>
              <div className="rounded-lg bg-accent-soft p-3">
                <p className="label !text-accent-text">{s.kind === "contradiction" ? "Other source" : "New"}</p>
                <p className="mt-1 text-[14px]">{String(p.after.value)}</p>
                {p.after.valid_from && <p className="tnum text-[11.5px] text-fg-3">from {fmtValid(p.after.valid_from)}</p>}
              </div>
            </div>
          )}
          {s.kind === "merge" && (
            <div className="mb-3 grid gap-2 sm:grid-cols-2">
              {[p.duplicate, p.target].map((e: { id: string; name: string; key: string }, i: number) => (
                <Link key={e.id} href={`/g/${gid}?entity=${e.id}`} className="rounded-lg bg-bg-2 p-3 shadow-[0_0_0_1px_var(--line)] hover:shadow-[0_0_0_1px_var(--line-2)]">
                  <p className="label">{i === 0 ? "New" : "Existing"}</p>
                  <p className="mt-1 text-[14px]">{e.name}</p>
                  <p className="mt-0.5 break-all text-[11.5px] text-fg-3">{e.key.replace(/^[a-z]+:(name|domain|url):/, "")}</p>
                </Link>
              ))}
            </div>
          )}

          {s.claims.length > 0 && (
            <div className="-mx-2 space-y-1">
              {s.claims.map((c, i) => (
                <ClaimBlock key={c.id} c={c} defaultOpen={i === 0}
                  label={c.predicate === "exists" ? "Exists" : s.kind === "relationship" ? "Link" : predicateLabel(c.predicate)} />
              ))}
            </div>
          )}
          {s.target_claim && s.kind === "contradiction" && (
            <div className="-mx-2 mt-1"><ClaimBlock c={s.target_claim} label="Current value" /></div>
          )}
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
            <SuggestionActions s={s} gid={gid} size="md" />
            <span className="flex items-center gap-3">
              {s.claims.some((c) => c.status === "contested") && <StatusBadge status="contested" />}
              {graphHref && <Link href={graphHref} className="inline-flex items-center gap-1 text-[12.5px] text-fg-2 hover:text-fg">Graph <ArrowRight className="h-3 w-3" /></Link>}
            </span>
          </div>
        </div>
      )}
    </li>
  );
}
