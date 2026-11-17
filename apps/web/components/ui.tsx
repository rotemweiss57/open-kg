"use client";

import clsx from "clsx";
import { Loader2, X } from "lucide-react";
import { Dialog as RDialog, Tooltip as RTooltip } from "radix-ui";
import { forwardRef } from "react";

export const cn = clsx;

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger" | "outline";
  size?: "sm" | "md" | "lg" | "icon";
  loading?: boolean;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", loading, className, children, disabled, ...rest }, ref) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        "inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg font-medium transition-[background,color,box-shadow,transform] duration-150 active:scale-[0.98] disabled:pointer-events-none disabled:opacity-45",
        size === "sm" && "h-7 px-2.5 text-[12.5px]",
        size === "md" && "h-9 px-3.5 text-[13px]",
        size === "lg" && "h-11 px-5 text-sm",
        size === "icon" && "h-8 w-8",
        variant === "primary" && "bg-accent text-accent-ink hover:brightness-110 shadow-[0_0_0_1px_rgba(0,0,0,0.15)_inset]",
        variant === "secondary" && "bg-panel-2 text-fg hover:bg-panel-3 shadow-[0_0_0_1px_var(--line)_inset]",
        variant === "outline" && "text-fg hover:bg-panel-2 shadow-[0_0_0_1px_var(--line-2)_inset]",
        variant === "ghost" && "text-fg-2 hover:bg-panel-2 hover:text-fg",
        variant === "danger" && "bg-danger-soft text-danger hover:brightness-125 shadow-[0_0_0_1px_var(--danger-soft)_inset]",
        className)}
      {...rest}
    >
      {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
      {children}
    </button>
  );
});

type Tone = "neutral" | "accent" | "ok" | "warn" | "danger" | "info" | "muted";
const TONES: Record<Tone, string> = {
  neutral: "bg-panel-2 text-fg-2 shadow-[0_0_0_1px_var(--line)_inset]",
  muted: "text-fg-3 shadow-[0_0_0_1px_var(--line)_inset]",
  accent: "bg-accent-soft text-accent-text",
  ok: "bg-[color-mix(in_srgb,var(--ok)_14%,transparent)] text-ok",
  warn: "bg-warn-soft text-warn",
  danger: "bg-danger-soft text-danger",
  info: "bg-[color-mix(in_srgb,var(--info)_14%,transparent)] text-info",
};

export function Badge({ tone = "neutral", children, className, dashed, title }: {
  tone?: Tone; children: React.ReactNode; className?: string; dashed?: boolean; title?: string }) {
  return (
    <span title={title} className={cn(
      "inline-flex h-[20px] items-center gap-1 whitespace-nowrap rounded-md px-1.5 text-[11px] font-medium leading-none",
      TONES[tone], dashed && "!bg-transparent !shadow-none border border-dashed border-line-2", className)}>
      {children}
    </span>
  );
}

/** One vocabulary for trust state everywhere in the product. */
export function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { tone: Tone; label: string; dashed?: boolean; title: string }> = {
    accepted: { tone: "ok", label: "Verified", title: "Accepted into the graph after review or policy" },
    proposed: { tone: "muted", label: "Provisional", dashed: true, title: "Suggested by research; not yet reviewed" },
    contested: { tone: "warn", label: "Contested", title: "Sources disagree; awaiting a reviewer" },
    superseded: { tone: "neutral", label: "Superseded", title: "Replaced by a newer dated claim; kept as history" },
    retracted: { tone: "neutral", label: "Retracted", title: "A reviewer sided with conflicting evidence" },
    rejected: { tone: "danger", label: "Rejected", title: "Rejected by a reviewer; kept for audit" },
    merged: { tone: "neutral", label: "Merged", title: "Merged into another entity" },
  };
  const m = map[status] ?? { tone: "neutral" as Tone, label: status, title: status };
  return <Badge tone={m.tone} dashed={m.dashed} title={m.title}>{m.label}</Badge>;
}

export function Confidence({ level, sources }: { level: string; sources?: number }) {
  const n = level === "high" ? 3 : level === "medium" ? 2 : 1;
  const label = `${level[0].toUpperCase()}${level.slice(1)} confidence${sources !== undefined ? ` · ${sources} independent source${sources === 1 ? "" : "s"}` : ""}`;
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] text-fg-3" title={label}>
      <span className="flex items-end gap-[2px]" aria-hidden>
        {[1, 2, 3].map((i) => (
          <span key={i} className={cn("w-[3px] rounded-sm", i <= n ? (level === "low" ? "bg-warn" : "bg-fg-2") : "bg-line-2")} style={{ height: 3 + i * 3 }} />
        ))}
      </span>
      <span className="sr-only">{label}</span>
      <span aria-hidden>{level}</span>
    </span>
  );
}

export function Card({ className, children, ...rest }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-xl bg-panel shadow-[0_0_0_1px_var(--line)]", className)} {...rest}>{children}</div>;
}

