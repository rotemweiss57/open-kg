"use client";

import { ArrowUpRight, Eye, Inbox, Plus, Search, Sparkles } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { MiniGraph } from "@/components/mini-graph";
import { RunPill } from "@/components/run-pill";
import { Button, Card, Empty, ErrorNote, Input, Skeleton, SyntheticTag, Textarea } from "@/components/ui";
import type { Graph } from "@/lib/api";
import { EXAMPLES, exampleHref } from "@/lib/examples";
import { plural, timeAgo, usd } from "@/lib/format";
import { useGraphs, useMe, useSystem } from "@/lib/hooks";

export default function LibraryPage() {
  const graphs = useGraphs();
  const system = useSystem();
  const me = useMe();
  const router = useRouter();
  const [prompt, setPrompt] = useState("");
  const [find, setFind] = useState("");
  const shown = (graphs.data ?? []).filter((g) => !find.trim() || g.name.toLowerCase().includes(find.trim().toLowerCase()));
  const start = (text: string, scenario?: string) => {
    const qs = new URLSearchParams();
    if (text.trim()) qs.set("prompt", text.trim());
    if (scenario) qs.set("scenario", scenario);
    router.push(`/new?${qs}`);
  };

  return (
    <div className="mx-auto max-w-[1240px] px-5 pb-20 sm:px-8">
      <header className="grid-bg -mx-5 border-b border-line px-5 pb-10 pt-12 sm:-mx-8 sm:px-8 sm:pt-16">
        <p className="label">{me.data?.workspace_name}</p>
        <h1 className="mt-2 font-display text-[40px] leading-[1.05] sm:text-[54px]">What do you want to <em className="text-accent-text">map</em>?</h1>
        <form className="mt-7 max-w-3xl" onSubmit={(e) => { e.preventDefault(); start(prompt); }}>
          <div className="rounded-2xl bg-panel p-2 shadow-panel shadow-[0_0_0_1px_var(--line-2)] transition-shadow focus-within:shadow-[0_0_0_1.5px_var(--accent-text)]">
            <Textarea
              aria-label="Describe what you want to map" rows={2} value={prompt} onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) start(prompt); }}
              placeholder="Product leaders at Series B startups in the US, with their companies and investors"
              className="!bg-transparent !shadow-none text-[15px]" />
            <div className="flex items-center justify-between gap-3 pb-1 pl-2 pr-1">
              <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                {system.data?.live_research_available
                  ? EXAMPLES.map((x) => (
                    <button key={x.id} type="button" title={x.prompt} onClick={() => router.push(exampleHref(x.prompt))}
                      className="inline-flex h-7 items-center gap-1.5 rounded-full px-2.5 text-[12.5px] text-fg-3 transition-colors hover:bg-panel-2 hover:text-fg">
                      <Sparkles className="h-3 w-3 text-accent-text" /> {x.label}
                    </button>))
                  : system.data?.scenarios.map((s) => (
                    <button key={s.id} type="button" onClick={() => start(s.prompt, s.id)}
                      className="inline-flex h-7 items-center gap-1.5 rounded-full px-2.5 text-[12.5px] text-fg-3 transition-colors hover:bg-panel-2 hover:text-fg">
                      <Sparkles className="h-3 w-3 text-accent-text" /> {s.name}
                    </button>))}
              </div>
              <Button type="submit" variant="primary" disabled={prompt.trim().length < 3}>Continue <ArrowUpRight className="h-4 w-4" /></Button>
            </div>
          </div>
        </form>
      </header>

      <section className="mt-9" aria-labelledby="graphs-h">
        <div className="mb-4 flex items-center justify-between">
          <h2 id="graphs-h" className="font-display text-[26px]">Graphs</h2>
          <div className="flex items-center gap-2">
            {(graphs.data?.length ?? 0) > 6 && (
              <div className="relative w-[min(220px,44vw)]">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-fg-3" />
                <Input value={find} onChange={(e) => setFind(e.target.value)} placeholder="Filter" aria-label="Filter graphs" className="!h-8 pl-8" />
              </div>
            )}
            <Button variant="outline" size="sm" onClick={() => router.push("/new")}><Plus className="h-3.5 w-3.5" /> New</Button>
          </div>
        </div>
        {graphs.isError && <ErrorNote error={graphs.error} retry={() => graphs.refetch()} />}
        {graphs.isLoading && <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-[230px] !rounded-xl" />)}</div>}
        {graphs.data?.length === 0 && (
          <Card><Empty icon={<Plus className="h-5 w-5" />} title="No graphs yet"
            action={<Button variant="primary" onClick={() => router.push("/new")}>New graph</Button>} /></Card>
        )}
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {shown.map((g) => <GraphCard key={g.id} g={g} />)}
        </div>
        {graphs.data && graphs.data.length > 0 && shown.length === 0 && <p className="py-10 text-center text-[13px] text-fg-3">No matches</p>}
        <div>
        </div>
      </section>
    </div>
  );
}

