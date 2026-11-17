"use client";

import { ChevronLeft, Play } from "lucide-react";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { GraphContext } from "@/components/graph-context";
import { RunPill } from "@/components/run-pill";
import { StartRunDialog } from "@/components/run/start-run";
import { Button, cn, Empty, ErrorNote, Skeleton, SyntheticTag } from "@/components/ui";
import { RUN_ACTIVE } from "@/lib/colors";
import { useGraph, useRuns } from "@/lib/hooks";

export default function GraphLayout({ children }: { children: React.ReactNode }) {
  const { id } = useParams<{ id: string }>();
  const path = usePathname();
  const graph = useGraph(id);
  const runs = useRuns(id);
  const [dialog, setDialog] = useState<{ open: boolean; focus?: string; frontierId?: string }>({ open: false });

  const ctx = useMemo(() => {
    if (!graph.data) return null;
    const research = (runs.data ?? []).filter((r) => r.kind === "research");
    return {
      graph: graph.data, runs: research, latestRun: research[0] ?? null,
      activeRun: research.find((r) => RUN_ACTIVE.includes(r.status) || r.status === "paused") ?? null,
      startRun: (focus?: string, frontierId?: string) => setDialog({ open: true, focus, frontierId }),
    };
  }, [graph.data, runs.data]);

  // A one-click example lands here with ?start=1: open the confirmation once, then clean the URL.
  useEffect(() => {
    if (!graph.data || typeof window === "undefined") return;
    const url = new URL(window.location.href);
    if (url.searchParams.get("start") !== "1") return;
    url.searchParams.delete("start");
    window.history.replaceState(null, "", url.pathname + url.search);
    queueMicrotask(() => setDialog({ open: true }));
  }, [graph.data]);
  useEffect(() => { if (graph.data?.name) document.title = `${graph.data.name} · OpenKG`; return () => { document.title = "OpenKG"; }; }, [graph.data?.name]);

  if (graph.isError) {
    return <div className="p-8"><Empty title="Graph not found" action={<Link href="/"><Button variant="primary">Library</Button></Link>} /><div className="mx-auto mt-2 max-w-md"><ErrorNote error={graph.error} retry={() => graph.refetch()} /></div></div>;
  }
  if (!ctx) return <div className="space-y-4 p-6"><Skeleton className="h-8 w-72" /><Skeleton className="h-[60dvh] w-full !rounded-xl" /></div>;

  const g = ctx.graph;
  const base = `/g/${id}`;
  const tabs = [
    { href: base, label: "Graph", active: path === base },
    { href: `${base}/table`, label: "Table", active: path.startsWith(`${base}/table`) },
    { href: `${base}/review`, label: "Review", count: g.stats.pending_suggestions, active: path.startsWith(`${base}/review`) },
    { href: `${base}/research`, label: "Research", live: !!ctx.activeRun && ctx.activeRun.status !== "paused", active: path.startsWith(`${base}/research`) },
    { href: `${base}/changes`, label: "Changes", count: g.stats.unread_changes, warn: true, active: path.startsWith(`${base}/changes`) },
    { href: `${base}/charter`, label: "Charter", active: path.startsWith(`${base}/charter`) },
  ];
  return (
    <GraphContext.Provider value={ctx}>
      <div className="flex h-full min-h-0 flex-col">
        <header className="shrink-0 border-b border-line bg-bg-2">
          <div className="flex items-center gap-3 px-3 pt-2.5 sm:px-4">
            <Link href="/" aria-label="Back to library" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-fg-3 hover:bg-panel-2 hover:text-fg"><ChevronLeft className="h-4 w-4" /></Link>
            <h1 className="min-w-0 truncate font-display text-[24px] leading-none sm:text-[27px]">{g.name}</h1>
            {g.is_synthetic && <SyntheticTag className="hidden sm:inline-flex" />}
            <div className="flex-1" />
            {ctx.activeRun
              ? <Link href={`${base}/research`} aria-label="Open the active run"><RunPill status={ctx.activeRun.status} /></Link>
              : <Button variant="primary" size="sm" onClick={() => ctx.startRun()}><Play className="h-3.5 w-3.5" /> <span className="hidden sm:inline">Start research</span><span className="sm:hidden">Run</span></Button>}
          </div>
          <nav aria-label="Graph sections" className="-mb-px mt-1.5 flex gap-0.5 overflow-x-auto px-3 sm:px-4">
            {tabs.map((t) => (
              <Link key={t.href} href={t.href} aria-current={t.active ? "page" : undefined}
                className={cn("relative flex h-9 shrink-0 items-center gap-1.5 border-b-2 px-2.5 text-[13px] transition-colors", t.active ? "border-accent text-fg" : "border-transparent text-fg-3 hover:text-fg")}>
                {t.label}
                {!!t.count && <span className={cn("tnum rounded-full px-1.5 text-[10.5px] font-medium leading-[16px]", t.warn ? "bg-warn-soft text-warn" : "bg-accent-soft text-accent-text")}>{t.count}</span>}
                {t.live && <span className="relative flex h-1.5 w-1.5"><span className="pulse-ring absolute inset-0 rounded-full bg-accent" /><span className="relative h-1.5 w-1.5 rounded-full bg-accent" /></span>}
              </Link>
            ))}
          </nav>
        </header>
        <div className="min-h-0 flex-1">{children}</div>
      </div>
      <StartRunDialog graph={g} open={dialog.open} onOpenChange={(open) => setDialog((d) => ({ ...d, open }))} focus={dialog.focus} frontierId={dialog.frontierId} />
    </GraphContext.Provider>
  );
}
