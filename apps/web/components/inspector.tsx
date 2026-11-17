"use client";

import { useMutation } from "@tanstack/react-query";
import { ArrowRight, ClipboardCopy, Eye, EyeOff, Telescope, History, Network, Radar, Search, TriangleAlert, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { del, post, type EntityDetail, type Graph, type RelationshipDetail, type Suggestion } from "@/lib/api";
import { entityBrief } from "@/lib/brief";
import { typeColor } from "@/lib/colors";
import { fmtDate, humanize, timeAgo, timeUntil, validRange } from "@/lib/format";
import { useEntity, useInvalidateGraph, useRelationship } from "@/lib/hooks";
import { ClaimBlock, predicateLabel } from "./evidence";
import { SuggestionActions, SuggestionTitle } from "./suggestion-actions";
import { toast, toastError } from "./toast";
import { Badge, Button, cn, ErrorNote, Skeleton, StatusBadge, Tip } from "./ui";

export type Selection = { kind: "entity" | "relationship"; id: string } | null;

type Props = {
  graph: Graph;
  selection: Selection;
  onSelect: (s: Selection) => void;
  onExpand?: (entityId: string) => void;
  onInvestigate?: (focus: string) => void;
};

export function Inspector(props: Props) {
  const { selection, onSelect } = props;
  if (!selection) return null;
  return (
    <aside aria-label="Evidence inspector" className="relative flex h-full w-full flex-col bg-panel">
      <Button variant="ghost" size="icon" aria-label="Close inspector" className="absolute right-2.5 top-2.5 z-10" onClick={() => onSelect(null)}><X className="h-4 w-4" /></Button>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {selection.kind === "entity" ? <EntitySheet key={selection.id} {...props} id={selection.id} /> : <RelationshipSheet key={selection.id} {...props} id={selection.id} />}
      </div>
    </aside>
  );
}

function Section({ title, count, children, icon }: { title: string; count?: number; children: React.ReactNode; icon?: React.ReactNode }) {
  return (
    <section className="border-t border-line px-4 py-4">
      <h3 className="label mb-2 flex items-center gap-1.5">{icon}{title}{count !== undefined && <span className="tnum opacity-70">{count}</span>}</h3>
      {children}
    </section>
  );
}

function TypeChip({ type, graph }: { type: string; graph: Graph }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[12px] font-medium text-fg-2">
      <span className="h-2 w-2 rounded-full" style={{ background: typeColor(type, graph.ontology) }} />{type}
    </span>
  );
}

function PendingList({ items, gid }: { items: Suggestion[]; gid: string }) {
  if (!items.length) return null;
  return (
    <Section title="To review" count={items.length}>
      <ul className="space-y-2">
        {items.map((s) => (
          <li key={s.id} className="rounded-lg bg-accent-soft p-3">
            <p className="text-[13px] font-medium"><SuggestionTitle title={s.title} /></p>
            <div className="mt-2.5"><SuggestionActions s={s} gid={gid} /></div>
          </li>
        ))}
      </ul>
    </Section>
  );
}

