"use client";

import { Check, GitMerge, X } from "lucide-react";
import type { Suggestion } from "@/lib/api";
import { prettyTitle } from "@/lib/format";
import { useDecide } from "@/lib/hooks";
import { toast, toastError } from "./toast";
import { Button } from "./ui";

export function approveLabel(s: Suggestion): string {
  if (s.kind === "merge") return "Merge";
  if (s.kind === "contradiction") return "Use new";
  if (s.kind === "claim" && s.payload.change === "supersede") return "Accept";
  return "Approve";
}
export function rejectLabel(s: Suggestion): string {
  if (s.kind === "merge") return "Keep separate";
  if (s.kind === "contradiction") return "Keep current";
  return "Reject";
}

export function SuggestionActions({ s, gid, size = "sm", onDone }: { s: Suggestion; gid: string; size?: "sm" | "md"; onDone?: () => void }) {
  const decide = useDecide(gid);
  const act = (action: "approve" | "reject") =>
    decide.mutate({ id: s.id, action }, {
      onSuccess: () => { toast(action === "approve" ? "Approved" : "Rejected"); onDone?.(); },
      onError: toastError,
    });
  return (
    <div className="flex items-center gap-1.5">
      <Button size={size} variant="primary" loading={decide.isPending && decide.variables?.action === "approve"} disabled={decide.isPending} onClick={() => act("approve")}>
        {s.kind === "merge" ? <GitMerge className="h-3.5 w-3.5" /> : <Check className="h-3.5 w-3.5" />} {approveLabel(s)}
      </Button>
      <Button size={size} variant="outline" loading={decide.isPending && decide.variables?.action === "reject"} disabled={decide.isPending} onClick={() => act("reject")}>
        <X className="h-3.5 w-3.5" /> {rejectLabel(s)}
      </Button>
    </div>
  );
}

/** Suggestion titles come from the server as sentences ("Add Person: Jane Doe", "A —works at→ B").
 *  Show the name first and the kind quietly, so a list scans by entity rather than by verb. */
export function SuggestionTitle({ title }: { title: string }) {
  const t = prettyTitle(title);
  const add = t.match(/^Add ([^:]+): (.+)$/);
  if (add) return <><span>{add[2]}</span> <span className="text-fg-3">{add[1]}</span></>;
  const link = t.match(/^(.+?) —(.+?)→ (.+)$/);
  if (link) return <><span>{link[1]}</span> <span className="text-fg-3">{link[2]}</span> <span>{link[3]}</span></>;
  const dup = t.match(/^Possible duplicate: (.+)$/);
  if (dup) return <><span>{dup[1]}</span> <span className="text-fg-3">duplicate?</span></>;
  const conflict = t.match(/^Conflict on (.+)$/);
  if (conflict) return <><span>{conflict[1]}</span> <span className="text-warn">conflict</span></>;
  return <>{t}</>;
}
