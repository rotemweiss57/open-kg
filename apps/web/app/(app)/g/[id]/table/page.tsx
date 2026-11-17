"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, ClipboardCopy, Download, Eye, Search, Table2 } from "lucide-react";
import { useMemo, useState } from "react";
import { useGraphCtx } from "@/components/graph-context";
import { Inspector, type Selection } from "@/components/inspector";
import { toast } from "@/components/toast";
import { Button, cn, Confidence, Empty, ErrorNote, Input, Skeleton, Tip } from "@/components/ui";
import { api, post } from "@/lib/api";
import { typeColor } from "@/lib/colors";
import { fmtValid, fmtValue, humanize, titleize } from "@/lib/format";
import type { components } from "@/lib/schema";

type TableOut = components["schemas"]["TableOut"];
type Row = components["schemas"]["TableRow"];
type Sort = { key: string; dir: 1 | -1 };

const linkLabel = (k: string) => (k.endsWith(" ←") ? `${titleize(k.replace(" ←", "").replace(" (past)", ""))} (from)${k.includes("(past)") ? " · past" : ""}` : titleize(k));
const CONF = { high: 3, medium: 2, low: 1 } as Record<string, number>;
const safeCell = (v: string) => (/^[=+\-@]/.test(v) ? `'${v}` : v);

