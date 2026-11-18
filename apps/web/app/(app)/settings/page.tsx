"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleCheck, CircleDashed, CircleX, FlaskConical, LogIn, PlugZap } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, type ReactNode } from "react";
import { Badge, Button, Card, ErrorNote, Skeleton } from "@/components/ui";
import { del, post, type SystemStatus } from "@/lib/api";
import { usd } from "@/lib/format";
import { useMe, useSystem } from "@/lib/hooks";

const ENV: Record<string, string> = { tavily: "TAVILY_API_KEY", anthropic: "ANTHROPIC_API_KEY", openai: "OPENAI_API_KEY", valar: "VALAR_API_KEY" };
const NAMES: Record<string, string> = { tavily: "Tavily", anthropic: "Anthropic", openai: "OpenAI", valar: "Valar" };

export default function SettingsPage() {
  return <Suspense><Settings /></Suspense>;
}

function Settings() {
  const system = useSystem();
  const me = useMe();
  const s = system.data;
  const params = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  // Tavily's sign-in redirects back here with ?tavily=connected|error&reason=…
  const [notice] = useState(() => params.get("tavily") === "connected" ? { ok: true, message: "Signed in with Tavily." }
    : params.get("tavily") === "error" ? { ok: false, message: `Tavily sign-in failed: ${params.get("reason") || "unknown error"}` } : null);
  useEffect(() => {
    if (!params.get("tavily")) return;
    qc.invalidateQueries({ queryKey: ["system"] });
    router.replace("/settings");
  }, [params, qc, router]);
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
      {notice && <Status {...notice} />}
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <ProviderCard id="tavily" configured={s.tavily_configured} note="Required for live research."
          detail={s.tavily_auth === "api_key" ? "Using TAVILY_API_KEY" : s.tavily_auth === "oauth" ? "Signed in with Tavily" : undefined}>
          <TavilyActions auth={s.tavily_auth ?? null} />
        </ProviderCard>
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

function Status({ ok, message }: { ok: boolean; message: string }) {
  return <p role="status" className={`mt-2 flex items-start gap-1.5 text-[12px] ${ok ? "text-ok" : "text-danger"}`}>{ok ? <CircleCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" /> : <CircleX className="mt-0.5 h-3.5 w-3.5 shrink-0" />}{message}</p>;
}

function TavilyActions({ auth }: { auth: SystemStatus["tavily_auth"] }) {
  const qc = useQueryClient();
  const connect = useMutation({
    mutationFn: () => post<{ authorize_url: string }>("/system/tavily/connect", { origin: window.location.origin }),
    onSuccess: (r) => { window.location.href = r.authorize_url; },
  });
  const disconnect = useMutation({ mutationFn: () => del("/system/tavily"), onSuccess: () => qc.invalidateQueries({ queryKey: ["system"] }) });
  const error = connect.error ?? disconnect.error;
  if (auth === "api_key") return null;
  return (
    <>
      {auth === "oauth" ? <Button size="sm" variant="outline" loading={disconnect.isPending} onClick={() => disconnect.mutate()}>Disconnect</Button> : (
        <span className="flex flex-wrap items-center gap-2">
          <Button size="sm" loading={connect.isPending} onClick={() => connect.mutate()}><LogIn className="h-3.5 w-3.5" /> Sign in with Tavily</Button>
          <span className="text-[12px] text-fg-3">or set <code className="font-mono text-[11.5px] text-fg-2">TAVILY_API_KEY</code></span>
        </span>
      )}
      {error && <Status ok={false} message={error.message} />}
    </>
  );
}

function ProviderCard({ id, configured, model, note, detail, primary, children }: { id: string; configured: boolean; model?: string | null; note: string; detail?: string; primary?: boolean; children?: ReactNode }) {
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
      {detail && <p className="mt-1.5 text-[12.5px] text-fg-2">{detail}</p>}
      {model && <p className="mt-1.5 truncate font-mono text-[11.5px] text-fg-3">{model}</p>}
      <div className="mt-auto pt-3">
        {configured ? (
          <span className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" loading={test.isPending} onClick={() => test.mutate()} title="Sends one tiny real request"><PlugZap className="h-3.5 w-3.5" /> Test</Button>
            {children}
          </span>
        ) : children ?? <p className="text-[12px] text-fg-3"><code className="font-mono text-[11.5px] text-fg-2">{ENV[id]}</code></p>}
        {result && <Status {...result} />}
      </div>
    </Card>
  );
}
