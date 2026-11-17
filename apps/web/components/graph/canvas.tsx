"use client";

import { Background, BackgroundVariant, ReactFlow, ReactFlowProvider, useNodesInitialized, useNodesState, useReactFlow, type Viewport } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Bookmark, Crosshair, List, Maximize2, Minus, Plus, Search, Shuffle, Trash2, X } from "lucide-react";
import { DropdownMenu } from "radix-ui";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Graph, KGEdge, KGNode, Snapshot } from "@/lib/api";
import { typeColor } from "@/lib/colors";
import { layoutGraph, nodeRadius, type Pos } from "@/lib/layout";
import type { Selection } from "../inspector";
import { Button, cn, Input, Tip } from "../ui";
import { RelEdgeView, type RelEdge } from "./edge";
import { EntityNodeView, type EntityNode } from "./node";

const nodeTypes = { entity: EntityNodeView };
const edgeTypes = { rel: RelEdgeView };
type SavedView = { name: string; hidden: string[]; viewport: Viewport };

type Props = {
  graph: Graph;
  snapshot: Snapshot;
  selection: Selection;
  onSelect: (s: Selection) => void;
  pulseIds: Set<string>;
  showProposed: boolean;
  onShowProposed: (v: boolean) => void;
  onToggleList: () => void;
  historical: boolean;
  children?: React.ReactNode;
};

export function GraphCanvas(props: Props) {
  return <ReactFlowProvider><Inner {...props} /></ReactFlowProvider>;
}

