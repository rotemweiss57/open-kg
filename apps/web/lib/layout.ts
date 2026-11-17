import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type SimulationNodeDatum } from "d3-force";

export type Pos = { x: number; y: number };
type N = SimulationNodeDatum & { id: string; r: number };

function seeded(id: string): Pos {
  let h = 2166136261;
  for (let i = 0; i < id.length; i++) h = Math.imul(h ^ id.charCodeAt(i), 16777619);
  const a = ((h >>> 0) % 3600) / 3600 * Math.PI * 2;
  const d = 60 + ((h >>> 8) % 260);
  return { x: Math.cos(a) * d, y: Math.sin(a) * d };
}

export const nodeRadius = (degree: number) => Math.round(13 + Math.min(17, Math.sqrt(degree) * 4.6));

/**
 * Deterministic force layout that is *stable under growth*: nodes that already have a position
 * are held almost in place, and new nodes are born next to a neighbour and then relaxed. This
 * is what lets discoveries "bloom" into the existing map instead of reshuffling it.
 */
export function layoutGraph(
  nodes: { id: string; degree: number }[],
  edges: { from_id: string; to_id: string }[],
  prev: Map<string, Pos>,
  opts: { spacing?: number; pinned?: Set<string> } = {},
): Map<string, Pos> {
  const spacing = opts.spacing ?? 1;
  const ids = new Set(nodes.map((n) => n.id));
  const links = edges.filter((e) => ids.has(e.from_id) && ids.has(e.to_id)).map((e) => ({ source: e.from_id, target: e.to_id }));
  const neighbours = new Map<string, string[]>();
  for (const l of links) {
    (neighbours.get(l.source) ?? neighbours.set(l.source, []).get(l.source)!).push(l.target);
    (neighbours.get(l.target) ?? neighbours.set(l.target, []).get(l.target)!).push(l.source);
  }
  const fresh = nodes.filter((n) => !prev.has(n.id)).length;
  const incremental = prev.size > 0 && fresh < nodes.length;
  // Centroid of what is already placed: newcomers with no placed neighbour start near it,
  // not at an arbitrary far-away spot.
  let cx = 0, cy = 0;
  prev.forEach((p) => { cx += p.x; cy += p.y; });
  if (prev.size) { cx /= prev.size; cy /= prev.size; }
  const home = new Map<string, Pos>();
  const sim: N[] = nodes.map((n) => {
    const p = prev.get(n.id);
    if (p) {
      if (incremental) home.set(n.id, p);
      return { id: n.id, r: nodeRadius(n.degree), x: p.x, y: p.y, ...(opts.pinned?.has(n.id) ? { fx: p.x, fy: p.y } : {}) };
    }
    const placed = (neighbours.get(n.id) ?? []).map((id) => prev.get(id)).filter(Boolean) as Pos[];
    const s = seeded(n.id);
    const ax = placed.length ? placed.reduce((a, q) => a + q.x, 0) / placed.length : cx;
    const ay = placed.length ? placed.reduce((a, q) => a + q.y, 0) / placed.length : cy;
    return { id: n.id, r: nodeRadius(n.degree), x: ax + s.x * 0.4, y: ay + s.y * 0.4 };
  });
  if (fresh === 0) return new Map(sim.map((n) => [n.id, { x: n.x!, y: n.y! }]));
  const simulation = forceSimulation(sim)
    .force("link", forceLink<N, { source: string; target: string }>(links).id((d) => d.id).distance(104 * spacing).strength(0.6))
    .force("charge", forceManyBody<N>().strength(-480 * spacing).distanceMax(700))
    .force("collide", forceCollide<N>().radius((d) => d.r + 34 * spacing).strength(0.9))
    // Existing nodes are tethered to where they were (they may drift a little to make room);
    // new nodes only feel the weak pull toward the centre.
    .force("x", forceX<N>((d) => home.get(d.id)?.x ?? cx).strength((d) => (home.has(d.id) ? 0.42 : 0.05)))
    .force("y", forceY<N>((d) => home.get(d.id)?.y ?? cy).strength((d) => (home.has(d.id) ? 0.42 : 0.15)))
    .stop();
  if (!incremental) simulation.force("center", forceCenter(0, 0));
  const ticks = incremental ? 220 : 340;
  for (let i = 0; i < ticks; i++) simulation.tick();
  return new Map(sim.map((n) => [n.id, { x: Math.round(n.x!), y: Math.round(n.y!) }]));
}
