"use client";

import { Search } from "lucide-react";
import { useMemo, useState } from "react";
import type { Graph, KGNode } from "@/lib/api";
import { typeColor } from "@/lib/colors";
import { Badge, cn, Input } from "./ui";

/** The graph as a list: the keyboard / screen-reader / small-screen equivalent of the canvas. */
export function EntityList({ graph, nodes, selectedId, onSelect, className }: {
  graph: Graph; nodes: KGNode[]; selectedId?: string | null; onSelect: (id: string) => void; className?: string }) {
  const [q, setQ] = useState("");
  const groups = useMemo(() => {
    const filtered = nodes.filter((n) => n.name.toLowerCase().includes(q.trim().toLowerCase()));
    const by = new Map<string, KGNode[]>();
    filtered.sort((a, b) => b.degree - a.degree || a.name.localeCompare(b.name)).forEach((n) => by.set(n.type, [...(by.get(n.type) ?? []), n]));
    return [...by.entries()];
  }, [nodes, q]);
  return (
    <div className={cn("flex min-h-0 flex-col", className)}>
      <div className="relative shrink-0 p-3">
        <Search className="pointer-events-none absolute left-5 top-1/2 z-10 h-3.5 w-3.5 -translate-y-1/2 text-fg-3" />
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter" aria-label="Filter entities" className="pl-8" />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
        {groups.length === 0 && <p className="px-2 py-6 text-center text-[13px] text-fg-3">No matches</p>}
        {groups.map(([type, items]) => (
          <section key={type} aria-label={type}>
            <h3 className="label sticky top-0 z-10 flex items-center gap-1.5 bg-panel px-2 py-1.5">
              <span className="h-2 w-2 rounded-full" style={{ background: typeColor(type, graph.ontology) }} />{type} · {items.length}
            </h3>
            <ul>
              {items.map((n) => (
                <li key={n.id}>
                  <button onClick={() => onSelect(n.id)} aria-current={selectedId === n.id}
                    className={cn("flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] hover:bg-panel-2", selectedId === n.id && "bg-panel-2")}>
                    <span className="min-w-0 flex-1 truncate">{n.name}</span>
                    {n.contested && <Badge tone="warn">contested</Badge>}
                    {n.status === "proposed" && <Badge tone="muted" dashed>provisional</Badge>}
                    <span className="tnum w-7 shrink-0 text-right text-[11px] text-fg-3" title={`${n.degree} connections`}>{n.degree}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}
