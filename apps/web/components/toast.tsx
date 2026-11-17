"use client";

import { Check, CircleAlert } from "lucide-react";
import { useEffect, useState } from "react";

type Toast = { id: number; message: string; tone: "ok" | "error" };
let listeners: ((t: Toast) => void)[] = [];
let nextId = 1;

export function toast(message: string, tone: "ok" | "error" = "ok") {
  listeners.forEach((l) => l({ id: nextId++, message, tone }));
}
export const toastError = (e: unknown) => toast(e instanceof Error ? e.message : "Something went wrong", "error");

export function Toaster() {
  const [items, setItems] = useState<Toast[]>([]);
  useEffect(() => {
    const l = (t: Toast) => {
      setItems((prev) => [...prev.slice(-3), t]);
      setTimeout(() => setItems((prev) => prev.filter((x) => x.id !== t.id)), t.tone === "error" ? 6000 : 3200);
    };
    listeners.push(l);
    return () => { listeners = listeners.filter((x) => x !== l); };
  }, []);
  return (
    <div aria-live="polite" className="pointer-events-none fixed bottom-4 left-1/2 z-[90] flex -translate-x-1/2 flex-col items-center gap-2">
      {items.map((t) => (
        <div key={t.id} role="status" className="anim-fade-up pointer-events-auto flex max-w-[90vw] items-center gap-2 rounded-full bg-panel-3 py-2 pl-3 pr-4 text-[13px] shadow-panel shadow-[0_0_0_1px_var(--line-2)]">
          {t.tone === "ok" ? <Check className="h-4 w-4 text-ok" /> : <CircleAlert className="h-4 w-4 text-danger" />}
          {t.message}
        </div>
      ))}
    </div>
  );
}
