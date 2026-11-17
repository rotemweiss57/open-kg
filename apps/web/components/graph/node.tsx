"use client";

import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { Eye } from "lucide-react";
import { memo } from "react";
import type { KGNode } from "@/lib/api";

export type EntityNodeData = {
  node: KGNode; color: string; r: number; shape: number;
  selected: boolean; dimmed: boolean; pulse: boolean; fresh: boolean; minor: boolean;
};
export type EntityNode = Node<EntityNodeData, "entity">;

const SHAPES = ["28%", "50%", "8%", "50% 50% 24% 24%"]; // squircle, circle, diamond (rotated), arch

/**
 * Trust state is encoded in form, not just colour: accepted = solid tinted fill,
 * provisional = dashed hollow outline, contested = amber marker.
 */
export const EntityNodeView = memo(function EntityNodeView({ data }: NodeProps<EntityNode>) {
  const { node, color, r, shape, selected, dimmed, pulse, fresh, minor } = data;
  const proposed = node.status === "proposed";
  const diamond = shape % SHAPES.length === 2;
  const size = r * 2;
  return (
    <div
      className={fresh ? "anim-bloom" : undefined}
      style={{ width: size, height: size, opacity: dimmed ? 0.16 : 1, transition: "opacity 240ms" }}
      aria-label={`${node.type}: ${node.name}, ${proposed ? "provisional" : "verified"}${node.contested ? ", has contested facts" : ""}`}
    >
      {pulse && <span className="pulse-ring pointer-events-none absolute inset-0 rounded-full" style={{ boxShadow: "0 0 0 2px var(--accent)" }} />}
      {selected && <span className="pointer-events-none absolute -inset-[7px] rounded-full" style={{ boxShadow: "0 0 0 1.5px var(--accent), 0 0 28px 2px var(--accent-soft)" }} />}
      <div
        className="kg-node-core absolute inset-0 flex items-center justify-center"
        style={{
          borderRadius: SHAPES[shape % SHAPES.length],
          transform: diamond ? "rotate(45deg) scale(0.82)" : undefined,
          background: proposed ? `color-mix(in srgb, ${color} 7%, var(--bg))` : `color-mix(in srgb, ${color} 26%, var(--panel))`,
          border: `${proposed ? 1.5 : 2}px ${proposed ? "dashed" : "solid"} ${color}`,
        }}
      >
        <span className="h-[30%] w-[30%] rounded-full" style={{ background: color, opacity: proposed ? 0.75 : 1 }} />
      </div>
      {node.contested && (
        <span title="Contested" className="absolute -right-0.5 -top-0.5 flex h-[13px] w-[13px] items-center justify-center rounded-full bg-warn font-mono text-[9px] font-bold text-black ring-2 ring-bg">!</span>
      )}
      {node.watched && (
        <span title="Watched" className="absolute -left-0.5 -top-0.5 flex h-[13px] w-[13px] items-center justify-center rounded-full bg-panel-3 text-info ring-2 ring-bg"><Eye className="h-2 w-2" strokeWidth={3} /></span>
      )}
      <div
        className="kg-label pointer-events-none absolute left-1/2 top-full mt-1.5 w-max max-w-[150px] -translate-x-1/2 text-center leading-tight"
        data-minor={minor && !selected}
      >
        <span className={`rounded px-1 text-[11px] ${selected ? "font-medium text-fg" : proposed ? "italic text-fg-3" : "text-fg-2"}`}
          style={{ background: "color-mix(in srgb, var(--bg) 78%, transparent)" }}>
          {node.name}
        </span>
      </div>
      <Handle type="target" position={Position.Top} isConnectable={false} className="!left-1/2 !top-1/2 !h-px !w-px !min-h-0 !min-w-0 !border-0 !bg-transparent !opacity-0" />
      <Handle type="source" position={Position.Top} isConnectable={false} className="!left-1/2 !top-1/2 !h-px !w-px !min-h-0 !min-w-0 !border-0 !bg-transparent !opacity-0" />
    </div>
  );
});
