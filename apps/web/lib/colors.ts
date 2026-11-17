import type { Ontology } from "./api";

const PALETTE = ["sky", "amber", "violet", "lime", "rose", "teal", "slate"] as const;

/** Stable colour per entity type: the ontology's declared colour, else assigned by position. */
export function typeColor(type: string, ontology?: Ontology | null): string {
  const types = ontology?.entity_types ?? [];
  const idx = types.findIndex((t) => t.type === type);
  const declared = idx >= 0 ? types[idx].color : "";
  const name = (PALETTE as readonly string[]).includes(declared ?? "") ? declared : PALETTE[(idx >= 0 ? idx : hash(type)) % PALETTE.length];
  return `var(--c-${name})`;
}

function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h;
}

export const RUN_ACTIVE = ["queued", "planning", "researching", "verifying", "resuming", "pausing", "cancel_requested"];
export const RUN_STATUS: Record<string, { label: string; tone: "accent" | "warn" | "danger" | "muted" | "ok" }> = {
  queued: { label: "Queued", tone: "accent" },
  planning: { label: "Planning", tone: "accent" },
  researching: { label: "Researching", tone: "accent" },
  verifying: { label: "Verifying", tone: "accent" },
  resuming: { label: "Resuming", tone: "accent" },
  pausing: { label: "Pausing…", tone: "warn" },
  paused: { label: "Paused", tone: "warn" },
  cancel_requested: { label: "Cancelling…", tone: "warn" },
  cancelled: { label: "Cancelled", tone: "muted" },
  awaiting_review: { label: "Awaiting review", tone: "ok" },
  completed: { label: "Completed", tone: "ok" },
  failed: { label: "Failed", tone: "danger" },
  budget_exhausted: { label: "Budget reached", tone: "warn" },
};
