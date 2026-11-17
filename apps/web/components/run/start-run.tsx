"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Globe, TriangleAlert } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { post, type Graph, type Run } from "@/lib/api";
import { usd } from "@/lib/format";
import { useSystem } from "@/lib/hooks";
import { toastError } from "../toast";
import { Button, cn, Dialog, Field, Input, Textarea } from "../ui";

export function StartRunDialog({ graph, open, onOpenChange, focus, frontierId }: {
  graph: Graph; open: boolean; onOpenChange: (v: boolean) => void; focus?: string; frontierId?: string }) {
  const system = useSystem();
  const router = useRouter();
  const qc = useQueryClient();
  const sys = system.data;
  const liveOk = !!sys?.live_research_available && !graph.is_synthetic;
  const [engine, setEngine] = useState<"fixture" | "deep_agent">("fixture");
  const [scenario, setScenario] = useState("");
  const [focusText, setFocusText] = useState("");
  const [budget, setBudget] = useState({ max_usd: 1, max_tavily_calls: 30, max_model_calls: 60, deadline_minutes: 30 });

  useEffect(() => {
    if (!open || !sys) return;
    queueMicrotask(() => {
      setEngine(liveOk ? "deep_agent" : "fixture");
      setScenario(graph.scenario ?? sys.scenarios[0]?.id ?? "");
      setFocusText(focus ?? "");
      setBudget({ max_usd: sys.defaults.run_budget_usd, max_tavily_calls: sys.defaults.max_tavily_calls, max_model_calls: sys.defaults.max_model_calls, deadline_minutes: sys.defaults.deadline_minutes });
    });
  }, [open, sys, liveOk, graph.scenario, focus]);

  const start = useMutation({
    mutationFn: () => post<Run>(`/graphs/${graph.id}/runs`, {
      engine, scenario: engine === "fixture" ? scenario : null, focus: focusText.trim() || null, frontier_id: frontierId ?? null,
      budget, confirm_spend: engine === "deep_agent",  // the button states the amount; pressing it is the confirmation
    }),
    onSuccess: (run) => {
      qc.invalidateQueries({ queryKey: ["runs", graph.id] });
      qc.invalidateQueries({ queryKey: ["graph", graph.id] });
      qc.invalidateQueries({ queryKey: ["frontier", graph.id] });
      onOpenChange(false);
      router.push(`/g/${graph.id}?run=${run.id}`);
    },
    onError: toastError,
  });

  const liveWhy = graph.is_synthetic ? "Synthetic graphs never mix with live evidence" : sys?.live_unavailable_reason ?? undefined;
  const simBlocked = !graph.is_synthetic && !!graph.stats.sources && !graph.scenario;
  const option = (id: "fixture" | "deep_agent", icon: React.ReactNode, title: string, disabled: boolean, why?: string) => (
    <button type="button" role="radio" aria-checked={engine === id} disabled={disabled} onClick={() => setEngine(id)} title={disabled ? why : undefined}
      className={cn("flex h-8 flex-1 items-center justify-center gap-2 rounded-md text-[13px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40",
        engine === id ? "bg-panel-3 text-fg shadow-[0_0_0_1px_var(--line-2)]" : "text-fg-3 hover:text-fg")}>
      {icon}{title}
    </button>
  );
  const num = (key: keyof typeof budget, label: string, step = 1) => (
    <Field label={label}>
      <Input type="number" min={0} step={step} value={budget[key]} onChange={(e) => setBudget({ ...budget, [key]: Number(e.target.value) })} />
    </Field>
  );
  const ceiling = sys?.defaults.max_run_budget_usd ?? 25;
  const invalid = engine === "deep_agent" ? budget.max_usd > ceiling || budget.max_usd <= 0 : !scenario;

  return (
    <Dialog open={open} onOpenChange={onOpenChange} wide title="Start research">
      <form onSubmit={(e) => { e.preventDefault(); start.mutate(); }} className="space-y-4">
        <div role="radiogroup" aria-label="Research mode" className="flex gap-1 rounded-lg bg-bg-2 p-1 shadow-[0_0_0_1px_var(--line)]">
          {option("deep_agent", <Globe className="h-3.5 w-3.5" />, "Live", !liveOk, liveWhy)}
          {option("fixture", <FlaskConical className="h-3.5 w-3.5" />, "Simulation", simBlocked, "This graph already holds live evidence")}
        </div>
        {!liveOk && liveWhy && engine === "fixture" && !graph.is_synthetic && <p className="-mt-2 text-[12px] text-fg-3">{liveWhy}</p>}

        {engine === "fixture" && !graph.is_synthetic && (
          <div className="rounded-lg bg-warn-soft p-3 text-[12.5px]">
            <p className="flex items-center gap-1.5 font-medium text-warn"><TriangleAlert className="h-3.5 w-3.5" /> Marks this graph as synthetic, permanently</p>
            <Field label="Scenario" className="mt-2.5">
              <select value={scenario} onChange={(e) => setScenario(e.target.value)} className="h-9 w-full rounded-lg bg-bg-2 px-2 text-[13px] text-fg shadow-[0_0_0_1px_var(--line)_inset]">
                {sys?.scenarios.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </Field>
          </div>
        )}

        <Field label="Focus">
          <Textarea rows={2} value={focusText} onChange={(e) => setFocusText(e.target.value)} placeholder="Optional. One question to narrow this run." />
        </Field>

        <fieldset>
          <legend className="label mb-2">Limits</legend>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {num("max_usd", "Cost (USD)", 0.25)}
            {num("max_tavily_calls", "Web calls")}
            {num("max_model_calls", "Model calls")}
            {num("deadline_minutes", "Minutes")}
          </div>
          {engine === "deep_agent" && budget.max_usd > ceiling && <p className="mt-2 text-[12px] text-warn">The server allows at most {usd(ceiling)} per run.</p>}
        </fieldset>

        <div className="flex items-center justify-end gap-2 pt-1">
          <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button type="submit" variant="primary" loading={start.isPending} disabled={invalid}>
            {engine === "fixture" ? "Start simulation" : <>Start · spend up to <span className="tnum">{usd(budget.max_usd)}</span></>}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
