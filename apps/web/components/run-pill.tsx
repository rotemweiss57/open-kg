import { RUN_ACTIVE, RUN_STATUS } from "@/lib/colors";
import { cn } from "./ui";

export function RunPill({ status, className }: { status: string; className?: string }) {
  const meta = RUN_STATUS[status] ?? { label: status, tone: "muted" as const };
  const live = RUN_ACTIVE.includes(status);
  return (
    <span className={cn(
      "inline-flex h-[22px] items-center gap-1.5 rounded-full bg-panel px-2 text-[11.5px] font-medium shadow-[0_0_0_1px_var(--line-2)]",
      meta.tone === "accent" && "text-accent-text", meta.tone === "ok" && "text-ok", meta.tone === "warn" && "text-warn",
      meta.tone === "danger" && "text-danger", meta.tone === "muted" && "text-fg-3", className)}>
      <span className="relative flex h-1.5 w-1.5">
        {live && <span className="pulse-ring absolute inset-0 rounded-full bg-current" />}
        <span className="relative h-1.5 w-1.5 rounded-full bg-current" />
      </span>
      {meta.label}
    </span>
  );
}
