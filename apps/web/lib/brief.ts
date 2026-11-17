import type { EntityDetail } from "./api";
import { humanize, titleize, validRange } from "./format";

/** A cited Markdown brief for one entity: what an analyst pastes into a memo or an SDR into a CRM note. */
export function entityBrief(e: EntityDetail, graphName: string, synthetic: boolean): string {
  const sources: string[] = [];
  const cite = (evidence: { stance: string; source: { url: string; title: string; domain: string; published_at?: string | null } }[]) =>
    evidence.filter((x) => x.stance === "supports").map((x) => {
      const line = `${x.source.title || x.source.domain} — ${x.source.url}${x.source.published_at ? ` (${x.source.published_at})` : ""}`;
      let i = sources.indexOf(line);
      if (i < 0) { sources.push(line); i = sources.length - 1; }
      return `[${i + 1}]`;
    }).filter((v, i, a) => a.indexOf(v) === i).join("");
  const state = (s: string) => (s === "accepted" ? "verified" : s === "proposed" ? "provisional" : s);
  const out: string[] = [`# ${e.name}`, `${e.type} · ${state(e.status)}${synthetic ? " · SYNTHETIC DEMO DATA" : ""}`, ""];
  if (e.description) out.push(e.description + (e.existence ? ` ${cite(e.existence.evidence)}` : ""), "");
  const facts = e.attributes.filter((a) => a.current);
  if (facts.length) {
    out.push("## Facts");
    for (const a of facts) {
      const c = a.current!;
      const when = validRange(c.valid_from, c.valid_to);
      out.push(`- **${titleize(a.predicate)}:** ${c.value}${when ? ` (${when})` : ""} — ${state(c.status)}, ${c.confidence} confidence ${cite(c.evidence)}`);
    }
    out.push("");
  }
  if (e.neighbors.length) {
    out.push("## Connections");
    for (const n of e.neighbors) {
      const when = validRange(n.valid_from, n.valid_to);
      out.push(`- ${n.direction === "out" ? `${humanize(n.type)} → ${n.entity.name}` : `${n.entity.name} → ${humanize(n.type)}`}${when ? ` (${when})` : ""}${n.status === "proposed" ? " — provisional" : ""}`);
    }
    out.push("");
  }
  if (e.unknowns.length) out.push("## Not yet known", e.unknowns.map((u) => `- ${titleize(u)}`).join("\n"), "");
  if (sources.length) out.push("## Sources", ...sources.map((s, i) => `[${i + 1}] ${s}`), "");
  out.push(`_From the OpenKG graph “${graphName}”. Claims reflect what the cited sources stated when they were read._`);
  return out.join("\n");
}
