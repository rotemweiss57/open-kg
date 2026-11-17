"use client";

import { BadgeCheck, ChevronDown, CircleHelp, ExternalLink, FileText } from "lucide-react";
import { Popover } from "radix-ui";
import { useState } from "react";
import type { Claim, Evidence } from "@/lib/api";
import { fmtDate, fmtValid, fmtValue, humanize, timeAgo, validRange } from "@/lib/format";
import { cn, Confidence, StatusBadge, Tip } from "./ui";

/** Only http(s) links are ever rendered as anchors; anything else (fixture://, javascript:) is inert. */
const safeHref = (url: string) => (/^https?:\/\//i.test(url) ? url : null);

export function SourceCard({ ev, compact }: { ev: Evidence; compact?: boolean }) {
  const s = ev.source;
  const href = safeHref(s.url);
  const body = (
    <>
      <div className="flex items-start gap-2">
        <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0 text-fg-3" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[12.5px] font-medium text-fg group-hover:text-accent-text">{s.title || s.domain}</p>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-[11.5px] text-fg-3" title={`Read ${timeAgo(s.fetched_at)}`}>
            <span>{s.domain}</span>
            {s.published_at && <span>· {fmtValid(s.published_at)}</span>}
          </p>
        </div>
        {href ? <ExternalLink className="h-3.5 w-3.5 shrink-0 text-fg-3 group-hover:text-accent-text" /> : null}
      </div>
      {!compact && ev.excerpt && (
        <blockquote className={cn("mt-2 border-l-2 pl-2.5 text-[12.5px] leading-relaxed text-fg-2", ev.stance === "contradicts" ? "border-warn" : "border-line-2")}>
          “{ev.excerpt}”
        </blockquote>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
        {ev.stance === "contradicts" && <span className="text-warn">Contradicts</span>}
        {ev.excerpt_verified
          ? <span className="inline-flex items-center gap-1 text-ok"><BadgeCheck className="h-3 w-3" /> Verified quote</span>
          : <span className="inline-flex items-center gap-1 text-warn"><CircleHelp className="h-3 w-3" /> Quote not found</span>}
        {s.is_synthetic && <span className="text-fg-3">Synthetic</span>}
      </div>
    </>
  );
  const cls = "group block w-full rounded-lg bg-bg-2 p-2.5 text-left shadow-[0_0_0_1px_var(--line)] transition-shadow hover:shadow-[0_0_0_1px_var(--line-2)]";
  if (href) return <a href={href} target="_blank" rel="noopener noreferrer nofollow" className={cls} aria-label={`Open source: ${s.title || s.domain}`}>{body}</a>;
  return (
    <Popover.Root>
      <Popover.Trigger className={cls} aria-label={`Show source: ${s.title || s.domain}`}>{body}</Popover.Trigger>
      <Popover.Portal>
        <Popover.Content side="left" align="start" sideOffset={8} collisionPadding={12} className="z-[70] w-[min(380px,calc(100vw-24px))] rounded-xl bg-panel-3 p-4 shadow-panel shadow-[0_0_0_1px_var(--line-2)] anim-fade-up">
          <p className="label">Synthetic document</p>
          <p className="mt-1 text-[13px] font-medium">{s.title}</p>
          <p className="mt-0.5 break-all text-[11.5px] text-fg-3">{s.url}</p>
          <p className="mt-3 text-[12.5px] leading-relaxed text-fg-2">{s.snippet}{s.snippet.length >= 400 ? "…" : ""}</p>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

export function ClaimMeta({ c }: { c: Claim }) {
  return (
    <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <StatusBadge status={c.status} />
      <Confidence level={c.confidence} sources={c.independent_sources} />
      {c.is_inferred && <Tip label="Inferred by the agent; no source states it directly."><span className="text-[11px] text-warn">inferred</span></Tip>}
    </span>
  );
}

/** A claim row that opens to reveal its evidence. Evidence is one click away, everywhere. */
export function ClaimBlock({ c, label, defaultOpen, muted }: { c: Claim; label?: string; defaultOpen?: boolean; muted?: boolean }) {
  const [open, setOpen] = useState(!!defaultOpen);
  const supports = c.evidence.filter((e) => e.stance === "supports").length;
  const against = c.evidence.length - supports;
  return (
    <div className={cn("rounded-lg", open && "bg-panel-2/60")}>
      <button onClick={() => setOpen(!open)} aria-expanded={open} className="flex w-full items-start gap-2 rounded-lg px-2 py-2 text-left hover:bg-panel-2">
        <div className="min-w-0 flex-1">
          {label && <p className="label mb-0.5">{label}</p>}
          <p className={cn("text-[13.5px] leading-snug", muted ? "text-fg-2 line-through decoration-line-2" : "text-fg")}>
            {c.value === true ? "Confirmed" : fmtValue(c.value, c.predicate)}
            {validRange(c.valid_from, c.valid_to) && <span className="tnum ml-2 whitespace-nowrap text-[11.5px] text-fg-3 no-underline">{validRange(c.valid_from, c.valid_to)}</span>}
          </p>
          <div className="mt-1.5"><ClaimMeta c={c} /></div>
        </div>
        <span className="mt-0.5 flex shrink-0 items-center gap-1 text-[11px] text-fg-3">
          {supports} source{supports === 1 ? "" : "s"}{against > 0 && <span className="text-warn"> · {against} against</span>}
          <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-180")} />
        </span>
      </button>
      {open && (
        <div className="space-y-2 px-2 pb-2.5 anim-fade-up">
          <p className="tnum text-[11.5px] text-fg-3">
            Seen {fmtDate(c.observed_at)}{c.accepted_at && ` · accepted ${fmtDate(c.accepted_at)}`}
          </p>
          {c.evidence.map((e) => <SourceCard key={e.id} ev={e} />)}
          {c.evidence.length === 0 && <p className="text-[12px] text-warn">No evidence</p>}
        </div>
      )}
    </div>
  );
}

export const predicateLabel = (p: string) => humanize(p).replace(/^\w/, (ch) => ch.toUpperCase());
