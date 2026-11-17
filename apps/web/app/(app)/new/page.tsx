"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Sparkles, Wand2 } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { CharterEditor, draftProblems, type GraphDraft } from "@/components/charter-editor";
import { toastError } from "@/components/toast";
import { Badge, Button, ErrorNote, Skeleton, SyntheticTag, Textarea } from "@/components/ui";
import { post, type CharterProposal, type Graph } from "@/lib/api";
import { EXAMPLES } from "@/lib/examples";
import { usd } from "@/lib/format";
import { useSystem } from "@/lib/hooks";

// Starting points for common analyst and go-to-market questions. Brackets are meant to be edited.
const PLAYBOOKS: { label: string; who: string; prompt: string }[] = [
  { label: "Account map", who: "Sales, SDR", prompt: "Map [industry] companies in [region] that raised funding in the last 18 months: the companies, their heads of [function] and founders, latest funding round and investors, and notable customers or partners." },
  { label: "Competitive landscape", who: "Strategy, product", prompt: "Map the competitors of [company] in [category]: each vendor, its founders and leadership, funding rounds and investors, acquisitions, and key partnerships." },
  { label: "Market map", who: "Consulting, research", prompt: "Map the [category] market: the vendors, where they are headquartered, founding year, funding stage and investors, and how they partner with or supply one another." },
  { label: "Investor map", who: "Venture, corp dev", prompt: "Map the investors most active in [sector] since 2024: the funds, their partners, the companies they backed, and the rounds they led." },
  { label: "Leadership map", who: "Recruiting, partnerships", prompt: "Map the leadership of [company or companies]: executives and board members, their titles and start dates, previous roles, and the companies they founded or advise." },
  { label: "Supply chain", who: "Operations, risk", prompt: "Map the supply chain for [product or technology]: who develops it, who supplies critical materials or components, which facilities exist, and who partners with or licenses from whom." },
];