export const Input = forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...rest }, ref) {
  return <input ref={ref} className={cn(
    "h-9 w-full rounded-lg bg-bg-2 px-3 text-[13px] text-fg placeholder:text-fg-3 shadow-[0_0_0_1px_var(--line)_inset] outline-none transition-shadow focus:shadow-[0_0_0_1.5px_var(--accent-text)_inset]",
    className)} {...rest} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea({ className, ...rest }, ref) {
  return <textarea ref={ref} className={cn(
    "w-full resize-none rounded-lg bg-bg-2 px-3 py-2 text-[13px] leading-relaxed text-fg placeholder:text-fg-3 shadow-[0_0_0_1px_var(--line)_inset] outline-none transition-shadow focus:shadow-[0_0_0_1.5px_var(--accent-text)_inset]",
    className)} {...rest} />;
});

export function Field({ label, hint, children, className }: { label: string; hint?: string; children: React.ReactNode; className?: string }) {
  return (
    <label className={cn("block", className)}>
      <span className="label mb-1.5 block">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11.5px] text-fg-3">{hint}</span>}
    </label>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton", className)} aria-hidden />;
}

export function Kbd({ children }: { children: React.ReactNode }) {
  return <kbd className="inline-flex h-[18px] min-w-[18px] items-center justify-center rounded border border-line-2 bg-panel-2 px-1 font-mono text-[10px] text-fg-2">{children}</kbd>;
}

export function Tip({ label, children, side = "top" }: { label: React.ReactNode; children: React.ReactNode; side?: "top" | "right" | "bottom" | "left" }) {
  return (
    <RTooltip.Root>
      <RTooltip.Trigger asChild>{children}</RTooltip.Trigger>
      <RTooltip.Portal>
        <RTooltip.Content side={side} sideOffset={6} className="z-[80] max-w-[260px] rounded-md bg-panel-3 px-2 py-1 text-[11.5px] text-fg shadow-panel anim-fade-up">
          {label}
        </RTooltip.Content>
      </RTooltip.Portal>
    </RTooltip.Root>
  );
}

export function Dialog({ open, onOpenChange, title, description, children, wide }: {
  open: boolean; onOpenChange: (v: boolean) => void; title: string; description?: string; children: React.ReactNode; wide?: boolean }) {
  return (
    <RDialog.Root open={open} onOpenChange={onOpenChange}>
      <RDialog.Portal>
        <RDialog.Overlay className="fixed inset-0 z-[60] bg-black/55 backdrop-blur-[2px] anim-fade-up" />
        <RDialog.Content className={cn(
          "fixed left-1/2 top-1/2 z-[61] max-h-[88dvh] w-[calc(100vw-24px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl bg-panel p-5 shadow-panel shadow-[0_0_0_1px_var(--line-2)] sm:p-6",
          wide ? "max-w-2xl" : "max-w-md")}>
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <RDialog.Title className="font-display text-[24px] leading-tight">{title}</RDialog.Title>
              {description && <RDialog.Description className="mt-1 text-[13px] text-fg-2">{description}</RDialog.Description>}
            </div>
            <RDialog.Close asChild>
              <Button variant="ghost" size="icon" aria-label="Close"><X className="h-4 w-4" /></Button>
            </RDialog.Close>
          </div>
          {children}
        </RDialog.Content>
      </RDialog.Portal>
    </RDialog.Root>
  );
}

export function Empty({ icon, title, children, action }: { icon?: React.ReactNode; title: string; children?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      {icon && <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-full text-fg-3 shadow-[0_0_0_1px_var(--line-2)]">{icon}</div>}
      <h3 className="font-display text-2xl">{title}</h3>
      {children && <p className="mt-1.5 max-w-sm text-[13px] text-fg-2">{children}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function ErrorNote({ error, retry }: { error: unknown; retry?: () => void }) {
  const msg = error instanceof Error ? error.message : "Something went wrong.";
  return (
    <div role="alert" className="flex items-center justify-between gap-3 rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">
      <span>{msg}</span>
      {retry && <Button size="sm" variant="ghost" onClick={retry} className="!text-danger">Try again</Button>}
    </div>
  );
}

export function SyntheticTag({ className }: { className?: string }) {
  return (
    <Tip label="Fictional demo data, not real research">
      <span className={cn("inline-flex h-[20px] items-center rounded-md border border-dashed border-line-2 px-1.5 text-[11px] font-medium text-fg-3", className)}>
        Synthetic
      </span>
    </Tip>
  );
}

export function Meter({ value, max, tone = "accent", label }: { value: number; max: number; tone?: "accent" | "warn" | "danger"; label: string }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  const t = pct >= 100 ? "danger" : pct >= 80 ? "warn" : tone;
  return (
    <div role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={max} aria-valuenow={value} className="h-1 w-full overflow-hidden rounded-full bg-line">
      <div className={cn("h-full rounded-full transition-[width] duration-500", t === "accent" && "bg-accent-text", t === "warn" && "bg-warn", t === "danger" && "bg-danger")} style={{ width: `${pct}%` }} />
    </div>
  );
}
