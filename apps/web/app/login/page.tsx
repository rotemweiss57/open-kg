"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, FlaskConical } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Logo } from "@/components/shell";
import { Button, ErrorNote, Field, Input } from "@/components/ui";
import { api, post, setCsrf, type Me } from "@/lib/api";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const pub = useQuery({ queryKey: ["public"], queryFn: () => api<{ demo_mode: boolean; allow_registration: boolean }>("/system/public") });
  const [mode, setMode] = useState<"login" | "register">("login");
  const [form, setForm] = useState({ email: "", password: "", name: "" });
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const done = (me: Me) => {
    setCsrf(me.csrf_token);
    qc.setQueryData(["me"], me);
    const next = params.get("next");
    router.replace(next && next.startsWith("/") && !next.startsWith("//") ? next : "/");
  };
  const submit = async (kind: "demo" | "login" | "register") => {
    setBusy(kind); setError(null);
    try {
      done(await post<Me>(`/auth/${kind}`, kind === "demo" ? {} : form));
    } catch (e) { setError(e); setBusy(null); }
  };

  return (
    <div className="grid min-h-full lg:grid-cols-[1.1fr_1fr]">
      <section className="grid-bg relative hidden flex-col justify-between overflow-hidden border-r border-line bg-bg-2 p-12 lg:flex">
        <div className="flex items-center gap-2.5 text-[15px] font-medium"><Logo /> OpenKG</div>
        <div className="max-w-lg">
          <h1 className="font-display text-[60px] leading-[1.0]">Research that <em className="text-accent-text">shows its work</em>.</h1>
          <p className="mt-5 max-w-md text-[16px] leading-relaxed text-fg-2">Agents research the open web and build a graph where every fact opens to its source.</p>
        </div>
        <p className="text-[12.5px] text-fg-3">Open source · self-hosted</p>
      </section>
      <section className="flex items-center justify-center p-6">
        <div className="w-full max-w-sm">
          <div className="mb-8 flex items-center gap-2.5 text-[15px] font-medium lg:hidden"><Logo /> OpenKG</div>
          <h2 className="font-display text-[34px]">{mode === "login" ? "Sign in" : "Create account"}</h2>

          {pub.data?.demo_mode && (
            <>
              <Button variant="primary" size="lg" className="mt-6 w-full" loading={busy === "demo"} onClick={() => submit("demo")} title="Synthetic graphs and simulated runs. No keys needed.">
                <FlaskConical className="h-4 w-4" /> Enter demo <ArrowRight className="h-4 w-4" />
              </Button>
              <div className="mt-6 flex items-center gap-3 text-[11.5px] text-fg-3"><span className="h-px flex-1 bg-line" />or<span className="h-px flex-1 bg-line" /></div>
            </>
          )}

          <form className="mt-5 space-y-3.5" onSubmit={(e) => { e.preventDefault(); submit(mode); }}>
            {mode === "register" && (
              <Field label="Name"><Input autoComplete="name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
            )}
            <Field label="Email"><Input type="email" required autoComplete="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
            <Field label="Password" hint={mode === "register" ? "10+ characters" : undefined}>
              <Input type="password" required minLength={mode === "register" ? 10 : 1} autoComplete={mode === "login" ? "current-password" : "new-password"}
                value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
            </Field>
            {error != null && <ErrorNote error={error} />}
            <Button type="submit" variant={pub.data?.demo_mode ? "secondary" : "primary"} className="w-full" loading={busy === mode}>
              {mode === "login" ? "Sign in" : "Create account"}
            </Button>
          </form>
          {pub.data?.allow_registration && (
            <button className="mt-4 text-[13px] text-fg-2 underline-offset-4 hover:text-fg hover:underline" onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(null); }}>
              {mode === "login" ? "Create account" : "Sign in instead"}
            </button>
          )}
        </div>
      </section>
    </div>
  );
}

export default function LoginPage() {
  return <Suspense><LoginForm /></Suspense>;
}
