"use client";

import { History, Pause, Play } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { Timeline } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { Button, cn, Tip } from "../ui";

/**
 * Time Machine. Scrubs *observation time*: "the graph as it was known at this moment".
 * Valid-time (when a fact held in the world) stays visible per fact in the inspector.
 */
export function TimeMachine({ timeline, index, onChange }: { timeline: Timeline; index: number | null; onChange: (i: number | null) => void }) {
  const pts = timeline.points;
  const last = pts.length - 1;
  const [playing, setPlaying] = useState(false);
  const current = index ?? last;

  useEffect(() => {
    if (!playing) return;
    const t = setInterval(() => {
      onChange(current >= last - 1 ? null : current + 1);
      if (current >= last - 1) setPlaying(false);
    }, 420);
    return () => clearInterval(t);
  }, [playing, current, last, onChange]);

  const spark = useMemo(() => {
    if (pts.length < 2) return "";
    const max = Math.max(...pts.map((p) => p.entities + p.relationships), 1);
    return pts.map((p, i) => `${(i / last) * 100},${22 - ((p.entities + p.relationships) / max) * 20}`).join(" ");
  }, [pts, last]);
  if (pts.length < 2) return null;
  const live = index === null;
  const p = pts[current];
  return (
    <div className={cn("flex items-center gap-3 rounded-xl bg-panel/95 py-2 pl-2 pr-3.5 shadow-panel shadow-[0_0_0_1px_var(--line-2)] backdrop-blur", !live && "shadow-[0_0_0_1.5px_var(--accent-text)]")}>
      <Tip label={playing ? "Pause" : "Replay"}>
        <Button variant="ghost" size="icon" aria-label={playing ? "Pause replay" : "Replay graph history"}
          onClick={() => { if (!playing && live) onChange(0); setPlaying(!playing); }}>
          {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
        </Button>
      </Tip>
      <div className="relative w-[min(42vw,360px)]">
        <svg viewBox="0 0 100 24" preserveAspectRatio="none" className="pointer-events-none absolute inset-x-0 -top-0.5 h-6 w-full" aria-hidden>
          <polyline points={`0,24 ${spark} 100,24`} fill="var(--accent-soft)" stroke="none" />
          <polyline points={spark} fill="none" stroke="var(--accent-text)" strokeWidth="1" vectorEffect="non-scaling-stroke" opacity="0.7" />
          <line x1={(current / last) * 100} x2={(current / last) * 100} y1="0" y2="24" stroke="var(--fg)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        </svg>
        <input type="range" min={0} max={last} value={current} aria-label="Time machine: show the graph as it was known at a past moment"
          aria-valuetext={`${fmtDateTime(p.at)}: ${p.entities} entities, ${p.relationships} relationships`}
          onChange={(e) => { setPlaying(false); const v = +e.target.value; onChange(v >= last ? null : v); }}
          className="relative h-6 w-full cursor-ew-resize opacity-0" />
      </div>
      <div className="min-w-[120px] whitespace-nowrap text-right leading-tight">
        <p className={cn("tnum flex items-center justify-end gap-1 text-[11.5px]", live ? "text-fg-2" : "text-accent-text")}>
          {!live && <History className="h-3 w-3" />}{live ? "Now" : fmtDateTime(p.at)}
        </p>
        <p className="tnum text-[11px] text-fg-3">{p.entities} entities · {p.relationships} links</p>
      </div>
      {!live && <Button size="sm" variant="outline" onClick={() => { setPlaying(false); onChange(null); }}>Now</Button>}
    </div>
  );
}
