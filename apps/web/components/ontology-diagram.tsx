"use client";

import { useMemo } from "react";
import type { Ontology } from "@/lib/api";
import { typeColor } from "@/lib/colors";
import { humanize } from "@/lib/format";

/** A small schema diagram: entity types on a ring, relationship types as labelled arcs. */
export function OntologyDiagram({ ontology, className }: { ontology: Ontology; className?: string }) {
  const view = useMemo(() => {
    const types = ontology.entity_types;
    const n = types.length || 1;
    const R = n === 1 ? 0 : n === 2 ? 110 : 118;
    const pos = new Map(types.map((t, i) => {
      const a = -Math.PI / 2 + (i / n) * Math.PI * 2;
      return [t.type, { x: 200 + Math.cos(a) * R * 1.25, y: 160 + Math.sin(a) * R }];
    }));
    const seen = new Map<string, number>();
    const rels = ontology.relationship_types.filter((r) => pos.has(r.source) && pos.has(r.target)).map((r) => {
      const key = [r.source, r.target].sort().join("|");
      const k = seen.get(key) ?? 0;
      seen.set(key, k + 1);
      return { ...r, k };
    });
    return { pos, rels };
  }, [ontology]);
  if (!ontology.entity_types.length) return <div className={`${className} flex items-center justify-center text-[12px] text-fg-3`}>Add entity types to preview the ontology</div>;
  return (
    <svg viewBox="0 0 400 320" className={className} role="img"
      aria-label={`Ontology: ${ontology.entity_types.map((t) => t.type).join(", ")}; relationships: ${ontology.relationship_types.map((r) => `${r.source} ${humanize(r.type)} ${r.target}`).join(", ")}`}>
      <defs><marker id="onto-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 8 4 0 8z" fill="var(--fg-3)" /></marker></defs>
      {view.rels.map((r) => {
        const a = view.pos.get(r.source)!, b = view.pos.get(r.target)!;
        if (r.source === r.target) {
          const lx = a.x + 46 + r.k * 4, ly = a.y - 30 - r.k * 13;
          return <g key={r.type}><path d={`M ${a.x + 14} ${a.y - 16} C ${a.x + 60} ${a.y - 60}, ${a.x + 70} ${a.y + 10}, ${a.x + 22} ${a.y + 4}`} fill="none" stroke="var(--line-2)" strokeWidth="1.2" markerEnd="url(#onto-arrow)" />
            <text x={lx} y={ly} className="fill-[var(--fg-3)] font-mono" fontSize="8" textAnchor="start">{humanize(r.type)}</text></g>;
        }
        const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1, ux = dx / len, uy = dy / len;
        const flip = r.source < r.target ? 1 : -1;
        const off = (18 + r.k * 15) * flip;
        const mx = (a.x + b.x) / 2 - uy * off, my = (a.y + b.y) / 2 + ux * off;
        const sx = a.x + ux * 22, sy = a.y + uy * 22, tx = b.x - ux * 24, ty = b.y - uy * 24;
        return (
          <g key={r.type + r.source + r.target}>
            <path d={`M ${sx} ${sy} Q ${mx} ${my} ${tx} ${ty}`} fill="none" stroke="var(--line-2)" strokeWidth="1.2" markerEnd="url(#onto-arrow)" strokeDasharray={r.temporal ? undefined : "3 3"} />
            <text x={(sx + 2 * mx + tx) / 4} y={(sy + 2 * my + ty) / 4 + 3} textAnchor="middle" fontSize="8" className="fill-[var(--fg-3)] font-mono" style={{ paintOrder: "stroke", stroke: "var(--bg-2)", strokeWidth: 4 }}>{humanize(r.type)}</text>
          </g>
        );
      })}
      {ontology.entity_types.map((t) => {
        const p = view.pos.get(t.type)!;
        const c = typeColor(t.type, ontology);
        return (
          <g key={t.type}>
            <circle cx={p.x} cy={p.y} r="17" fill={`color-mix(in srgb, ${c} 22%, var(--panel))`} stroke={c} strokeWidth="1.8" />
            <circle cx={p.x} cy={p.y} r="4.5" fill={c} />
            <text x={p.x} y={p.y + 32} textAnchor="middle" fontSize="11" className="fill-[var(--fg)]" style={{ paintOrder: "stroke", stroke: "var(--bg-2)", strokeWidth: 4 }}>{t.type}</text>
          </g>
        );
      })}
    </svg>
  );
}