// A finished run needs no badge: the card already says what is waiting for review.
const QUIET = new Set(["awaiting_review", "completed", "cancelled"]);

function GraphCard({ g }: { g: Graph }) {
  const s = g.stats;
  const entities = s.entities + s.proposed_entities;
  return (
    <Card className="group relative flex flex-col overflow-hidden transition-[box-shadow,transform] duration-200 hover:-translate-y-0.5 hover:shadow-[0_0_0_1px_var(--line-2),0_18px_40px_-24px_rgba(0,0,0,0.6)]">
      <Link href={`/g/${g.id}`} aria-label={`Open ${g.name}`} className="absolute inset-0 z-10 rounded-xl" />
      <div className="grid-bg relative h-[128px] border-b border-line bg-bg-2">
        <MiniGraph graphId={g.id} ontology={g.ontology} className="h-full w-full p-3" />
        <div className="absolute left-3 top-3 flex items-center gap-1.5">
          {g.latest_run && !QUIET.has(g.latest_run.status) && <RunPill status={g.latest_run.status} />}
          {g.is_synthetic && <SyntheticTag />}
        </div>
      </div>
      <div className="flex flex-1 flex-col p-4">
        <h3 className="line-clamp-2 font-display text-[19px] leading-snug">{g.name}</h3>
        <p className="tnum mt-auto flex flex-wrap items-center gap-x-2 pt-3 text-[12.5px] text-fg-3">
          <span title={`${s.entities} verified, ${s.proposed_entities} provisional`}><b className="font-medium text-fg-2">{entities}</b> entities</span>
          <span aria-hidden>·</span>
          <span><b className="font-medium text-fg-2">{s.sources}</b> sources</span>
          {!g.is_synthetic && <><span aria-hidden>·</span><span title={`of ${usd(g.budget_usd)}`}>{usd(s.spent_usd)}</span></>}
          <span className="ml-auto">{g.last_researched_at ? timeAgo(g.last_researched_at) : "New"}</span>
        </p>
        {(s.pending_suggestions > 0 || s.active_monitors > 0) && (
          <div className="relative z-20 mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line pt-2.5 text-[12.5px]">
            {s.pending_suggestions > 0 && (
              <Link href={`/g/${g.id}/review`} className="inline-flex items-center gap-1.5 text-accent-text hover:underline"><Inbox className="h-3.5 w-3.5" />{s.pending_suggestions} to review</Link>
            )}
            {s.active_monitors > 0 && (
              <Link href={`/g/${g.id}/changes`} className="inline-flex items-center gap-1.5 text-fg-3 hover:text-fg"><Eye className="h-3.5 w-3.5" />{s.active_monitors} watching{s.unread_changes > 0 && <span className="text-warn"> · {plural(s.unread_changes, "change")}</span>}</Link>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}
