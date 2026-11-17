"use client";

import {
  BookOpenText, CircleDot, Compass, FileSearch, Flag, GitMerge, Lightbulb, ListChecks, MessageSquare, Network,
  RefreshCw, Route, Search, ShieldCheck, Sparkles, TriangleAlert, Waypoints,
} from "lucide-react";
import type { RunEvent } from "@/lib/api";
import { RUN_STATUS } from "@/lib/colors";
import { firstLine, fmtClock, fmtValid, humanize } from "@/lib/format";
import { Favicon } from "../favicon";
import { SuggestionTitle } from "../suggestion-actions";
import { Badge, cn } from "../ui";

export const HIDDEN_EVENTS = new Set(["focus"]);

const AGENTS: Record<string, { label: string; cls: string }> = {
  coordinator: { label: "Coordinator", cls: "text-accent-text" },
  scout: { label: "Scout", cls: "text-info" },
  investigator: { label: "Investigator", cls: "text-[var(--c-violet)]" },
  verifier: { label: "Verifier", cls: "text-warn" },
};

/** Models write summaries with light Markdown. Render bold and drop heading marks; nothing else. */
function Prose({ text }: { text: string }) {
  const parts = String(text ?? "").replace(/^#{1,4}\s+/gm, "").split(/\*\*(.+?)\*\*/g);
  return <span className="whitespace-pre-line">{parts.map((p, i) => (i % 2 ? <strong key={i} className="font-medium text-fg">{p}</strong> : p))}</span>;
}

export function AgentTag({ agent }: { agent?: string }) {
  if (!agent) return null;
  const a = AGENTS[agent] ?? { label: agent, cls: "text-fg-2" };
  return <span className={cn("text-[11px] font-medium", a.cls)}>{a.label}</span>;
}

const OUTCOME: Record<string, { tone: "ok" | "warn" | "neutral" | "danger" | "info"; label: string }> = {
  corroborated: { tone: "ok", label: "Corroborated" },
  single_source: { tone: "neutral", label: "Single source" },
  syndicated: { tone: "warn", label: "Syndicated copy" },
  conflict: { tone: "warn", label: "Conflict" },
  hypothesis: { tone: "info", label: "Hypothesis" },
  out_of_scope: { tone: "neutral", label: "Possibly out of scope" },
  possible_duplicate: { tone: "warn", label: "Possible duplicate" },
};

type View = { icon: React.ReactNode; title: React.ReactNode; body?: React.ReactNode; tone?: "accent" | "warn" | "danger" | "muted"; entityId?: string; relationshipId?: string };

export function describeEvent(ev: RunEvent): View {
  const p = ev.payload;
  const i = "h-3.5 w-3.5";
  switch (ev.type) {
    case "run.status": {
      const meta = RUN_STATUS[p.status] ?? { label: p.status };
      const extra = p.reason || p.error || (p.pending_suggestions ? `${p.pending_suggestions} to review` : "");
      return { icon: <CircleDot className={i} />, title: meta.label.replace("…", ""), body: extra || undefined,
        tone: p.status === "failed" ? "danger" : ["paused", "pausing", "budget_exhausted", "cancel_requested"].includes(p.status) ? "warn" : "muted" };
    }
    case "plan.created":
      return { icon: <Route className={i} />, title: "Research plan", tone: "accent", body: (
        <div>
          <p className="text-fg-2">{p.objective}</p>
          <ol className="mt-2 space-y-1.5">
            {(p.slices ?? []).map((s: { title: string; why: string; est_calls: number }, n: number) => (
              <li key={n} className="flex gap-2.5"><span className="tnum w-3 shrink-0 text-right text-fg-3">{n + 1}</span>
                <span className="text-fg" title={s.why}>{s.title}</span></li>
            ))}
          </ol>
          {p.note && <p className="mt-2 text-fg-3">{p.note}</p>}
        </div>) };
    case "slice.started":
      return { icon: <Compass className={i} />, title: <span className="text-fg" title={p.title}>{firstLine(p.title)}</span>, tone: "accent" };
    case "search.performed":
      return { icon: <Search className={i} />, title: <><span className="text-fg-3">Searched</span> {p.query}</>, body: (
        <ul className="space-y-0.5">
          {(p.results ?? []).slice(0, 6).map((r: { title: string; domain: string; published_at?: string }, n: number) => (
            <li key={n} className="flex items-center gap-2"><Favicon domain={r.domain} size={13} /><span className="truncate text-fg-3">{r.title}</span><span className="shrink-0 text-[11px] text-fg-3 opacity-70">{r.domain}{r.published_at ? ` · ${fmtValid(r.published_at)}` : ""}</span></li>
          ))}
          {(p.results ?? []).length === 0 && <li className="text-fg-3">No results.</li>}
        </ul>) };
    case "source.read":
      return { icon: <BookOpenText className={i} />, title: <><span className="text-fg-3">Read</span> {p.sources?.length ?? 0} source{p.sources?.length === 1 ? "" : "s"}</>, body: (
        <div className="flex flex-wrap gap-1">{(p.sources ?? []).map((s: { id: string; title: string; domain: string }) => (
          <span key={s.id} className="inline-flex max-w-full items-center gap-1.5 truncate rounded-md bg-panel-2 px-1.5 py-0.5 text-[11px] text-fg-2" title={s.title}><Favicon domain={s.domain} size={12} />{s.domain}</span>))}</div>) };
    case "site.map": case "site.crawl":
      return { icon: <FileSearch className={i} />, title: `${ev.type === "site.map" ? "Mapped" : "Crawled"} ${p.root}`, body: `${p.pages} page${p.pages === 1 ? "" : "s"}` };
    case "candidate.entity":
      return { icon: <Sparkles className={i} />, entityId: p.entity?.id, title: <><span className="text-fg-3">{p.entity?.type}</span> <strong className="font-medium text-fg">{p.entity?.name}</strong></> };
    case "candidate.relationship":
      return { icon: <Network className={i} />, relationshipId: p.relationship?.id, title: <span className="text-fg"><SuggestionTitle title={p.title ?? ""} /></span> };
    case "candidate.claim":
      return { icon: p.change === "contradicted" ? <TriangleAlert className={i} /> : <Waypoints className={i} />, entityId: p.entity?.id, tone: p.change === "contradicted" || p.change === "changed" ? "warn" : undefined,
        title: p.change === "contradicted" ? <><span className="text-warn">Conflict</span> <strong className="font-medium text-fg">{p.title?.replace(/^Conflict on /, "")}</strong></> : p.title,
        body: p.before ? <span className="text-fg-3">{String(p.before.value)} → {String(p.after?.value)}</span> : undefined };
    case "candidate.merge":
      return { icon: <GitMerge className={i} />, title: p.title, tone: "warn" };
    case "suggestion.decided":
      return { icon: <ShieldCheck className={i} />, title: p.by === "policy" ? <><span className="text-accent-text">Approved</span> <strong className="font-medium text-fg"><SuggestionTitle title={p.title ?? "suggestion"} /></strong></> : `Suggestion ${p.status}`, tone: "accent", entityId: p.entity_id, relationshipId: p.relationship_id };
    case "verify.result":
      return { icon: <ListChecks className={i} />, title: "Verification", body: (
        <ul className="space-y-1.5">
          {(p.checks ?? []).map((c: { label: string; outcome: string; detail: string }, n: number) => {
            const o = OUTCOME[c.outcome] ?? { tone: "neutral" as const, label: humanize(c.outcome) };
            return <li key={n}><span className="mr-1.5 text-fg">{c.label}</span><Badge tone={o.tone}>{o.label}</Badge><br /><span className="text-fg-3">{c.detail}</span></li>;
          })}
        </ul>) };
    case "note":
      return { icon: p.kind === "dossier" ? <Flag className={i} /> : <Lightbulb className={i} />, title: p.kind === "decision" ? "Decision" : p.kind === "dossier" ? "Dossier" : p.kind === "dead_end" ? "Dead end" : "Note", body: <Prose text={p.text} /> };
    case "frontier.added":
      return { icon: <Compass className={i} />, title: `${p.questions?.length ?? 0} open question${p.questions?.length === 1 ? "" : "s"}`, body: (
        <ul className="list-disc space-y-0.5 pl-4 text-fg-2">{(p.questions ?? []).map((q: { id: string; question: string }) => <li key={q.id}>{q.question}</li>)}</ul>) };
    case "run.summary":
      return { icon: <Flag className={i} />, title: "Summary", body: <Prose text={p.text} />, tone: "accent" };
    case "run.retry":
      return { icon: <RefreshCw className={i} />, title: `Retrying in ${p.retry_in_seconds}s`, body: p.error, tone: "warn" };
    case "run.resumed":
      return { icon: <RefreshCw className={i} />, title: "Resumed", tone: "warn" };
    case "provider.fallback":
      return { icon: <RefreshCw className={i} />, title: `Model fallback: ${p.from} → ${p.to}`, body: p.reason, tone: "warn" };
    case "steer.received":
      return { icon: <MessageSquare className={i} />, title: <><span className="text-fg-3">Steer</span> <span className="text-fg">{p.instruction}</span></> };
    case "steer.delivered":
      return { icon: <MessageSquare className={i} />, title: "Steering delivered" };
    default:
      return { icon: <CircleDot className={i} />, title: humanize(ev.type.replace(".", " ")) };
  }
}

export function EventRow({ ev, onPick, compact }: { ev: RunEvent; onPick?: (sel: { kind: "entity" | "relationship"; id: string }) => void; compact?: boolean }) {
  const v = describeEvent(ev);
  const pickable = onPick && (v.entityId || v.relationshipId);
  const pick = () => v.entityId ? onPick?.({ kind: "entity", id: v.entityId }) : v.relationshipId ? onPick?.({ kind: "relationship", id: v.relationshipId }) : undefined;
  const tone = v.tone === "accent" ? "text-accent-text" : v.tone === "warn" ? "text-warn" : v.tone === "danger" ? "text-danger" : "text-fg-3";
  const inner = (
    <>
      <span className={cn("mt-[3px] shrink-0", tone)}>{v.icon}</span>
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-baseline gap-x-2">
          <span className={cn("text-[13px] leading-snug text-fg-2", compact && "truncate")}>{v.title}</span>
          <AgentTag agent={ev.payload.agent} />
        </span>
        {!compact && v.body && <span className="mt-1 block text-[12.5px] leading-relaxed text-fg-2">{v.body}</span>}
      </span>
      {!compact && <time className="tnum shrink-0 text-[11px] text-fg-3 opacity-70" dateTime={ev.created_at}>{fmtClock(ev.created_at)}</time>}
    </>
  );
  const cls = cn("flex w-full gap-2.5 text-left", compact ? "px-1 py-1" : "rounded-lg px-2.5 py-2");
  return pickable
    ? <button onClick={pick} className={cn(cls, "hover:bg-panel-2")} title="Show on the graph">{inner}</button>
    : <div className={cls}>{inner}</div>;
}
