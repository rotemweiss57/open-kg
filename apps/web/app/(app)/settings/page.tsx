"use client";

import { useMutation } from "@tanstack/react-query";
import { CircleCheck, CircleDashed, CircleX, FlaskConical, PlugZap } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Card, ErrorNote, Skeleton } from "@/components/ui";
import { post } from "@/lib/api";
import { usd } from "@/lib/format";
import { useMe, useSystem } from "@/lib/hooks";

const ENV: Record<string, string> = { tavily: "TAVILY_API_KEY", anthropic: "ANTHROPIC_API_KEY", openai: "OPENAI_API_KEY", valar: "VALAR_API_KEY" };
const NAMES: Record<string, string> = { tavily: "Tavily", anthropic: "Anthropic", openai: "OpenAI", valar: "Valar" };

export default function SettingsPage() {
  const system = useSystem();
  const me = useMe();
  const s = system.data;
  if (system.isError) return <div className="p-8"><ErrorNote error={system.error} retry={() => system.refetch()} /></div>;
  if (!s) return <div className="mx-auto max-w-3xl space-y-4 p-8"><Skeleton className="h-10 w-48" /><Skeleton className="h-56 w-full !rounded-xl" /></div>;
  const d = s.defaults;
  return (
    <div className="mx-auto max-w-[920px] px-5 pb-20 pt-10 sm:px-8">
      <p className="label">{me.data?.workspace_name}</p>
      <h1 className="mt-2 font-display text-[40px] leading-none">Settings</h1>

      {s.demo_mode && (
        <Card className="mt-6 flex items-start gap-3 p-4">
          <FlaskConical className="mt-0.5 h-4 w-4 shrink-0 text-accent-text" />
          <div className="text-[13px]"><p className="font-medium">Demo mode is on</p>
            <p className="mt-0.5 text-fg-2">Synthetic graphs and simulated runs. Set <code className="font-mono text-[12px]">DEMO_MODE=false</code> for production.</p></div>
        </Card>
      )}

      <h2 className="mt-10 flex flex-wrap items-center gap-3 font-display text-[26px]">Providers
        <span className="font-sans tracking-normal">{s.live_research_available ? <Badge tone="ok">Live research ready</Badge> : <Badge tone="warn" title={s.live_unavailable_reason ?? undefined}>Live research off</Badge>}</span>
      </h2>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <ProviderCard id="tavily" configured={s.tavily_configured} note={s.tavily_configured ? "" : "Required for live research."} />
        {s.providers.map((p) => <ProviderCard key={p.provider} id={p.provider} configured={p.configured} model={p.model} note={p.note} primary={p.provider === s.primary_provider} />)}
      </div>
      {s.fallbacks.length > 0 && <p className="mt-3 text-[12.5px] text-fg-3">Fallback: <span className="font-mono text-[12px] text-fg-2">{s.fallbacks.join(" → ")}</span></p>}

      {Object.keys(s.role_models ?? {}).length > 0 && (
        <>
          <h2 className="mt-10 font-display text-[26px]">Models</h2>
          <Card className="mt-4 divide-y divide-line text-[13px]">
            {([["planner", "Coordinator", "Plans and delegates"], ["researcher", "Scout", "Finds entities"],
              ["extractor", "Investigator", "Reads pages, records facts"], ["verifier", "Verifier", "Looks for second sources"]] as const).map(([role, name, what]) => (
              <div key={role} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-2.5" title={what}>
                <span className="font-medium">{name}</span>
                <span className="font-mono text-[12px] text-fg-2">{s.role_models?.[role]}</span>
              </div>
            ))}
          </Card>
        </>
      )}

      <h2 className="mt-10 font-display text-[26px]">Limits</h2>
      <Card className="mt-4 grid grid-cols-2 gap-x-6 gap-y-4 p-4 sm:grid-cols-4">
        <Limit label="Cost per run" value={usd(d.run_budget_usd)} hint={`max ${usd(d.max_run_budget_usd)}`} />
        <Limit label="Web calls" value={String(d.max_tavily_calls)} />
        <Limit label="Model calls" value={String(d.max_model_calls)} />
        <Limit label="Time" value={`${d.deadline_minutes} min`} />
      </Card>
      <p className="mt-2 text-[12px] text-fg-3">Costs are estimates from usage.</p>

      <h2 className="mt-10 font-display text-[26px]">System</h2>
      <Card className="mt-4 divide-y divide-line text-[13px]">
        {[
          ["Environment", s.environment],
          ["Database", s.database === "sqlite" ? "SQLite" : "PostgreSQL"],
          ["Worker", s.execution_mode === "embedded" ? (s.worker.embedded_alive ? "Embedded, running" : "Embedded, stopped") : s.execution_mode === "celery" ? "Celery" : "Disabled"],
          ["Registration", s.allow_registration ? "Open" : "Closed"],
          ["API docs", "/api/docs"],
        ].map(([k, v]) => (
          <div key={k} className="flex items-center justify-between gap-4 px-4 py-2.5"><span className="text-fg-2">{k}</span>
            {k === "API docs" && s.environment !== "production" ? <a className="font-mono text-[12px] text-accent-text hover:underline" href="/api/docs" target="_blank" rel="noreferrer">{v}</a> : <span className="font-mono text-[12px]">{v}</span>}</div>
        ))}
      </Card>
    </div>
  );
}

function Limit({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return <div><p className="label">{label}</p><p className="tnum mt-1 text-[19px] font-medium leading-none">{value}</p>{hint && <p className="mt-1 text-[10.5px] text-fg-3">{hint}</p>}</div>;
}

function ProviderCard({ id, configured, model, note, primary }: { id: string; configured: boolean; model?: string | null; note: string; primary?: boolean }) {
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const test = useMutation({ mutationFn: () => post<{ ok: boolean; message: string }>(`/system/providers/${id}/test`), onSuccess: setResult, onError: (e) => setResult({ ok: false, message: e instanceof Error ? e.message : "Test failed" }) });
  return (
    <Card className="flex flex-col p-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-[15px] font-medium">
          {configured ? <CircleCheck className="h-4 w-4 text-ok" /> : <CircleDashed className="h-4 w-4 text-fg-3" />}{NAMES[id]}
        </h3>
        <span className="flex gap-1.5">{primary && <Badge tone="accent">In use</Badge>}{!configured && <Badge>Not set</Badge>}</span>
      </div>
      {note && !configured && <p className="mt-1.5 text-[12.5px] text-fg-2">{note}</p>}
      {model && <p className="mt-1.5 truncate font-mono text-[11.5px] text-fg-3">{model}</p>}
      <div className="mt-auto pt-3">
        {configured ? (
          <Button size="sm" variant="outline" loading={test.isPending} onClick={() => test.mutate()} title="Sends one tiny real request"><PlugZap className="h-3.5 w-3.5" /> Test</Button>
        ) : <p className="text-[12px] text-fg-3"><code className="font-mono text-[11.5px] text-fg-2">{ENV[id]}</code></p>}
        {result && <p role="status" className={`mt-2 flex items-start gap-1.5 text-[12px] ${result.ok ? "text-ok" : "text-danger"}`}>{result.ok ? <CircleCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" /> : <CircleX className="mt-0.5 h-3.5 w-3.5 shrink-0" />}{result.message}</p>}
      </div>
    </Card>
  );
}
