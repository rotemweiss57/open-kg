"use client";

import { Plus, ShieldCheck, Trash2, UserCheck } from "lucide-react";
import type { Charter, Ontology } from "@/lib/api";
import { typeColor } from "@/lib/colors";
import { OntologyDiagram } from "./ontology-diagram";
import { Button, cn, Field, Input, Textarea } from "./ui";

export type GraphDraft = { name: string; description: string; charter: Charter; ontology: Ontology; autonomy_mode: "review_first" | "auto_accept_high"; budget_usd: number };

const lines = (v: string[] | undefined) => (v ?? []).join("\n");
const unlines = (v: string) => v.split("\n").map((s) => s.trim()).filter(Boolean);
const snake = (v: string) => v.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
const upper = (v: string) => v.toUpperCase().replace(/[^A-Z0-9]+/g, "_").replace(/^_/, "");
const select = "h-8 w-full rounded-md bg-bg-2 px-1.5 text-[12.5px] text-fg shadow-[0_0_0_1px_var(--line)_inset]";

export function CharterEditor({ draft, onChange }: { draft: GraphDraft; onChange: (d: GraphDraft) => void }) {
  const c = draft.charter, o = draft.ontology;
  const setC = (patch: Partial<Charter>) => onChange({ ...draft, charter: { ...c, ...patch } });
  const setO = (patch: Partial<Ontology>) => onChange({ ...draft, ontology: { ...o, ...patch } });
  const ents = o.entity_types ?? [], rels = o.relationship_types ?? [];
  const typeNames = ents.map((e) => e.type).filter(Boolean);

  return (
    <div className="space-y-8">
      <section aria-labelledby="ce-scope">
        <h3 id="ce-scope" className="font-display text-2xl">Scope</h3>
        <div className="mt-3 grid gap-4">
          <div className="grid gap-4 sm:grid-cols-[1fr_180px]">
            <Field label="Name"><Input value={draft.name} onChange={(e) => onChange({ ...draft, name: e.target.value })} maxLength={200} required /></Field>
            <Field label="Budget (USD)"><Input type="number" min={0} step={1} value={draft.budget_usd} onChange={(e) => onChange({ ...draft, budget_usd: +e.target.value })} /></Field>
          </div>
          <Field label="Question"><Textarea rows={2} value={c.question ?? ""} onChange={(e) => setC({ question: e.target.value })} /></Field>
          <Field label="Limits"><Textarea rows={2} value={c.scope ?? ""} placeholder="Optional. Region, dates, definitions." onChange={(e) => setC({ scope: e.target.value })} /></Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Include"><Textarea rows={3} value={lines(c.inclusion)} onChange={(e) => setC({ inclusion: unlines(e.target.value) })} /></Field>
            <Field label="Exclude"><Textarea rows={3} value={lines(c.exclusion)} onChange={(e) => setC({ exclusion: unlines(e.target.value) })} /></Field>
            <Field label="Time horizon"><Input value={c.time_horizon ?? ""} onChange={(e) => setC({ time_horizon: e.target.value })} /></Field>
            <Field label="Preferred sources"><Textarea rows={2} value={lines(c.preferred_domains)} onChange={(e) => setC({ preferred_domains: unlines(e.target.value) })} /></Field>
          </div>
          <Field label="Evidence standard"><Textarea rows={2} value={c.evidence_standard ?? ""} onChange={(e) => setC({ evidence_standard: e.target.value })} /></Field>
          {(c.ambiguities?.length ?? 0) > 0 && (
            <div className="rounded-xl bg-warn-soft p-3.5">
              <p className="label !text-warn">Assumptions</p>
              <ul className="mt-1.5 list-disc space-y-1 pl-4 text-[13px] text-fg-2">{c.ambiguities!.map((a, i) => <li key={i}>{a}</li>)}</ul>
            </div>
          )}
        </div>
      </section>

      <section aria-labelledby="ce-onto">
        <h3 id="ce-onto" className="font-display text-2xl">Ontology</h3>
        <div className="mt-4 grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="space-y-5">
            <div>
              <p className="label mb-2">Entities</p>
              <ul className="space-y-2">
                {ents.map((e, i) => (
                  <li key={i} className="grid grid-cols-[14px_1fr_32px] items-start gap-2 rounded-lg bg-bg-2 p-2.5 shadow-[0_0_0_1px_var(--line)] sm:grid-cols-[14px_150px_1fr_32px]">
                    <span className="mt-2.5 h-2.5 w-2.5 rounded-full" style={{ background: typeColor(e.type, o) }} />
                    <Input aria-label="Entity type name" className="!h-8" value={e.type} placeholder="Type"
                      onChange={(ev) => { const next = ev.target.value.replace(/[^A-Za-z0-9 ]/g, ""); setO({ entity_types: ents.map((x, j) => j === i ? { ...x, type: next } : x),
                        relationship_types: rels.map((r) => ({ ...r, source: r.source === e.type ? next : r.source, target: r.target === e.type ? next : r.target })) }); }} />
                    <div className="col-span-2 col-start-2 space-y-1.5 sm:col-span-1 sm:col-start-3">
                      <Input aria-label="Description" className="!h-8" value={e.description ?? ""} placeholder="Description"
                        onChange={(ev) => setO({ entity_types: ents.map((x, j) => j === i ? { ...x, description: ev.target.value } : x) })} />
                      <Input aria-label="Attributes, comma separated" className="!h-8 font-mono !text-[12px]" placeholder="attributes"
                        defaultValue={(e.attributes ?? []).map((a) => a.name).join(", ")}
                        onBlur={(ev) => setO({ entity_types: ents.map((x, j) => j === i ? { ...x, attributes: ev.target.value.split(",").map(snake).filter(Boolean).map((name) => x.attributes?.find((a) => a.name === name) ?? { name, description: "" }) } : x) })} />
                    </div>
                    <Button type="button" variant="ghost" size="icon" aria-label={`Remove ${e.type || "type"}`} className="row-start-1 col-start-3 sm:col-start-4"
                      onClick={() => setO({ entity_types: ents.filter((_, j) => j !== i), relationship_types: rels.filter((r) => r.source !== e.type && r.target !== e.type) })}><Trash2 className="h-3.5 w-3.5" /></Button>
                  </li>
                ))}
              </ul>
              <Button type="button" variant="ghost" size="sm" className="mt-2" onClick={() => setO({ entity_types: [...ents, { type: "", description: "", color: "", attributes: [] }] })}><Plus className="h-3.5 w-3.5" /> Add</Button>
            </div>
            <div>
              <p className="label mb-2">Relationships</p>
              <ul className="space-y-2">
                {rels.map((r, i) => {
                  const set = (patch: Partial<typeof r>) => setO({ relationship_types: rels.map((x, j) => j === i ? { ...x, ...patch } : x) });
                  return (
                    <li key={i} className="grid grid-cols-[1fr_1fr_32px] items-center gap-2 rounded-lg bg-bg-2 p-2.5 shadow-[0_0_0_1px_var(--line)] sm:grid-cols-[110px_150px_110px_1fr_32px]">
                      <select aria-label="From type" className={select} value={r.source ?? ""} onChange={(e) => set({ source: e.target.value })}>{["", ...typeNames].map((t) => <option key={t} value={t}>{t || "from…"}</option>)}</select>
                      <Input aria-label="Relationship name" className="!h-8 font-mono !text-[12px]" value={r.type} placeholder="RELATES_TO" onChange={(e) => set({ type: upper(e.target.value) })} />
                      <select aria-label="To type" className={cn(select, "col-start-1 sm:col-start-3")} value={r.target ?? ""} onChange={(e) => set({ target: e.target.value })}>{["", ...typeNames].map((t) => <option key={t} value={t}>{t || "to…"}</option>)}</select>
                      <Input aria-label="Description" className="!h-8" value={r.description ?? ""} placeholder="Description" onChange={(e) => set({ description: e.target.value })} />
                      <Button type="button" variant="ghost" size="icon" aria-label={`Remove ${r.type || "relationship"}`} className="col-start-3 row-start-1 sm:col-start-5" onClick={() => setO({ relationship_types: rels.filter((_, j) => j !== i) })}><Trash2 className="h-3.5 w-3.5" /></Button>
                    </li>
                  );
                })}
              </ul>
              <Button type="button" variant="ghost" size="sm" className="mt-2" onClick={() => setO({ relationship_types: [...rels, { type: "", source: typeNames[0] ?? "", target: typeNames[0] ?? "", description: "", temporal: true }] })}><Plus className="h-3.5 w-3.5" /> Add</Button>
            </div>
          </div>
          <div className="lg:sticky lg:top-4 lg:self-start">
            <p className="label mb-2">Preview</p>
            <div className="grid-bg rounded-xl bg-bg-2 shadow-[0_0_0_1px_var(--line)]"><OntologyDiagram ontology={o} className="h-[280px] w-full" /></div>
          </div>
        </div>
      </section>

      <section aria-labelledby="ce-auto">
        <h3 id="ce-auto" className="font-display text-2xl">Review</h3>
        <div role="radiogroup" aria-label="Autonomy mode" className="mt-3 grid gap-2.5 sm:grid-cols-2">
          {([
            ["review_first", <UserCheck key="i" className="h-4 w-4" />, "Review everything", "Nothing enters the graph without you."],
            ["auto_accept_high", <ShieldCheck key="i" className="h-4 w-4" />, "Approve on evidence", "Two independent sources, or an organisation’s own site. Changes and conflicts still wait for you."],
          ] as const).map(([id, icon, title, body]) => (
            <button key={id} type="button" role="radio" aria-checked={draft.autonomy_mode === id} onClick={() => onChange({ ...draft, autonomy_mode: id })}
              className={cn("rounded-xl p-3.5 text-left transition-shadow", draft.autonomy_mode === id ? "bg-accent-soft shadow-[0_0_0_1.5px_var(--accent-text)]" : "bg-bg-2 shadow-[0_0_0_1px_var(--line)] hover:shadow-[0_0_0_1px_var(--line-2)]")}>
              <span className="flex items-center gap-2 text-[13.5px] font-medium">{icon}{title}</span>
              <span className="mt-1 block text-[12.5px] text-fg-2">{body}</span>
            </button>
          ))}
        </div>
        <Field label="Watch by default" className="mt-4 max-w-sm">
          <Textarea rows={2} className="font-mono !text-[12px]" value={lines(c.watch_defaults)} onChange={(e) => setC({ watch_defaults: unlines(e.target.value).map(snake) })} />
        </Field>
      </section>
    </div>
  );
}

export function draftProblems(d: GraphDraft): string[] {
  const out: string[] = [];
  if (!d.name.trim()) out.push("Give the graph a name.");
  const names = d.ontology.entity_types.map((e) => e.type.trim());
  if (!names.length) out.push("Add at least one entity type.");
  if (names.some((n) => !n)) out.push("Every entity type needs a name.");
  if (new Set(names).size !== names.length) out.push("Entity type names must be unique.");
  if (d.ontology.relationship_types.some((r) => !r.type || !names.includes(r.source ?? "") || !names.includes(r.target ?? ""))) out.push("Every relationship needs a name and valid endpoints.");
  return out;
}
