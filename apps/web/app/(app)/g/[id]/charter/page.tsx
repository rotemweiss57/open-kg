"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Download, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { CharterEditor, draftProblems, type GraphDraft } from "@/components/charter-editor";
import { useGraphCtx } from "@/components/graph-context";
import { SuggestionTitle } from "@/components/suggestion-actions";
import { toast, toastError } from "@/components/toast";
import { Badge, Button, Card, Dialog, Input, Meter } from "@/components/ui";
import { api, del, patch, type Graph } from "@/lib/api";
import { fmtDateTime, humanize, usd } from "@/lib/format";
import { useInvalidateGraph, useUsage } from "@/lib/hooks";

const toDraft = (g: Graph): GraphDraft => ({ name: g.name, description: g.description, charter: g.charter, ontology: g.ontology,
  autonomy_mode: g.autonomy_mode as GraphDraft["autonomy_mode"], budget_usd: g.budget_usd });

export default function CharterPage() {
  const { graph } = useGraphCtx();
  const router = useRouter();
  const invalidate = useInvalidateGraph(graph.id);
  const [draft, setDraft] = useState<GraphDraft>(() => toDraft(graph));
  const [confirm, setConfirm] = useState(false);
  const [typed, setTyped] = useState("");
  const usage = useUsage(graph.id);
  const audit = useQuery({ queryKey: ["audit", graph.id], queryFn: () => api<{ id: string; action: string; actor_kind: string; detail: Record<string, unknown>; created_at: string }[]>(`/graphs/${graph.id}/audit`) });
  const dirty = JSON.stringify(draft) !== JSON.stringify(toDraft(graph));
  const problems = draftProblems(draft);
  const save = useMutation({ mutationFn: () => patch(`/graphs/${graph.id}`, draft), onSuccess: () => { toast("Saved"); invalidate(); }, onError: toastError });
  const remove = useMutation({ mutationFn: () => del(`/graphs/${graph.id}`), onSuccess: () => { toast("Deleted"); invalidate(); router.replace("/"); }, onError: toastError });
  const u = usage.data;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[1080px] px-4 pb-28 pt-6 sm:px-8">
        <CharterEditor draft={draft} onChange={setDraft} />

        <section className="mt-10 grid gap-4 lg:grid-cols-2" aria-label="Usage and export">
          <Card className="p-4">
            <h3 className="font-display text-2xl">Spend</h3>
            {u && (
              <>
                <p className="tnum mt-2 text-[26px] font-medium leading-none">{usd(u.spent_usd)} <span className="text-[13px] font-normal text-fg-3">of {usd(u.budget_usd)}</span></p>
                <div className="mt-3"><Meter value={u.spent_usd} max={u.budget_usd} label="Graph budget used" /></div>
                {u.cost_per_accepted_claim != null && <p className="tnum mt-2 text-[12px] text-fg-3">{usd(u.cost_per_accepted_claim)} per accepted fact</p>}
                <table className="mt-3 w-full text-left text-[12.5px]">
                  <thead><tr className="label"><th className="py-1 font-normal">Provider</th><th className="font-normal">Operation</th><th className="text-right font-normal">Calls</th><th className="text-right font-normal">Cost</th></tr></thead>
                  <tbody>
                    {u.rows.map((r) => (
                      <tr key={r.provider + r.operation + r.simulated} className="border-t border-line">
                        <td className="py-1.5">{r.provider}{r.simulated && <Badge tone="muted" dashed className="ml-1.5">simulated</Badge>}</td>
                        <td className="text-fg-2">{r.operation}</td><td className="tnum text-right">{r.calls}</td>
                        <td className="tnum text-right">{r.simulated ? "—" : usd(r.est_cost_usd)}</td>
                      </tr>
                    ))}
                    {u.rows.length === 0 && <tr><td colSpan={4} className="py-3 text-fg-3">Nothing yet</td></tr>}
                  </tbody>
                </table>
              </>
            )}
          </Card>
          <Card className="p-4">
            <h3 className="font-display text-2xl">Export</h3>
            <div className="mt-3 flex flex-wrap gap-2">
              {(["markdown", "csv", "json", "graphml"] as const).map((f) => (
                <a key={f} href={`/api/v1/graphs/${graph.id}/export?format=${f}`} download className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-panel-2 px-3.5 text-[13px] font-medium shadow-[0_0_0_1px_var(--line)_inset] hover:bg-panel-3">
                  <Download className="h-3.5 w-3.5" /> {f === "markdown" ? "Report" : f === "graphml" ? "GraphML" : f.toUpperCase()}
                </a>
              ))}
            </div>
            <details className="mt-4 rounded-lg bg-bg-2 p-3 shadow-[0_0_0_1px_var(--line)]">
              <summary className="text-[13px] font-medium">API</summary>
              <pre className="mt-2 overflow-x-auto rounded-md bg-panel p-2.5 font-mono text-[11px] leading-relaxed text-fg-2">{[
                `GET /api/v1/graphs/${graph.id}/table`,
                `GET /api/v1/graphs/${graph.id}/entities/{id}`,
                `GET /api/v1/graphs/${graph.id}/export?format=json`,
                `GET /api/v1/graphs/${graph.id}/changes`,
              ].join("\n")}</pre>
              <a href="/api/docs" target="_blank" rel="noreferrer" className="mt-2 inline-block text-[12.5px] text-accent-text hover:underline">Reference</a>
            </details>
            <h3 className="mt-6 font-display text-2xl">History</h3>
            <ul className="mt-2 max-h-56 space-y-1 overflow-y-auto text-[12.5px]">
              {audit.data?.slice(0, 40).map((a) => (
                <li key={a.id} className="flex items-baseline gap-2">
                  <span className="tnum shrink-0 text-[11.5px] text-fg-3">{fmtDateTime(a.created_at)}</span>
                  <span className="min-w-0 truncate"><span className="text-fg-2">{humanize(a.action.split(".").pop() ?? a.action).replace(/^\w/, (c) => c.toUpperCase())}</span>{typeof a.detail?.title === "string" && <span className="text-fg-3"> · <SuggestionTitle title={a.detail.title} /></span>}</span>
                  <span className="ml-auto shrink-0 text-[11px] text-fg-3">{a.actor_kind}</span>
                </li>
              ))}
              {audit.data?.length === 0 && <li className="text-fg-3">Nothing yet</li>}
            </ul>
          </Card>
        </section>

        <section className="mt-10 rounded-xl p-4 shadow-[0_0_0_1px_var(--danger-soft)]" aria-label="Danger zone">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div><h3 className="text-[14px] font-medium">Delete graph</h3><p className="text-[12.5px] text-fg-3">Removes everything. Cannot be undone.</p></div>
            <Button variant="danger" onClick={() => { setTyped(""); setConfirm(true); }}><Trash2 className="h-4 w-4" /> Delete</Button>
          </div>
        </section>
      </div>

      {dirty && (
        <div className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-bg-2/95 backdrop-blur md:left-[56px]">
          <div className="mx-auto flex max-w-[1080px] items-center justify-between gap-3 px-4 py-3 sm:px-8">
            <span className="text-[12.5px] text-fg-2">{problems[0] ? <span className="text-warn">{problems[0]}</span> : "Unsaved changes"}</span>
            <span className="flex gap-2"><Button variant="ghost" onClick={() => setDraft(toDraft(graph))}>Discard</Button>
              <Button variant="primary" loading={save.isPending} disabled={problems.length > 0} onClick={() => save.mutate()}>Save</Button></span>
          </div>
        </div>
      )}
      <Dialog open={confirm} onOpenChange={setConfirm} title="Delete graph?" description={`Type “${graph.name}” to confirm.`}>
        <form onSubmit={(e) => { e.preventDefault(); if (typed === graph.name) remove.mutate(); }} className="space-y-3">
          <Input autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} aria-label="Type the graph name to confirm" />
          <div className="flex justify-end gap-2"><Button type="button" variant="ghost" onClick={() => setConfirm(false)}>Cancel</Button>
            <Button type="submit" variant="danger" loading={remove.isPending} disabled={typed !== graph.name}>Delete</Button></div>
        </form>
      </Dialog>
    </div>
  );
}