function Inner({ graph, snapshot, selection, onSelect, pulseIds, showProposed, onShowProposed, onToggleList, historical, children }: Props) {
  const flow = useReactFlow();
  const wrap = useRef<HTMLDivElement>(null);
  const positions = useRef(new Map<string, Pos>());
  const seen = useRef<Set<string> | null>(null);
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [layoutNonce, setLayoutNonce] = useState(0);
  const [query, setQuery] = useState("");
  const [views, setViews] = useState<SavedView[]>([]);
  const viewsKey = `openkg-views-${graph.id}`;

  useEffect(() => {
    queueMicrotask(() => { try { setViews(JSON.parse(localStorage.getItem(viewsKey) ?? "[]")); } catch { /* ignore */ } });
  }, [viewsKey]);

  const types = useMemo(() => {
    const counts = new Map<string, number>();
    snapshot.nodes.forEach((n) => counts.set(n.type, (counts.get(n.type) ?? 0) + 1));
    const order = graph.ontology.entity_types.map((t) => t.type);
    return [...counts.entries()].sort((a, b) => (order.indexOf(a[0]) + 99) % 99 - (order.indexOf(b[0]) + 99) % 99);
  }, [snapshot.nodes, graph.ontology]);
  const shapeOf = useCallback((type: string) => Math.max(0, graph.ontology.entity_types.findIndex((t) => t.type === type)), [graph.ontology]);

  const visible = useMemo(() => {
    const nodes = snapshot.nodes.filter((n) => !hidden.has(n.type));
    const ids = new Set(nodes.map((n) => n.id));
    return { nodes, edges: snapshot.edges.filter((e) => ids.has(e.from_id) && ids.has(e.to_id)), ids };
  }, [snapshot, hidden]);

  // Stable layout: existing nodes stay put, new ones are born next to a neighbour.
  const layoutKey = visible.nodes.map((n) => n.id).sort().join("|") + "#" + visible.edges.length + "#" + layoutNonce;
  // The position map is an imperative layout cache (also written by drag handlers), so it is
  // deliberately a ref that this memo updates when the node/edge set changes.
  /* eslint-disable react-hooks/refs */
  useMemo(() => {
    positions.current = layoutGraph(visible.nodes, visible.edges, positions.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layoutKey]);

  // Graph Bloom: anything that appears after first paint animates in.
  useEffect(() => {
    const ids = [...visible.nodes.map((n) => n.id), ...visible.edges.map((e) => e.id)];
    if (seen.current === null) { seen.current = new Set(ids); return; }
    const added = ids.filter((id) => !seen.current!.has(id));
    if (!added.length) return;
    added.forEach((id) => seen.current!.add(id));
    queueMicrotask(() => setFresh((prev) => new Set([...prev, ...added])));
    // Keep discoveries in sight: if a newcomer landed outside the viewport, ease the camera out.
    const el = wrap.current, vp = flow.getViewport();
    const outside = el && added.some((id) => {
      const p = positions.current.get(id);
      if (!p) return false;
      const sx = p.x * vp.zoom + vp.x, sy = p.y * vp.zoom + vp.y;
      return sx < 40 || sy < 40 || sx > el.clientWidth - 40 || sy > el.clientHeight - 110;
    });
    if (outside) setTimeout(() => flow.fitView({ padding: 0.2, maxZoom: 1.2, duration: 700 }), 120);
    const t = setTimeout(() => setFresh((prev) => { const next = new Set(prev); added.forEach((id) => next.delete(id)); return next; }), 2600);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible]);

  const neighbourhood = useMemo(() => {
    if (!selection) return null;
    const set = new Set<string>();
    if (selection.kind === "entity") {
      set.add(selection.id);
      visible.edges.forEach((e) => { if (e.from_id === selection.id) set.add(e.to_id); if (e.to_id === selection.id) set.add(e.from_id); });
    } else {
      const e = visible.edges.find((x) => x.id === selection.id);
      if (e) { set.add(e.from_id); set.add(e.to_id); }
    }
    return set.size ? set : null;
  }, [selection, visible.edges]);

  const maxDegree = useMemo(() => Math.max(1, ...visible.nodes.map((n) => n.degree)), [visible.nodes]);
  const derivedNodes: EntityNode[] = useMemo(() => visible.nodes.map((n) => {
    const p = positions.current.get(n.id) ?? { x: 0, y: 0 };
    const r = nodeRadius(n.degree);
    return {
      id: n.id, type: "entity", position: p, width: r * 2, height: r * 2, focusable: true,
      ariaLabel: `${n.type} ${n.name}`,
      data: {
        node: n, color: typeColor(n.type, graph.ontology), r, shape: shapeOf(n.type),
        selected: selection?.kind === "entity" && selection.id === n.id,
        dimmed: !!neighbourhood && !neighbourhood.has(n.id),
        pulse: pulseIds.has(n.id), fresh: fresh.has(n.id),
        minor: visible.nodes.length > 40 && n.degree < Math.max(2, maxDegree * 0.3),
      },
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [visible.nodes, layoutKey, selection, neighbourhood, pulseIds, fresh, graph.ontology, shapeOf, maxDegree]);
  /* eslint-enable react-hooks/refs */

  const [nodes, setNodes, onNodesChange] = useNodesState<EntityNode>([]);
  useEffect(() => { setNodes(derivedNodes); }, [derivedNodes, setNodes]);
  // Fit once the first real layout has been measured (and again after an explicit re-layout).
  const initialized = useNodesInitialized();
  const fitted = useRef(-1);
  useEffect(() => {
    if (initialized && nodes.length > 0 && fitted.current !== layoutNonce) {
      const first = fitted.current === -1;
      fitted.current = layoutNonce;
      // two frames later, so the fit uses final measured node sizes and the settled container size
      requestAnimationFrame(() => requestAnimationFrame(() => flow.fitView({ padding: 0.17, maxZoom: 1.2, duration: first ? 0 : 400 })));
    }
  }, [initialized, nodes.length, layoutNonce, flow]);

  const edges: RelEdge[] = useMemo(() => {
    const radius = new Map(visible.nodes.map((n) => [n.id, nodeRadius(n.degree)]));
    const pairCount = new Map<string, number>();
    const touching = selection?.kind === "entity" ? visible.edges.filter((e) => e.from_id === selection.id || e.to_id === selection.id).length : 0;
    return visible.edges.map((e: KGEdge) => {
      const key = [e.from_id, e.to_id].sort().join("|");
      const k = pairCount.get(key) ?? 0;
      pairCount.set(key, k + 1);
      const isSel = selection?.kind === "relationship" && selection.id === e.id;
      const touches = selection?.kind === "entity" && (e.from_id === selection.id || e.to_id === selection.id);
      return {
        id: e.id, source: e.from_id, target: e.to_id, type: "rel", focusable: true, ariaLabel: `Relationship ${e.type}`,
        zIndex: isSel || touches ? 3 : 0,
        data: {
          edge: e, selected: isSel, highlighted: !!touches, showLabel: isSel || (!!touches && touching <= 6), dimmed: !!selection && !isSel && !touches, fresh: fresh.has(e.id),
          curve: k === 0 ? 9 : (k % 2 ? 1 : -1) * (9 + 22 * Math.ceil(k / 2)) * (e.from_id < e.to_id ? 1 : -1),
          sr: radius.get(e.from_id) ?? 14, tr: radius.get(e.to_id) ?? 14,
          labelAt: selection?.kind === "entity" ? (e.from_id === selection.id ? 0.66 : 0.34) : 0.5,
        },
      };
    });
  }, [visible, selection, fresh]);

  // Re-fit when the canvas itself changes size a lot (the inspector opening or closing), unless the
  // user is focused on a selection.
  const hasSelection = !!selection;
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    let last = el.clientWidth, timer: ReturnType<typeof setTimeout> | null = null;
    const ro = new ResizeObserver(() => {
      if (Math.abs(el.clientWidth - last) < 120) return;
      last = el.clientWidth;
      if (timer) clearTimeout(timer);
      if (!hasSelection) timer = setTimeout(() => flow.fitView({ padding: 0.17, maxZoom: 1.2, duration: 350 }), 120);
    });
    ro.observe(el);
    return () => { ro.disconnect(); if (timer) clearTimeout(timer); };
  }, [flow, hasSelection]);

  // Level of detail: minor labels disappear when zoomed out (DOM attribute, no React re-render).
  const onMove = useCallback((_: unknown, vp: Viewport) => {
    wrap.current?.setAttribute("data-zoom", vp.zoom < 0.38 ? "far" : vp.zoom < 0.62 ? "mid" : "near");
  }, []);

  const centerOn = useCallback((id: string) => {
    const p = positions.current.get(id);
    if (p) flow.setCenter(p.x, p.y, { zoom: Math.max(flow.getZoom(), 1.05), duration: 500 });
  }, [flow]);
  // Jumping to an entity (search, list, link, command palette) frames its neighbourhood.
  // Clicking a node on the canvas does not move the camera: the user is already looking at it.
  const lastCentered = useRef<string | null>(null);
  const clickedOnCanvas = useRef(false);
  useEffect(() => {
    const id = selection?.kind === "entity" ? selection.id : null;
    if (id && lastCentered.current !== id && nodes.some((n) => n.id === id)) {
      if (!clickedOnCanvas.current && neighbourhood) {
        const ids = [...neighbourhood].map((n) => ({ id: n }));
        setTimeout(() => flow.fitView({ nodes: ids, padding: 0.3, maxZoom: 1.25, minZoom: 0.5, duration: 550 }), 90);
      }
      lastCentered.current = id;
    }
    if (!id) lastCentered.current = null;
    clickedOnCanvas.current = false;
  }, [selection, neighbourhood, flow, nodes]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q.length < 1 ? [] : snapshot.nodes.filter((n) => n.name.toLowerCase().includes(q)).slice(0, 8);
  }, [query, snapshot.nodes]);
  const pick = (n: KGNode) => {
    setQuery("");
    if (hidden.has(n.type)) setHidden((h) => { const next = new Set(h); next.delete(n.type); return next; });
    onSelect({ kind: "entity", id: n.id });
    setTimeout(() => centerOn(n.id), 60);
  };

  const saveViews = (next: SavedView[]) => { setViews(next); try { localStorage.setItem(viewsKey, JSON.stringify(next)); } catch { /* ignore */ } };
  const proposedCount = snapshot.nodes.filter((n) => n.status === "proposed").length + snapshot.edges.filter((e) => e.status === "proposed").length;

  return (
    <div ref={wrap} data-zoom="near" className="kg-canvas relative h-full w-full overflow-hidden bg-bg">
      <style>{`.kg-canvas[data-zoom="far"] .kg-label{opacity:0}.kg-canvas[data-zoom="mid"] .kg-label[data-minor="true"]{opacity:0}.kg-label{transition:opacity 200ms}`}</style>
      {/* The flow stops above the bottom overlays (legend, time machine) so fit-to-view never hides nodes under them. */}
      <div className="absolute inset-x-0 bottom-[54px] top-0">
      <ReactFlow
        nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} onNodesChange={onNodesChange}
        nodeOrigin={[0.5, 0.5]} fitView fitViewOptions={{ padding: 0.17, maxZoom: 1.2 }} minZoom={0.15} maxZoom={2.4}
        nodesConnectable={false} edgesReconnectable={false} deleteKeyCode={null} selectionKeyCode={null} multiSelectionKeyCode={null}
        onlyRenderVisibleElements={nodes.length > 150} proOptions={{ hideAttribution: false }}
        onNodeClick={(_, n) => { clickedOnCanvas.current = true; onSelect({ kind: "entity", id: n.id }); }}
        onEdgeClick={(_, e) => onSelect({ kind: "relationship", id: e.id })}
        onPaneClick={() => onSelect(null)} onMove={onMove}
        onNodeDragStop={(_, n) => { positions.current.set(n.id, { x: n.position.x, y: n.position.y }); }}
        onKeyDown={(ev) => {
          const el = ev.target as HTMLElement;
          if ((ev.key === "Enter" || ev.key === " ") && el.dataset.id) {
            ev.preventDefault();
            onSelect({ kind: el.classList.contains("react-flow__edge") ? "relationship" : "entity", id: el.dataset.id });
          }
          if (ev.key === "Escape") onSelect(null);
        }}
        aria-label={`Knowledge graph with ${visible.nodes.length} entities and ${visible.edges.length} relationships`}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="var(--grid)" />
      </ReactFlow>
      </div>

      {/* top-left: find + filter */}
      <div className="pointer-events-none absolute left-3 top-3 flex max-w-[calc(100%-70px)] flex-col gap-2">
        <div className="pointer-events-auto relative w-[min(260px,62vw)]">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 z-10 h-3.5 w-3.5 -translate-y-1/2 text-fg-3" />
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find" aria-label="Find an entity in this graph"
            onKeyDown={(e) => { if (e.key === "Enter" && matches[0]) pick(matches[0]); if (e.key === "Escape") setQuery(""); }}
            className="!h-8 !bg-panel/95 pl-8 backdrop-blur" />
          {matches.length > 0 && (
            <ul role="listbox" className="absolute z-20 mt-1 w-full overflow-hidden rounded-lg bg-panel p-1 shadow-panel shadow-[0_0_0_1px_var(--line-2)]">
              {matches.map((n) => (
                <li key={n.id}>
                  <button role="option" aria-selected={false} onClick={() => pick(n)} className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] hover:bg-panel-2">
                    <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: typeColor(n.type, graph.ontology) }} />
                    <span className="truncate">{n.name}</span><span className="ml-auto shrink-0 text-[11px] text-fg-3">{n.type}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="pointer-events-auto flex flex-wrap gap-1.5" role="group" aria-label="Filter by entity type">
          {types.map(([type, count]) => {
            const off = hidden.has(type);
            return (
              <button key={type} aria-pressed={!off} title={off ? `Show ${type}` : `Hide ${type}`}
                onClick={() => setHidden((h) => { const next = new Set(h); if (off) next.delete(type); else next.add(type); return next; })}
                className={cn("inline-flex h-6 items-center gap-1.5 rounded-full bg-panel/95 px-2 text-[11.5px] shadow-[0_0_0_1px_var(--line)] backdrop-blur transition-opacity", off ? "text-fg-3 opacity-55 line-through" : "text-fg-2 hover:text-fg")}>
                <span className="h-2 w-2 rounded-full" style={{ background: typeColor(type, graph.ontology) }} />{type}<span className="tnum text-fg-3">{count}</span>
              </button>
            );
          })}
          <button aria-pressed={showProposed} onClick={() => onShowProposed(!showProposed)} disabled={historical && false}
            title={showProposed ? "Hide provisional suggestions" : "Show provisional suggestions"}
            className={cn("inline-flex h-6 items-center gap-1.5 rounded-full bg-panel/95 px-2 text-[11.5px] shadow-[0_0_0_1px_var(--line)] backdrop-blur", showProposed ? "text-fg-2 hover:text-fg" : "text-fg-3 opacity-55 line-through")}>
            <span className="h-2 w-2 rounded-full border border-dashed border-fg-2" />Provisional{showProposed && proposedCount > 0 && <span className="tnum text-fg-3">{proposedCount}</span>}
          </button>
        </div>
      </div>

      {/* top-right: view controls */}
      <div className="absolute right-3 top-3 flex flex-col gap-1 rounded-xl bg-panel/95 p-1 shadow-[0_0_0_1px_var(--line)] backdrop-blur" role="toolbar" aria-label="Canvas controls">
        <Tip label="Zoom in" side="left"><Button variant="ghost" size="icon" aria-label="Zoom in" onClick={() => flow.zoomIn({ duration: 200 })}><Plus className="h-4 w-4" /></Button></Tip>
        <Tip label="Zoom out" side="left"><Button variant="ghost" size="icon" aria-label="Zoom out" onClick={() => flow.zoomOut({ duration: 200 })}><Minus className="h-4 w-4" /></Button></Tip>
        <Tip label="Fit" side="left"><Button variant="ghost" size="icon" aria-label="Fit graph to view" onClick={() => flow.fitView({ padding: 0.17, duration: 400, maxZoom: 1.2 })}><Maximize2 className="h-4 w-4" /></Button></Tip>
        <Tip label="Tidy" side="left"><Button variant="ghost" size="icon" aria-label="Re-run automatic layout"
          onClick={() => { positions.current = new Map(); setLayoutNonce((n) => n + 1); }}><Shuffle className="h-4 w-4" /></Button></Tip>
        {selection?.kind === "entity" && <Tip label="Centre" side="left"><Button variant="ghost" size="icon" aria-label="Centre on selection" onClick={() => centerOn(selection.id)}><Crosshair className="h-4 w-4" /></Button></Tip>}
        <Tip label="List" side="left"><Button variant="ghost" size="icon" aria-label="Open entity list" onClick={onToggleList}><List className="h-4 w-4" /></Button></Tip>
        <DropdownMenu.Root>
          <Tip label="Saved views" side="left">
            <DropdownMenu.Trigger asChild><Button variant="ghost" size="icon" aria-label="Saved views"><Bookmark className="h-4 w-4" /></Button></DropdownMenu.Trigger>
          </Tip>
          <DropdownMenu.Portal>
            <DropdownMenu.Content side="left" align="start" sideOffset={8} className="z-[70] w-60 rounded-xl bg-panel p-1.5 shadow-panel shadow-[0_0_0_1px_var(--line-2)]">
              <DropdownMenu.Label className="label px-2 py-1.5">Saved views</DropdownMenu.Label>
              {views.map((v, i) => (
                <div key={v.name + i} className="flex items-center">
                  <DropdownMenu.Item onSelect={() => { setHidden(new Set(v.hidden)); flow.setViewport(v.viewport, { duration: 400 }); }}
                    className="flex-1 cursor-pointer truncate rounded-md px-2 py-1.5 text-[13px] outline-none data-[highlighted]:bg-panel-2">{v.name}</DropdownMenu.Item>
                  <button aria-label={`Delete view ${v.name}`} onClick={() => saveViews(views.filter((_, j) => j !== i))} className="flex h-7 w-7 items-center justify-center rounded-md text-fg-3 hover:text-danger"><Trash2 className="h-3.5 w-3.5" /></button>
                </div>
              ))}
              <DropdownMenu.Separator className="my-1 h-px bg-line" />
              <DropdownMenu.Item
                onSelect={() => saveViews([...views, { name: `View ${views.length + 1}${hidden.size ? ` · without ${[...hidden].join(", ")}` : ""}`, hidden: [...hidden], viewport: flow.getViewport() }])}
                className="cursor-pointer rounded-md px-2 py-1.5 text-[13px] text-accent-text outline-none data-[highlighted]:bg-panel-2">Save view</DropdownMenu.Item>
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
      </div>

      {/* legend: trust state is encoded in line style, so say what the styles mean */}
      <div className="pointer-events-none absolute bottom-3 left-3 hidden items-center gap-3 rounded-lg bg-panel/90 px-2.5 py-1.5 text-[11px] text-fg-3 shadow-[0_0_0_1px_var(--line)] backdrop-blur lg:flex" aria-hidden data-legend>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-[3px] border-2 border-fg-2 bg-fg-3/30" />Verified</span>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-[3px] border border-dashed border-fg-2" />Provisional</span>
        <span className="flex items-center gap-1.5"><span className="flex h-2.5 w-2.5 items-center justify-center rounded-full bg-warn text-[7px] font-bold text-black">!</span>Contested</span>
        <span className="flex items-center gap-1.5"><svg width="18" height="6"><line x1="0" y1="3" x2="18" y2="3" stroke="var(--fg-2)" strokeWidth="1.4" strokeDasharray="1.5 4" /></svg>Inferred</span>
      </div>
      {snapshot.truncated && (
        <div className="absolute left-1/2 top-3 -translate-x-1/2 rounded-full bg-panel/95 px-3 py-1 text-[12px] text-fg-2 shadow-[0_0_0_1px_var(--line-2)]">
          Top {snapshot.nodes.length} of {snapshot.total_nodes}. Expand a node for more.
        </div>
      )}
      {visible.nodes.length === 0 && snapshot.nodes.length > 0 && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-[13px] text-fg-3">Everything is filtered out.
          <button className="pointer-events-auto ml-2 text-accent-text" onClick={() => setHidden(new Set())}><X className="mr-0.5 inline h-3 w-3" />Clear</button></div>
      )}
      {children}
    </div>
  );
}
