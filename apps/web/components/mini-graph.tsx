"use client";

import { useMemo } from "react";
import type { Ontology } from "@/lib/api";
import { typeColor } from "@/lib/colors";
import { useSnapshot } from "@/lib/hooks";
import { layoutGraph } from "@/lib/layout";

/** A small, real rendering of the graph (not a decorative placeholder). */
export function MiniGraph({ graphId, ontology, className }: { graphId: string; ontology: Ontology; className?: string }) {
  const { data } = useSnapshot(graphId, null, true);
  const view = useMemo(() => {
    if (!data?.nodes.length) return null;
    const pos = layoutGraph(data.nodes, data.edges, new Map(), { spacing: 0.8 });
    const xs = [...pos.values()].map((p) => p.x), ys = [...pos.values()].map((p) => p.y);
    const pad = 30, minX = Math.min(...xs) - pad, minY = Math.min(...ys) - pad;
    const w = Math.max(...xs) + pad - minX, h = Math.max(...ys) + pad - minY;
    // Marks scale with the drawing so a 60-node graph stays as legible as a 10-node one.
    return { pos, box: `${minX} ${minY} ${w} ${h}`, k: Math.max(1, Math.max(w / 3.4, h) / 300) };
  }, [data]);
  if (!data) return <div className={className} />;
  if (!view) return <div className={`${className} grid-bg flex items-center justify-center text-[12px] text-fg-3`}>No entities yet</div>;
  return (
    <svg viewBox={view.box} className={className} preserveAspectRatio="xMidYMid meet" role="img" aria-label={`Preview: ${data.nodes.length} entities`}>
      {data.edges.map((e) => {
        const a = view.pos.get(e.from_id), b = view.pos.get(e.to_id);
        if (!a || !b) return null;
        return <line key={e.id} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="var(--fg-3)" strokeOpacity={e.status === "proposed" ? 0.35 : 0.6} strokeWidth={1.6 * view.k} strokeDasharray={e.status === "proposed" ? `${4 * view.k} ${5 * view.k}` : undefined} />;
      })}
      {data.nodes.map((n) => {
        const p = view.pos.get(n.id)!;
        const r = (6 + Math.min(10, Math.sqrt(n.degree) * 2.6)) * view.k;
        const c = typeColor(n.type, ontology);
        return n.status === "proposed"
          ? <circle key={n.id} cx={p.x} cy={p.y} r={r} fill={c} fillOpacity={0.14} stroke={c} strokeWidth={1.8 * view.k} strokeDasharray={`${3 * view.k} ${3 * view.k}`} />
          : <circle key={n.id} cx={p.x} cy={p.y} r={r} fill={c} />;
      })}
    </svg>
  );
}
