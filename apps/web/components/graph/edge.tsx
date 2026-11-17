"use client";

import { EdgeLabelRenderer, useInternalNode, type Edge, type EdgeProps } from "@xyflow/react";
import { memo } from "react";
import type { KGEdge } from "@/lib/api";
import { humanize } from "@/lib/format";

export type RelEdgeData = { edge: KGEdge; selected: boolean; highlighted: boolean; showLabel: boolean; dimmed: boolean; fresh: boolean; curve: number; sr: number; tr: number; labelAt: number };
export type RelEdge = Edge<RelEdgeData, "rel">;

/** Floating edge: drawn centre-to-centre and trimmed to each node's radius, with a hand-drawn arrowhead. */
export const RelEdgeView = memo(function RelEdgeView({ id, source, target, data }: EdgeProps<RelEdge>) {
  const a = useInternalNode(source), b = useInternalNode(target);
  if (!a || !b || !data) return null;
  const ax = a.internals.positionAbsolute.x + (a.measured.width ?? 0) / 2, ay = a.internals.positionAbsolute.y + (a.measured.height ?? 0) / 2;
  const bx = b.internals.positionAbsolute.x + (b.measured.width ?? 0) / 2, by = b.internals.positionAbsolute.y + (b.measured.height ?? 0) / 2;
  const dx = bx - ax, dy = by - ay, len = Math.hypot(dx, dy) || 1;
  const ux = dx / len, uy = dy / len;
  const sx = ax + ux * (data.sr + 3), sy = ay + uy * (data.sr + 3);
  const tx = bx - ux * (data.tr + 7), ty = by - uy * (data.tr + 7);
  const mx = (sx + tx) / 2 - uy * data.curve, my = (sy + ty) / 2 + ux * data.curve;
  const path = `M ${sx} ${sy} Q ${mx} ${my} ${tx} ${ty}`;
  // arrowhead along the curve's end tangent
  const tdx = tx - mx, tdy = ty - my, tl = Math.hypot(tdx, tdy) || 1, ex = tdx / tl, ey = tdy / tl;
  const head = `M ${tx + ex * 5} ${ty + ey * 5} L ${tx - ex * 3 - ey * 3.6} ${ty - ey * 3 + ex * 3.6} L ${tx - ex * 3 + ey * 3.6} ${ty - ey * 3 - ex * 3.6} Z`;

  const { edge, selected, highlighted, dimmed, fresh } = data;
  const proposed = edge.status === "proposed";
  const weak = edge.confidence === "low";
  const active = selected || highlighted;
  const stroke = selected ? "var(--accent)" : highlighted ? "var(--fg)" : proposed ? "var(--fg-3)" : "var(--fg-2)";
  const opacity = dimmed ? 0.07 : active ? 1 : edge.ended ? 0.32 : proposed ? 0.7 : 0.62;
  const lt = data.labelAt;
  const lx = (1 - lt) * (1 - lt) * sx + 2 * (1 - lt) * lt * mx + lt * lt * tx, ly = (1 - lt) * (1 - lt) * sy + 2 * (1 - lt) * lt * my + lt * lt * ty;
  return (
    <>
      <path d={path} fill="none" stroke="transparent" strokeWidth={18} className="react-flow__edge-interaction" />
      <path
        id={id} d={path} fill="none" stroke={stroke} strokeWidth={selected ? 2.2 : active ? 1.7 : 1.25} strokeLinecap="round"
        strokeDasharray={weak ? "1.5 5" : proposed ? "5 5" : edge.ended ? "10 4" : undefined}
        className={fresh && proposed ? "kg-edge-live" : undefined}
        style={{ opacity, transition: "opacity 240ms, stroke 160ms" }}
      />
      <path d={head} fill={stroke} style={{ opacity, transition: "opacity 240ms" }} />
      {data.showLabel && (
        <EdgeLabelRenderer>
          <div className="pointer-events-none absolute whitespace-nowrap rounded bg-panel-3 px-1 py-px text-[9px] font-medium text-fg-2 shadow-[0_0_0_1px_var(--line-2)]"
            style={{ transform: `translate(-50%, -50%) translate(${lx}px, ${ly}px)`, zIndex: 5 }}>
            {humanize(edge.type)}{weak ? " ?" : ""}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
});
