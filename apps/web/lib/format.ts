export function timeAgo(iso?: string | null): string {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 0) return timeUntil(iso);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  if (s < 86400 * 30) return `${Math.round(s / 86400)}d ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function timeUntil(iso?: string | null): string {
  if (!iso) return "not scheduled";
  const s = (new Date(iso).getTime() - Date.now()) / 1000;
  if (s <= 0) return "due now";
  if (s < 3600) return `in ${Math.max(1, Math.round(s / 60))}m`;
  if (s < 86400) return `in ${Math.round(s / 3600)}h`;
  return `in ${Math.round(s / 86400)}d`;
}

export const fmtDate = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "—";

export const fmtDateTime = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";

export const fmtClock = (iso: string) =>
  new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** Valid-time strings are YYYY, YYYY-MM or YYYY-MM-DD. */
export function fmtValid(v?: string | null): string {
  if (!v) return "";
  const [y, m, d] = v.split("-");
  if (!m) return y;
  return d ? `${MONTHS[+m - 1]} ${+d}, ${y}` : `${MONTHS[+m - 1]} ${y}`;
}

export function validRange(from?: string | null, to?: string | null): string {
  if (from && to) return `${fmtValid(from)} – ${fmtValid(to)}`;
  if (from) return `since ${fmtValid(from)}`;
  if (to) return `until ${fmtValid(to)}`;
  return "";
}

export function duration(startIso?: string | null, endIso?: string | null): string {
  if (!startIso) return "0s";
  const s = Math.max(0, ((endIso ? new Date(endIso).getTime() : Date.now()) - new Date(startIso).getTime()) / 1000);
  if (s < 60) return `${Math.floor(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}

export const usd = (n: number) => (n > 0 && n < 0.01 ? "<$0.01" : `$${n.toFixed(2)}`);
export const humanize = (s: string) => s.replace(/_/g, " ").toLowerCase();
export const titleize = (s: string) => humanize(s).replace(/^\w/, (c) => c.toUpperCase());
export const plural = (n: number, one: string, many = one + "s") => `${n} ${n === 1 ? one : many}`;
export const cadence = (min: number) => (min % 1440 === 0 ? (min === 1440 ? "Daily" : `Every ${min / 1440} days`) : min % 60 === 0 ? (min === 60 ? "Hourly" : `Every ${min / 60}h`) : `Every ${min}m`);

/** Display a claim value: big numbers become readable, money predicates get a currency sign. */
export function fmtValue(value: unknown, predicate = ""): string {
  if (value === true) return "Yes";
  if (value === false) return "No";
  const n = typeof value === "number" ? value : typeof value === "string" && /^\d{5,}(\.\d+)?$/.test(value) ? Number(value) : null;
  if (n === null || Math.abs(n) < 10000) return String(value); // years and small counts are shown as written
  const money = /usd|amount|valuation|revenue|funding|raised|price|arr/i.test(predicate);
  const abs = Math.abs(n);
  const short = abs >= 1e9 ? `${+(n / 1e9).toFixed(2)}B` : abs >= 1e6 ? `${+(n / 1e6).toFixed(2)}M` : abs >= 1e4 ? `${+(n / 1e3).toFixed(1)}K` : n.toLocaleString();
  return money ? `$${short}` : short;
}

/** Suggestion titles recorded with raw numbers ("funding amount = 50000000") read better compacted. */
export const prettyTitle = (t: string) => t.replace(/\b\d{7,}\b/g, (m) => fmtValue(Number(m), /amount|usd|valuation|funding|revenue|raised/i.test(t) ? "usd" : ""));

/** Agents write assignments as paragraphs. A heading needs only the first sentence. */
export const firstLine = (t: string | null | undefined, max = 96) => {
  const text = String(t ?? "").replace(/^\s*(?:[A-Za-z-]+\s+)?(?:task|assignment|slice|goal|objective)\s*:\s*/i, "");
  const line = text.split(/\n|(?<=[.:?!])\s/)[0].replace(/[:.]$/, "").trim();
  return line.length > max ? `${line.slice(0, max - 1).trimEnd()}…` : line;
};
