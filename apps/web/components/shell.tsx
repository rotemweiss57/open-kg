"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Command } from "cmdk";
import { Bell, FlaskConical, LibraryBig, LogOut, Moon, Plus, Search, Settings, Sun } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Dialog as RDialog, Popover } from "radix-ui";
import { useEffect, useState } from "react";
import { api, post, type SearchHit } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { useGraphs, useMe, useNotifications, useSystem, useTheme } from "@/lib/hooks";
import { cn, Kbd, Skeleton, Tip } from "./ui";

export function Logo({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M6 17.5 12 5.5l6 12M8.3 13.5h7.4" stroke="var(--fg-3)" strokeWidth="1.3" strokeLinecap="round" />
      <circle cx="12" cy="5.5" r="2.6" fill="var(--accent)" />
      <circle cx="6" cy="17.5" r="2.2" fill="var(--fg)" />
      <circle cx="18" cy="17.5" r="2.2" fill="none" stroke="var(--fg)" strokeWidth="1.3" strokeDasharray="2 1.6" />
    </svg>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const me = useMe();
  const router = useRouter();
  const path = usePathname();
  const system = useSystem();
  const { theme, toggle } = useTheme();
  const [palette, setPalette] = useState(false);
  const qc = useQueryClient();

  useEffect(() => {
    // Keep the query string, so a shared link to an example still opens it after sign-in.
    const target = path + (typeof window !== "undefined" ? window.location.search : "");
    if (me.isError) router.replace(`/login${target && target !== "/" ? `?next=${encodeURIComponent(target)}` : ""}`);
  }, [me.isError, router, path]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette((v) => !v); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!me.data) {
    return (
      <div className="flex h-full items-center justify-center" role="status" aria-label="Loading workspace">
        <div className="flex items-center gap-3 text-fg-3"><Logo /><Skeleton className="h-3 w-28" /></div>
      </div>
    );
  }
  const logout = async () => { await post("/auth/logout"); qc.clear(); router.replace("/login"); };
  const nav = [
    { href: "/", label: "Library", icon: LibraryBig, active: path === "/" || path.startsWith("/g/") },
    { href: "/new", label: "New graph", icon: Plus, active: path === "/new" },
    { href: "/settings", label: "Settings", icon: Settings, active: path === "/settings" },
  ];
  return (
    <div className="flex h-full flex-col md:flex-row">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-[100] focus:rounded-md focus:bg-accent focus:px-3 focus:py-1.5 focus:text-accent-ink">Skip to content</a>
      <nav aria-label="Primary" className="z-30 flex shrink-0 items-center gap-1 border-b border-line bg-bg-2 px-2 py-1.5 md:w-[56px] md:flex-col md:border-b-0 md:border-r md:px-0 md:py-3">
        <Link href="/" aria-label="OpenKG home" className="flex h-9 w-9 items-center justify-center rounded-lg md:mb-3"><Logo /></Link>
        {nav.map((n) => (
          <Tip key={n.href} label={n.label} side="right">
            <Link href={n.href} aria-label={n.label} aria-current={n.active ? "page" : undefined}
              className={cn("relative flex h-9 w-9 items-center justify-center rounded-lg text-fg-3 transition-colors hover:bg-panel-2 hover:text-fg", n.active && "bg-panel-2 !text-fg")}>
              <n.icon className="h-[18px] w-[18px]" strokeWidth={1.7} />
              {n.active && <span className="absolute -left-[10px] hidden h-4 w-[3px] rounded-r bg-accent md:block" />}
            </Link>
          </Tip>
        ))}
        <Tip label={<span className="flex items-center gap-1.5">Search <Kbd>⌘K</Kbd></span>} side="right">
          <button aria-label="Open command palette" onClick={() => setPalette(true)} className="flex h-9 w-9 items-center justify-center rounded-lg text-fg-3 hover:bg-panel-2 hover:text-fg">
            <Search className="h-[18px] w-[18px]" strokeWidth={1.7} />
          </button>
        </Tip>
        <div className="flex-1" />
        {system.data?.demo_mode && (
          <Tip label="Demo mode" side="right">
            <span className="flex h-9 w-9 items-center justify-center text-fg-3" aria-label="Demo mode"><FlaskConical className="h-[17px] w-[17px]" strokeWidth={1.7} /></span>
          </Tip>
        )}
        <Notifications />
        <Tip label={theme === "dark" ? "Light" : "Dark"} side="right">
          <button aria-label="Toggle theme" onClick={toggle} className="flex h-9 w-9 items-center justify-center rounded-lg text-fg-3 hover:bg-panel-2 hover:text-fg">
            {theme === "dark" ? <Sun className="h-[17px] w-[17px]" strokeWidth={1.7} /> : <Moon className="h-[17px] w-[17px]" strokeWidth={1.7} />}
          </button>
        </Tip>
        <Tip label="Sign out" side="right">
          <button aria-label="Sign out" onClick={logout} className="flex h-9 w-9 items-center justify-center rounded-lg text-fg-3 hover:bg-panel-2 hover:text-fg">
            <LogOut className="h-[17px] w-[17px]" strokeWidth={1.7} />
          </button>
        </Tip>
      </nav>
      <main id="main" className="min-h-0 min-w-0 flex-1 overflow-y-auto">{children}</main>
      <CommandPalette open={palette} onOpenChange={setPalette} toggleTheme={toggle} />
    </div>
  );
}

function Notifications() {
  const { data, refetch } = useNotifications();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const unread = data?.filter((n) => !n.read_at).length ?? 0;
  const markRead = async () => { await post("/notifications/read"); refetch(); };
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Tip label="Changes" side="right">
        <Popover.Trigger asChild>
          <button aria-label={`Notifications${unread ? `, ${unread} unread` : ""}`} className="relative flex h-9 w-9 items-center justify-center rounded-lg text-fg-3 hover:bg-panel-2 hover:text-fg">
            <Bell className="h-[17px] w-[17px]" strokeWidth={1.7} />
            {unread > 0 && <span className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-accent ring-2 ring-bg-2" />}
          </button>
        </Popover.Trigger>
      </Tip>
      <Popover.Portal>
        <Popover.Content side="right" align="end" sideOffset={10} collisionPadding={12} className="z-[70] w-[min(360px,calc(100vw-24px))] rounded-xl bg-panel p-1.5 shadow-panel shadow-[0_0_0_1px_var(--line-2)] anim-fade-up">
          <div className="flex items-center justify-between px-2.5 py-2">
            <span className="label">Changes</span>
            {unread > 0 && <button onClick={markRead} className="text-[12px] text-fg-2 hover:text-fg">Mark read</button>}
          </div>
          {!data?.length && <p className="px-2.5 pb-4 pt-1 text-[13px] text-fg-3">Nothing yet</p>}
          <ul className="max-h-[50dvh] overflow-y-auto">
            {data?.map((n) => (
              <li key={n.id}>
                <button onClick={() => { setOpen(false); router.push(`/g/${n.graph_id}/changes`); }}
                  className="flex w-full gap-2.5 rounded-lg px-2.5 py-2 text-left hover:bg-panel-2">
                  <span className={cn("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", n.read_at ? "bg-line-2" : "bg-accent")} />
                  <span className="min-w-0">
                    <span className="block text-[13px] leading-snug">{n.title}</span>
                    <span className="mt-0.5 block truncate text-[11.5px] text-fg-3">{n.graph_name} · {timeAgo(n.created_at)}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

function CommandPalette({ open, onOpenChange, toggleTheme }: { open: boolean; onOpenChange: (v: boolean) => void; toggleTheme: () => void }) {
  const router = useRouter();
  const graphs = useGraphs();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  useEffect(() => {
    if (q.trim().length < 2) { queueMicrotask(() => setHits([])); return; }
    const t = setTimeout(() => api<SearchHit[]>(`/search?q=${encodeURIComponent(q.trim())}`).then(setHits).catch(() => setHits([])), 160);
    return () => clearTimeout(t);
  }, [q]);
  const go = (href: string) => { onOpenChange(false); setQ(""); router.push(href); };
  const item = "flex cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] text-fg-2 data-[selected=true]:bg-panel-2 data-[selected=true]:text-fg";
  return (
    <RDialog.Root open={open} onOpenChange={onOpenChange}>
      <RDialog.Portal>
        <RDialog.Overlay className="fixed inset-0 z-[60] bg-black/55 backdrop-blur-[2px]" />
        <RDialog.Content aria-describedby={undefined} className="fixed left-1/2 top-[14dvh] z-[61] w-[calc(100vw-24px)] max-w-xl -translate-x-1/2 overflow-hidden rounded-2xl bg-panel shadow-panel shadow-[0_0_0_1px_var(--line-2)] anim-fade-up">
          <RDialog.Title className="sr-only">Command palette</RDialog.Title>
          <Command shouldFilter label="Command palette">
            <div className="flex items-center gap-2.5 border-b border-line px-4">
              <Search className="h-4 w-4 text-fg-3" />
              <Command.Input value={q} onValueChange={setQ} placeholder="Search graphs and entities" className="h-12 flex-1 bg-transparent text-[14px] outline-none placeholder:text-fg-3" />
              <Kbd>esc</Kbd>
            </div>
            <Command.List className="max-h-[52dvh] overflow-y-auto p-1.5">
              <Command.Empty className="px-3 py-6 text-center text-[13px] text-fg-3">No matches</Command.Empty>
              {hits.length > 0 && (
                <Command.Group heading={<span className="label px-2.5">Entities</span>}>
                  {hits.map((h) => (
                    <Command.Item key={h.id} value={`${h.name} ${h.type} ${h.graph_name}`} onSelect={() => go(`/g/${h.graph_id}?entity=${h.id}`)} className={item}>
                      <span className="h-2 w-2 rounded-full bg-fg-3" />
                      <span className="text-fg">{h.name}</span>
                      <span className="text-fg-3">{h.type} · {h.graph_name}</span>
                    </Command.Item>
                  ))}
                </Command.Group>
              )}
              <Command.Group heading={<span className="label px-2.5">Graphs</span>}>
                {graphs.data?.map((g) => (
                  <Command.Item key={g.id} value={`graph ${g.name}`} onSelect={() => go(`/g/${g.id}`)} className={item}>
                    <LibraryBig className="h-4 w-4 shrink-0" /> <span className="truncate text-fg">{g.name}</span>
                    <span className="tnum ml-auto shrink-0 text-fg-3">{g.stats.entities + g.stats.proposed_entities}</span>
                  </Command.Item>
                ))}
              </Command.Group>
              <Command.Group heading={<span className="label px-2.5">Commands</span>}>
                <Command.Item value="new graph create" onSelect={() => go("/new")} className={item}><Plus className="h-4 w-4" /> New graph</Command.Item>
                <Command.Item value="settings providers keys" onSelect={() => go("/settings")} className={item}><Settings className="h-4 w-4" /> Settings</Command.Item>
                <Command.Item value="toggle theme dark light" onSelect={() => { toggleTheme(); onOpenChange(false); }} className={item}><Sun className="h-4 w-4" /> Toggle theme</Command.Item>
              </Command.Group>
            </Command.List>
          </Command>
        </RDialog.Content>
      </RDialog.Portal>
    </RDialog.Root>
  );
}