function Studio() {
  const params = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const system = useSystem();
  const [prompt, setPrompt] = useState(params.get("prompt") ?? "");
  const [draft, setDraft] = useState<GraphDraft | null>(null);
  const [proposal, setProposal] = useState<CharterProposal | null>(null);
  const scenarioParam = params.get("scenario");

  const propose = useMutation({
    mutationFn: (v: { useModel?: boolean; scenario?: string | null; text?: string }) =>
      post<CharterProposal>(`/charter/propose${v.useModel ? "?use_model=true" : ""}`, { prompt: v.text ?? prompt, scenario: v.scenario ?? null }),
    onSuccess: (p) => {
      const d: GraphDraft = { name: p.name, description: p.description, charter: p.charter, ontology: p.ontology, autonomy_mode: "auto_accept_high", budget_usd: 5 };
      // One-click examples skip the charter screen: create the graph and stop at the spending confirmation.
      if (quick.current && p.generated_by !== "template") { quick.current = false; create.mutate({ draft: d, start: true }); return; }
      setProposal(p);
      setDraft(d);
    },
  });
  const quick = useRef(params.get("quick") === "1");
  const auto = useRef(false);
  useEffect(() => {
    if (!auto.current && prompt.trim().length >= 3 && (params.get("prompt") || scenarioParam)) { auto.current = true; propose.mutate({ scenario: scenarioParam }); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const create = useMutation({
    mutationFn: (v: { draft?: GraphDraft; start?: boolean } | void) =>
      post<Graph>("/graphs", { ...(v?.draft ?? draft), scenario: !v?.draft && proposal?.generated_by === "template" ? proposal.scenario : null }),
    onSuccess: (g, v) => { qc.invalidateQueries({ queryKey: ["graphs"] }); router.push(`/g/${g.id}${v?.start ? "?start=1" : ""}`); },
    onError: toastError,
  });
  const problems = draft ? draftProblems(draft) : [];
  const est = proposal?.estimate as Record<string, number | string> | undefined;
  const modelReady = !!system.data?.primary_provider;

  const brackets = /\[[^\]]+\]/.test(prompt);

  if (!draft) {
    return (
      <div className="mx-auto max-w-3xl px-5 pb-20 pt-12 sm:px-8 sm:pt-16">
        <h1 className="font-display text-[40px] leading-[1.05]">New graph</h1>
        <form className="mt-6" onSubmit={(e) => { e.preventDefault(); propose.mutate({}); }}>
          <Textarea autoFocus rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} aria-label="What do you want to map?"
            onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && prompt.trim().length >= 3 && !brackets) { e.preventDefault(); propose.mutate({}); } }} className="!rounded-xl !px-4 !py-3 text-[15px]"
            placeholder="What do you want to understand?" />
          {propose.isError && <div className="mt-3"><ErrorNote error={propose.error} /></div>}
          <div className="mt-3 flex items-center justify-between gap-3">
            <Button type="button" variant="ghost" onClick={() => router.push("/")}><ArrowLeft className="h-4 w-4" /> Back</Button>
            <div className="flex items-center gap-3">
              {brackets && <span className="text-[12.5px] text-warn">Fill in the [brackets]</span>}
              <Button type="submit" variant="primary" size="lg" loading={propose.isPending} disabled={prompt.trim().length < 3 || brackets}>Continue <ArrowRight className="h-4 w-4" /></Button>
            </div>
          </div>
        </form>
        {propose.isPending && <div className="mt-8 space-y-3"><Skeleton className="h-6 w-1/3" /><Skeleton className="h-32 w-full" /></div>}
        {system.data?.live_research_available && (
          <div className="mt-10">
            <p className="label mb-2.5">Examples</p>
            <div className="grid gap-2 sm:grid-cols-3">
              {EXAMPLES.map((x) => (
                <button key={x.id} type="button" title={x.prompt} disabled={propose.isPending || create.isPending}
                  onClick={() => { quick.current = true; setPrompt(x.prompt); propose.mutate({ text: x.prompt }); }}
                  className="group rounded-xl bg-panel p-3.5 text-left shadow-[0_0_0_1px_var(--line)] transition-[box-shadow,background] hover:bg-panel-2 hover:shadow-[0_0_0_1px_var(--accent-text)]">
                  <span className="flex items-center gap-1.5 text-[13.5px] font-medium"><Sparkles className="h-3.5 w-3.5 text-accent-text" />{x.label}</span>
                  <span className="mt-1 block text-[12px] text-fg-3">{x.who}</span>
                  <span className="mt-2 block text-[12.5px] leading-snug text-fg-2">{x.gets}</span>
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="mt-8">
          <p className="label mb-2.5">Templates</p>
          <div className="grid gap-2 sm:grid-cols-3">
            {PLAYBOOKS.map((b) => (
              <button key={b.label} type="button" onClick={() => setPrompt(b.prompt)} title={b.prompt}
                className="group rounded-xl bg-panel px-3.5 py-3 text-left shadow-[0_0_0_1px_var(--line)] transition-[box-shadow,background] hover:bg-panel-2 hover:shadow-[0_0_0_1px_var(--line-2)]">
                <span className="block text-[13.5px] font-medium">{b.label}</span>
                <span className="mt-0.5 block text-[12px] text-fg-3">{b.who}</span>
              </button>
            ))}
          </div>
        </div>
        {!!system.data?.scenarios.length && (
          <div className="mt-7">
            <p className="label mb-2.5">Demo data</p>
            <div className="flex flex-wrap gap-2">
              {system.data.scenarios.map((s) => (
                <button key={s.id} title={s.description} onClick={() => { setPrompt(s.prompt); propose.mutate({ scenario: s.id }); }}
                  className="inline-flex h-9 items-center gap-2 rounded-xl bg-panel px-3.5 text-[13.5px] font-medium shadow-[0_0_0_1px_var(--line)] transition-[box-shadow,background] hover:bg-panel-2 hover:shadow-[0_0_0_1px_var(--line-2)]">
                  <Sparkles className="h-3.5 w-3.5 text-accent-text" />{s.name}<SyntheticTag />
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1080px] px-5 pb-32 pt-8 sm:px-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-[36px] leading-[1.05]">Charter</h1>
          <p className="mt-2 flex flex-wrap items-center gap-2 text-[13px] text-fg-2">
            {proposal?.generated_by === "template" ? <Badge tone="muted" dashed>Synthetic example</Badge>
              : proposal?.generated_by === "heuristic" ? <Badge>Draft</Badge>
              : <Badge tone="accent">Drafted by {proposal?.generated_by.replace("model:", "")}</Badge>}
            <span className="text-fg-3">Everything is editable.</span>
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={() => { setDraft(null); setProposal(null); }}><ArrowLeft className="h-4 w-4" /> Back</Button>
          {proposal?.generated_by === "heuristic" && (
            <Button variant="outline" disabled={!modelReady} loading={propose.isPending} title={modelReady ? "One paid model call" : "No model provider is configured"}
              onClick={() => propose.mutate({ useModel: true }, { onError: toastError })}><Wand2 className="h-4 w-4" /> Refine with AI</Button>
          )}
        </div>
      </div>
      <div className="mt-8"><CharterEditor draft={draft} onChange={setDraft} /></div>

      <div className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-bg-2/95 backdrop-blur md:left-[56px]">
        <div className="mx-auto flex max-w-[1080px] flex-wrap items-center justify-between gap-3 px-5 py-3 sm:px-8">
          <div className="text-[12.5px] text-fg-3">
            {problems.length > 0 ? <span className="text-warn">{problems[0]}</span>
              : <>Runs are capped at <span className="tnum text-fg-2">{usd(Number(est?.max_usd ?? 1))}</span> each.</>}
          </div>
          <Button variant="primary" size="lg" loading={create.isPending} disabled={problems.length > 0} onClick={() => create.mutate()}>Create graph <ArrowRight className="h-4 w-4" /></Button>
        </div>
      </div>
    </div>
  );
}

export default function Page() {
  return <Suspense><Studio /></Suspense>;
}
