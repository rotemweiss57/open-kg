"use client";

import { useState } from "react";
import { cn } from "./ui";

const FAKE = /\.(synthetic|example|test|invalid|localhost)$|^fixture/i;

/** A source site's icon, served by OpenKG's own API. Falls back to the site's initial. */
export function Favicon({ domain, size = 14, className }: { domain: string; size?: number; className?: string }) {
  const [failed, setFailed] = useState(false);
  const host = (domain || "").replace(/^www\./, "").toLowerCase();
  const real = /^[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(host) && !FAKE.test(host);
  if (!real || failed) {
    return (
      <span aria-hidden className={cn("inline-flex shrink-0 items-center justify-center rounded-[4px] bg-panel-3 font-medium uppercase text-fg-3", className)}
        style={{ width: size, height: size, fontSize: Math.max(8, size * 0.6), lineHeight: 1 }}>{host.charAt(0) || "·"}</span>
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element -- tiny same-origin icon; the image optimiser adds nothing
    <img src={`/api/v1/favicon/${host}`} alt="" width={size} height={size} loading="lazy" decoding="async" onError={() => setFailed(true)}
      className={cn("shrink-0 rounded-[4px] bg-white/90 object-contain", className)} style={{ width: size, height: size }} />
  );
}