function EntitySheet({ graph, id, onSelect, onExpand, onInvestigate }: Props & { id: string }) {
  const { data: e, isLoading, error, refetch } = useEntity(graph.id, id);
  const invalidate = useInvalidateGraph(graph.id);
  const watch = useMutation({
    mutationFn: async (ent: EntityDetail) => {
      const existing = ent.monitors[0];
      if (existing) await del(`/monitors/${existing.id}`);
      else await post(`/graphs/${graph.id}/monitors`, { entity_id: ent.id, question: "Material changes" });
      return !existing;
    },
    onSuccess: (on) => { toast(on ? "Watching" : "Stopped watching"); invalidate(); },
    onError: toastError,
  });
  if (isLoading) return <SheetSkeleton />;
  if (error || !e) return <div className="p-4"><ErrorNote error={error} retry={() => refetch()} /></div>;

  const contested = e.attributes.some((a) => a.current?.status === "contested");
  const groups = new Map<string, typeof e.neighbors>();
  for (const n of e.neighbors) {
    const key = `${n.direction === "out" ? "" : "← "}${humanize(n.type)}`;
    groups.set(key, [...(groups.get(key) ?? []), n]);
  }
  const ownSuggestion = e.pending_suggestions.filter((s) => s.kind !== "entity" || s.entity_id === e.id);
  return (
    <div className="anim-fade-up">
      <header className="px-4 pb-4 pt-4">
        <TypeChip type={e.type} graph={graph} />
        <h2 className="mt-1.5 pr-8 font-display text-[28px] leading-[1.1]">{e.name}</h2>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <StatusBadge status={e.status} />
          {contested && <Badge tone="warn"><TriangleAlert className="h-3 w-3" /> Contested</Badge>}
          {e.monitors.length > 0 && <Badge tone="info"><Eye className="h-3 w-3" /> Watched</Badge>}
          {graph.is_synthetic && <Badge tone="muted" dashed>Synthetic</Badge>}
        </div>
        {e.description && <p className="mt-3 text-[13.5px] leading-relaxed text-fg-2">{e.description}</p>}
        {e.aliases.length > 0 && <p className="mt-2 text-[12px] text-fg-3">aka {e.aliases.join(", ")}</p>}
        <div className="mt-3.5 flex flex-wrap gap-1.5">
          {e.status === "accepted" && (
            <Button size="sm" variant="outline" loading={watch.isPending} onClick={() => watch.mutate(e)}>
              {e.monitors.length ? <><EyeOff className="h-3.5 w-3.5" /> Unwatch</> : <><Eye className="h-3.5 w-3.5" /> Watch</>}
            </Button>
          )}
          {onExpand && <Button size="sm" variant="outline" onClick={() => onExpand(e.id)}><Network className="h-3.5 w-3.5" /> Expand</Button>}
          {onInvestigate && (
            <Tip label="Run focused research on this entity">
              <Button size="sm" variant="outline" onClick={() => onInvestigate(`Deepen ${e.name} (${e.type}): fill in what is still unknown${e.unknowns.length ? ` (${e.unknowns.map(humanize).join(", ")})` : ""}, add its key people, investors and partners, and find an independent second source for every fact that currently has only one.`)}>
                <Telescope className="h-3.5 w-3.5" /> Research
              </Button>
            </Tip>
          )}
          <Tip label="Cited Markdown brief">
            <Button size="sm" variant="outline" onClick={async () => { await navigator.clipboard.writeText(entityBrief(e, graph.name, graph.is_synthetic)); toast("Brief copied"); }}>
              <ClipboardCopy className="h-3.5 w-3.5" /> Copy brief
            </Button>
          </Tip>
        </div>
      </header>

      <PendingList items={ownSuggestion} gid={graph.id} />

      {e.existence && (
        <Section title="Evidence">
          <div className="-mx-2"><ClaimBlock c={e.existence} defaultOpen={e.status === "proposed"} /></div>
        </Section>
      )}

      <Section title="Attributes" count={e.attributes.length}>
        {e.attributes.length === 0 && <p className="text-[13px] text-fg-3">None yet</p>}
        <div className="-mx-2 space-y-1">
          {e.attributes.map((a) => (
            <div key={a.predicate}>
              {a.current && <ClaimBlock c={a.current} label={predicateLabel(a.predicate)} />}
              {a.history.length > 0 && <HistoryList claims={a.history} />}
            </div>
          ))}
        </div>
      </Section>

      <Section title="Connections" count={e.neighbors.length}>
        {e.neighbors.length === 0 && <p className="text-[13px] text-fg-3">None yet</p>}
        <div className="space-y-3">
          {[...groups.entries()].map(([label, items]) => (
            <div key={label}>
              <p className="mb-0.5 text-[11.5px] text-fg-3">{label}</p>
              <ul>
                {items.map((n) => (
                  <li key={n.relationship_id} className="group flex items-center gap-1">
                    <button onClick={() => onSelect({ kind: "entity", id: n.entity.id })} className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-1.5 py-1.5 text-left hover:bg-panel-2">
                      <span className={cn("h-2 w-2 shrink-0 rounded-full", n.entity.status === "proposed" && "!bg-transparent ring-1 ring-current")} style={{ background: typeColor(n.entity.type, graph.ontology), color: typeColor(n.entity.type, graph.ontology) }} />
                      <span className={cn("truncate text-[13px]", n.status === "proposed" && "text-fg-2")}>{n.entity.name}</span>
                      {validRange(n.valid_from, n.valid_to) && <span className="tnum shrink-0 text-[11.5px] text-fg-3">{validRange(n.valid_from, n.valid_to)}</span>}
                    </button>
                    <Tip label="Evidence">
                      <button aria-label={`Inspect relationship with ${n.entity.name}`} onClick={() => onSelect({ kind: "relationship", id: n.relationship_id })}
                        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-fg-3 hover:bg-panel-2 hover:text-accent-text">
                        <Search className="h-3.5 w-3.5" />
                      </button>
                    </Tip>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </Section>

      {e.unknowns.length > 0 && (
        <Section title="Unknown" icon={<Radar className="h-3 w-3" />}>
          <div className="flex flex-wrap gap-1.5">
            {e.unknowns.map((u) => (
              <button key={u} onClick={() => onInvestigate?.(`Find the ${humanize(u)} of ${e.name}, with dated evidence.`)}
                className="inline-flex h-6 items-center gap-1 rounded-md border border-dashed border-line-2 px-2 text-[12px] text-fg-2 hover:border-accent-text hover:text-accent-text">
                {humanize(u)} <ArrowRight className="h-3 w-3" />
              </button>
            ))}
          </div>
        </Section>
      )}

      {e.monitors.map((m) => (
        <Section key={m.id} title="Monitor" icon={<Eye className="h-3 w-3" />}>
          <p className="flex items-center justify-between gap-2 text-[12.5px] text-fg-2">
            <span>Checked {timeAgo(m.last_checked_at)} · next {timeUntil(m.next_run_at)}</span>
            <Link href={`/g/${graph.id}/changes`} className="inline-flex items-center gap-1 text-accent-text hover:underline">Changes <ArrowRight className="h-3 w-3" /></Link>
          </p>
        </Section>
      ))}

      <section className="flex items-center justify-between gap-2 border-t border-line px-4 py-3 text-[12px] text-fg-3" title={e.discovered_via || undefined}>
        <span className="tnum">First seen {fmtDate(e.first_seen_at)}</span>
        {e.discovered_run_id && <Link href={`/g/${graph.id}/research?run=${e.discovered_run_id}`} className="inline-flex items-center gap-1 text-fg-2 hover:text-fg">Run <ArrowRight className="h-3 w-3" /></Link>}
      </section>
    </div>
  );
}

function HistoryList({ claims }: { claims: EntityDetail["attributes"][number]["history"] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="ml-2 border-l border-line pl-2">
      <button onClick={() => setOpen(!open)} aria-expanded={open} className="flex items-center gap-1.5 px-2 py-1 text-[11.5px] text-fg-3 hover:text-fg">
        <History className="h-3 w-3" /> {claims.length} other value{claims.length === 1 ? "" : "s"}
      </button>
      {open && claims.map((c) => <ClaimBlock key={c.id} c={c} muted={["superseded", "retracted"].includes(c.status)} />)}
    </div>
  );
}

function EndCard({ n, graph, onSelect }: { n: RelationshipDetail["source"]; graph: Graph; onSelect: (s: Selection) => void }) {
  return (
    <button onClick={() => onSelect({ kind: "entity", id: n.id })} className="group flex w-full items-center gap-2.5 rounded-lg bg-bg-2 p-2.5 text-left shadow-[0_0_0_1px_var(--line)] hover:shadow-[0_0_0_1px_var(--line-2)]">
      <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: typeColor(n.type, graph.ontology) }} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[14px] font-medium group-hover:text-accent-text">{n.name}</span>
        <span className="text-[11.5px] text-fg-3">{n.type}</span>
      </span>
      <StatusBadge status={n.status} />
    </button>
  );
}

function RelationshipSheet({ graph, id, onSelect }: Props & { id: string }) {
  const { data: r, isLoading, error, refetch } = useRelationship(graph.id, id);
  if (isLoading) return <SheetSkeleton />;
  if (error || !r) return <div className="p-4"><ErrorNote error={error} retry={() => refetch()} /></div>;
  const inferred = r.claims.some((c) => c.is_inferred);
  return (
    <div className="anim-fade-up">
      <header className="px-4 pb-4 pt-4">
        <EndCard n={r.source} graph={graph} onSelect={onSelect} />
        <div className="my-1.5 flex items-center gap-2 pl-3.5">
          <span className="h-7 w-px bg-line-2" />
          <span className="rounded-md bg-panel-2 px-2 py-1 text-[12px] font-medium text-fg">{humanize(r.type)}</span>
          {validRange(r.valid_from, r.valid_to) && <span className="tnum text-[11.5px] text-fg-3">{validRange(r.valid_from, r.valid_to)}</span>}
        </div>
        <EndCard n={r.target} graph={graph} onSelect={onSelect} />
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <StatusBadge status={r.status} />
          {inferred && <Badge tone="warn">Inferred</Badge>}
          {r.valid_to && <Badge tone="neutral">Ended {r.valid_to.slice(0, 4)}</Badge>}
        </div>
      </header>
      <PendingList items={r.pending_suggestions} gid={graph.id} />
      <Section title="Evidence" count={r.claims.reduce((n, c) => n + c.evidence.length, 0)}>
        <div className="-mx-2 space-y-1">
          {r.claims.map((c) => <ClaimBlock key={c.id} c={{ ...c, value: `${r.source.name} ${humanize(r.type)} ${r.target.name}` }} defaultOpen />)}
        </div>
      </Section>
      <section className="flex items-center justify-between gap-2 border-t border-line px-4 py-3 text-[12px] text-fg-3">
        <span className="tnum">First seen {fmtDate(r.first_seen_at)}</span>
        {r.discovered_run_id && <Link href={`/g/${graph.id}/research?run=${r.discovered_run_id}`} className="inline-flex items-center gap-1 text-fg-2 hover:text-fg">Run <ArrowRight className="h-3 w-3" /></Link>}
      </section>
    </div>
  );
}

function SheetSkeleton() {
  return (
    <div className="space-y-3 p-4" role="status" aria-label="Loading">
      <Skeleton className="h-3 w-20" /><Skeleton className="h-8 w-3/4" /><Skeleton className="h-4 w-1/2" />
      <Skeleton className="mt-6 h-20 w-full" /><Skeleton className="h-20 w-full" /><Skeleton className="h-20 w-full" />
    </div>
  );
}
