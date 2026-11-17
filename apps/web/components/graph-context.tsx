"use client";

import { createContext, useContext } from "react";
import type { Graph, Run } from "@/lib/api";

export type GraphCtx = {
  graph: Graph;
  runs: Run[];
  activeRun: Run | null; // queued/running/pausing/paused: something the user can control
  latestRun: Run | null;
  startRun: (focus?: string, frontierId?: string) => void;
};
export const GraphContext = createContext<GraphCtx | null>(null);
export function useGraphCtx(): GraphCtx {
  const ctx = useContext(GraphContext);
  if (!ctx) throw new Error("useGraphCtx outside a graph route");
  return ctx;
}