export default function TablePage() {
  const { graph, startRun } = useGraphCtx();
  const types = graph.ontology.entity_types.map((t) => t.type);
  const [type, setType] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [verifiedOnly, setVerifiedOnly] = useState(false);
  const [sort, setSort] = useState<Sort>({ key: "connections", dir: -1 });
  const [selection, setSelection] = useState<Selection>(null);
  const q = useQuery({
    queryKey: ["table", graph.id, type, verifiedOnly],
    queryFn: () => api<TableOut>(`/graphs/${graph.id}/table?include_proposed=${!verifiedOnly}${type ? `&type=${encodeURIComponent(type)}` : ""}`),
    placeholderData: (p) => p, refetchInterval: 10_000,
  });

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const list = (q.data?.rows ?? []).filter((r) => !needle || r.name.toLowerCase().includes(needle)
      || Object.values(r.cells ?? {}).some((c) => String(c.value).toLowerCase().includes(needle))
      || Object.values(r.links ?? {}).some((l) => l.some((n) => n.toLowerCase().includes(needle))));
    const val = (r: Row): string | number => {
      if (sort.key === "name") return r.name.toLowerCase();
      if (sort.key === "type") return r.type;
      if (sort.key === "connections") return r.connections ?? 0;
      if (sort.key === "sources") return r.sources ?? 0;
      if (sort.key === "confidence") return CONF[r.confidence ?? "medium"];
      if (sort.key.startsWith("link:")) return (r.links?.[sort.key.slice(5)] ?? []).join(", ").toLowerCase();
      return String(r.cells?.[sort.key]?.value ?? "").toLowerCase();
    };
    return [...list].sort((a, b) => { const x = val(a), y = val(b); return (x < y ? -1 : x > y ? 1 : a.name.localeCompare(b.name)) * sort.dir; });
  }, [q.data, query, sort]);

  // With every type mixed together, keep only columns that are reasonably filled.
  const cols = useMemo(() => {
    const all = q.data?.columns ?? [];
    if (type || !q.data) return all;
    const fill = (c: string) => q.data!.rows.filter((r) => r.cells?.[c]).length;
    // Small graphs (an account map of one company) keep single-value columns; large ones drop the long tail.
    const min = q.data!.rows.length > 40 ? 2 : 1;
    return all.filter((c) => fill(c) >= min).sort((a, b) => fill(b) - fill(a)).slice(0, 7);
  }, [q.data, type]);
  const linkCols = useMemo(() => (q.data?.link_columns ?? []).slice(0, type ? 8 : 3), [q.data, type]);
  const matrix = () => {
    const head = ["Name", "Type", "Status", ...cols.map(titleize), ...linkCols.map(linkLabel), "Confidence", "Independent sources", "Connections"];
    const body = rows.map((r) => [r.name, r.type, r.status === "accepted" ? "verified" : "provisional",
      ...cols.map((c) => { const cell = r.cells?.[c]; return cell ? `${cell.value}${cell.status === "proposed" ? " (provisional)" : cell.status === "contested" ? " (contested)" : ""}` : ""; }),
      ...linkCols.map((l) => (r.links?.[l] ?? []).join("; ")), r.confidence ?? "", String(r.sources ?? 0), String(r.connections ?? 0)]);
    return [head, ...body];
  };
  const exportCsv = () => {
    const csv = matrix().map((line) => line.map((v) => `"${safeCell(String(v)).replace(/"/g, '""')}"`).join(",")).join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = `${graph.name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}${type ? `-${type.toLowerCase()}` : ""}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
    toast(`Exported ${rows.length} rows`);
  };
  const copyTsv = async () => {
    await navigator.clipboard.writeText(matrix().map((line) => line.map((v) => safeCell(String(v)).replace(/[\t\n]/g, " ")).join("\t")).join("\n"));
    toast(`Copied ${rows.length} rows. Paste into Sheets or Excel.`);
  };
  // Track a whole list at once: watch every verified row currently shown (accounts, competitors, ...).
  const watchable = rows.filter((r) => r.status === "accepted").slice(0, 25);
  const watchAll = useMutation({
    mutationFn: async () => {
      const results = await Promise.allSettled(watchable.map((r) => post(`/graphs/${graph.id}/monitors`, { entity_id: r.id, question: "Material changes" })));
      return results.filter((r) => r.status === "fulfilled").length;
    },
    onSuccess: (n) => toast(n ? `Watching ${n} more` : "Already watching"),
  });
  const copyMarkdown = async () => {
    const m = matrix().map((line) => line.slice(0, line.length - 1)); // drop the link count
    const esc = (v: string) => v.replace(/\|/g, "\\|").replace(/\n/g, " ");
    const md = [
      `## ${graph.name}${type ? ` — ${type}` : ""}`, "",
      `_${graph.charter.question || graph.description}_`, "",
      `| ${m[0].map(esc).join(" | ")} |`, `| ${m[0].map(() => "---").join(" | ")} |`,
      ...m.slice(1).map((line) => `| ${line.map((v) => esc(String(v)) || "—").join(" | ")} |`), "",
      `${rows.length} rows · ${rows.filter((r) => r.status === "accepted").length} verified, ${rows.filter((r) => r.status !== "accepted").length} provisional. ${graph.is_synthetic ? "SYNTHETIC DEMO DATA. " : ""}${graph.charter.coverage_note || "A sample of what public sources state; not a complete list."}`,
    ].join("\n");
    await navigator.clipboard.writeText(md);
    toast("Copied");
  };
  const th = (k: string, children: React.ReactNode, align?: "right") => (
    <th key={k} scope="col" aria-sort={sort.key === k ? (sort.dir === 1 ? "ascending" : "descending") : "none"} className={cn("whitespace-nowrap border-b border-line bg-panel px-3 py-0 text-left font-normal", align === "right" && "text-right")}>
      <button onClick={() => setSort((s) => ({ key: k, dir: s.key === k ? (s.dir === 1 ? -1 : 1) : (["connections", "sources", "confidence"].includes(k) ? -1 : 1) }))}
        className={cn("label inline-flex h-9 items-center gap-1 hover:text-fg", sort.key === k && "!text-fg")}>
        {children}{sort.key === k && (sort.dir === 1 ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
      </button>
    </th>
  );

  if (q.isError) return <div className="p-6"><ErrorNote error={q.error} retry={() => q.refetch()} /></div>;
  const empty = q.data && q.data.total === 0;
  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-line px-3 py-2 sm:px-4">
          <div className="flex gap-0.5 rounded-lg bg-panel p-0.5 shadow-[0_0_0_1px_var(--line)]" role="tablist" aria-label="Entity type">
            {[null, ...types].map((t) => (
              <button key={t ?? "all"} role="tab" aria-selected={type === t} onClick={() => setType(t)}
                className={cn("inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-[12.5px]", type === t ? "bg-panel-3 text-fg" : "text-fg-3 hover:text-fg")}>
                {t && <span className="h-2 w-2 rounded-full" style={{ background: typeColor(t, graph.ontology) }} />}{t ?? "All"}
              </button>
            ))}
          </div>
          <div className="relative w-[min(240px,50vw)]">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 z-10 h-3.5 w-3.5 -translate-y-1/2 text-fg-3" />
            <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter" aria-label="Filter rows" className="!h-8 pl-8" />
          </div>
          <label className="flex cursor-pointer items-center gap-1.5 text-[12.5px] text-fg-2">
            <input type="checkbox" checked={verifiedOnly} onChange={(e) => setVerifiedOnly(e.target.checked)} className="h-3.5 w-3.5 accent-[var(--accent)]" /> Verified only
          </label>
          <div className="flex-1" />
          <span className="tnum hidden text-[12px] text-fg-3 sm:inline">{rows.length}</span>
          <Tip label={watchable.length ? `Watch the ${watchable.length} verified rows` : "Only verified rows can be watched"}>
            <Button size="sm" variant="outline" onClick={() => watchAll.mutate()} loading={watchAll.isPending} disabled={!watchable.length}><Eye className="h-3.5 w-3.5" /> Watch {watchable.length || ""}</Button>
          </Tip>
          <Tip label="Copy for Sheets or Excel"><Button size="sm" variant="outline" onClick={copyTsv} disabled={!rows.length}><ClipboardCopy className="h-3.5 w-3.5" /> Copy</Button></Tip>
          <Tip label="Copy as Markdown"><Button size="sm" variant="outline" onClick={copyMarkdown} disabled={!rows.length}>Markdown</Button></Tip>
          <Button size="sm" variant="outline" onClick={exportCsv} disabled={!rows.length}><Download className="h-3.5 w-3.5" /> CSV</Button>
        </div>
        {!q.data ? <div className="space-y-2 p-4">{[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-9 w-full" />)}</div>
          : empty ? (
            <div className="grid-bg flex flex-1 items-center justify-center">
              <Empty icon={<Table2 className="h-5 w-5" />} title="No rows yet" action={<Button variant="primary" onClick={() => startRun()}>Start research</Button>}>
                Entities appear here as research records them, one row each, with a column per attribute.</Empty>
            </div>
          ) : (
            <div className="min-h-0 flex-1 overflow-auto">
              <table className="w-max min-w-full border-separate border-spacing-0 text-[13px]">
                <thead className="sticky top-0 z-20">
                  <tr>
                    <th scope="col" className="sticky left-0 z-30 border-b border-r border-line bg-panel px-3 py-0 text-left font-normal">
                      <button onClick={() => setSort((s) => ({ key: "name", dir: s.key === "name" && s.dir === 1 ? -1 : 1 }))} className={cn("label inline-flex h-9 items-center gap-1 hover:text-fg", sort.key === "name" && "!text-fg")}>Name{sort.key === "name" && (sort.dir === 1 ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}</button>
                    </th>
                    {!type && th("type", "Type")}
                    {cols.map((c) => th(c, humanize(c)))}
                    {linkCols.map((l) => th(`link:${l}`, linkLabel(l)))}
                    {th("confidence", "Confidence")}
                    {th("sources", "Sources", "right")}
                    {th("connections", "Links", "right")}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const sel = selection?.id === r.id;
                    const td = cn("border-b border-line px-3 py-2 align-top", sel && "bg-accent-soft");
                    return (
                      <tr key={r.id} onClick={() => setSelection({ kind: "entity", id: r.id })} className="group cursor-pointer hover:bg-panel-2/60">
                        <th scope="row" className={cn("sticky left-0 z-10 max-w-[260px] border-b border-r border-line px-3 py-2 text-left font-normal", sel ? "bg-[color-mix(in_srgb,var(--accent)_14%,var(--bg))]" : "bg-bg group-hover:bg-panel-2")}>
                          <button className="flex w-full min-w-0 max-w-[236px] items-center gap-2 text-left" onClick={(e) => { e.stopPropagation(); setSelection({ kind: "entity", id: r.id }); }}>
                            <span className={cn("h-2 w-2 shrink-0 rounded-full", r.status === "proposed" && "!bg-transparent ring-1 ring-current")} style={{ background: typeColor(r.type, graph.ontology), color: typeColor(r.type, graph.ontology) }} />
                            <span className={cn("truncate pr-1 font-medium", r.status === "proposed" && "italic text-fg-2")} title={`${r.name} · ${r.status === "proposed" ? "provisional" : "verified"}`}>{r.name}</span>
                          </button>
                        </th>
                        {!type && <td className={cn(td, "text-fg-2")}>{r.type}</td>}
                        {cols.map((c) => {
                          const cell = r.cells?.[c];
                          return (
                            <td key={c} className={cn(td, "max-w-[240px]")} title={cell ? `${cell.status === "proposed" ? "Provisional" : cell.status === "contested" ? "Contested" : "Verified"} · ${cell.sources} independent source${cell.sources === 1 ? "" : "s"}${cell.valid_from ? ` · since ${fmtValid(cell.valid_from)}` : ""}` : undefined}>
                              {cell ? (
                                <span className={cn("block truncate", cell.status === "proposed" && "italic text-fg-2 underline decoration-line-2 decoration-dashed underline-offset-4", cell.status === "contested" && "text-warn")}>
                                  {fmtValue(cell.value, c)}{cell.valid_from && <span className="tnum ml-1.5 text-[11px] not-italic text-fg-3 no-underline">{fmtValid(cell.valid_from)}</span>}
                                </span>
                              ) : <span className="text-fg-3">—</span>}
                            </td>
                          );
                        })}
                        {linkCols.map((l) => {
                          const v = r.links?.[l] ?? [];
                          return <td key={l} className={cn(td, "max-w-[260px] text-fg-2")} title={v.join(", ")}><span className="block truncate">{v.length ? v.join(", ") : <span className="text-fg-3">—</span>}</span></td>;
                        })}
                        <td className={td}><Confidence level={r.confidence ?? "medium"} /></td>
                        <td className={cn(td, "tnum text-right text-fg-2")}>{r.sources}</td>
                        <td className={cn(td, "tnum text-right text-fg-2")}>{r.connections}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {rows.length === 0 && <p className="p-8 text-center text-[13px] text-fg-3">No matches</p>}
            </div>
          )}
        <p className="flex shrink-0 items-center gap-4 border-t border-line px-4 py-1.5 text-[11.5px] text-fg-3">
          <span className="italic underline decoration-line-2 decoration-dashed underline-offset-4">Provisional</span>
          <span className="text-warn">Contested</span>
        </p>
      </div>
      {selection && (
        <div className="fixed inset-0 z-50 bg-panel md:static md:z-auto md:w-[400px] md:shrink-0 md:border-l md:border-line xl:w-[430px]">
          <Inspector graph={graph} selection={selection} onSelect={setSelection} onInvestigate={(f) => startRun(f)} />
        </div>
      )}
    </div>
  );
}
