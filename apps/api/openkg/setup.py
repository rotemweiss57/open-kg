"""`openkg setup` (guided configuration) and `openkg doctor` (health check).

Both are safe to run repeatedly. Neither ever prints a key: input is hidden, and status lines
say only "set" or "missing". Key checks use each provider's free, read-only endpoints.
"""
from __future__ import annotations

import getpass
import os
import re
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import httpx

TAVILY_URL = "https://app.tavily.com"
PROVIDERS = {
    "valar": {"label": "Valar", "env": "VALAR_API_KEY", "url": "https://docs.valarhq.ai/quickstart",
              "blurb": "open models, lowest cost; measured per-role defaults"},
    "anthropic": {"label": "Anthropic", "env": "ANTHROPIC_API_KEY", "url": "https://console.anthropic.com/settings/keys",
                  "blurb": "Claude models"},
    "openai": {"label": "OpenAI", "env": "OPENAI_API_KEY", "url": "https://platform.openai.com/api-keys",
               "blurb": "GPT models"},
}
OK, WARN, FAIL = "ok", "warn", "fail"
MARK = {OK: "✓", WARN: "!", FAIL: "✗"}


# --- locating and editing the env file ----------------------------------------------------------

def repo_root() -> Path:
    """The checkout root (the directory holding docker-compose.yml)."""
    for base in (Path.cwd(), *Path.cwd().parents, *Path(__file__).resolve().parents):
        if (base / "docker-compose.yml").exists() and (base / "apps").is_dir():
            return base
    return Path.cwd()


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
        if m:
            out[m.group(1)] = _unquote(m.group(2))
    return out


def _unquote(raw: str) -> str:
    v = raw.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return re.sub(r"\s+#.*$", "", v).strip()


def write_env(path: Path, updates: dict[str, str], template: Path | None = None) -> None:
    """Update keys in place, keeping comments and unrelated lines. Creates the file from the
    template on first use and restricts it to the current user."""
    if path.exists():
        lines = path.read_text().splitlines()
    elif template and template.exists():
        lines = template.read_text().splitlines()
    else:
        lines = []
    pending = dict(updates)
    for i, line in enumerate(lines):
        m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m and m.group(1) in pending:
            lines[i] = f"{m.group(1)}={pending.pop(m.group(1))}"
    lines += [f"{k}={v}" for k, v in pending.items()]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


# --- key checks (free endpoints only) -----------------------------------------------------------

def check_key(kind: str, key: str, *, base_url: str | None = None,
              transport: httpx.BaseTransport | None = None) -> tuple[str, str]:
    """Returns (ok|fail|warn, message). `warn` means the check could not be completed."""
    key = key.strip()
    if not key:
        return FAIL, "empty"
    if re.search(r"\s", key):
        return FAIL, "contains whitespace; paste the key on its own"
    try:
        with httpx.Client(timeout=15, transport=transport) as c:
            if kind == "tavily":
                r = c.get((base_url or "https://api.tavily.com").rstrip("/") + "/usage",
                          headers={"Authorization": f"Bearer {key}"})
            elif kind == "anthropic":
                r = c.get("https://api.anthropic.com/v1/models", headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
            elif kind == "openai":
                r = c.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"})
            elif kind == "valar":
                r = c.get((base_url or "https://api.valarhq.ai/v1").rstrip("/") + "/models",
                          headers={"Authorization": f"Bearer {key}"})
            else:
                return WARN, "unknown provider"
    except httpx.HTTPError as e:
        return WARN, f"could not reach the provider to check it ({type(e).__name__})"
    if r.status_code in (401, 403):
        return FAIL, "the provider rejected this key"
    if r.status_code >= 400:
        return WARN, f"the provider answered HTTP {r.status_code}; key not confirmed"
    return OK, "accepted"


# --- setup --------------------------------------------------------------------------------------

def setup(*, assume_yes: bool = False, provider: str | None = None, env_file: str | None = None,
          skip_checks: bool = False, ask: Callable[[str], str] = input,
          ask_secret: Callable[[str], str] = getpass.getpass, out: Callable[[str], None] = print,
          environ: dict[str, str] | None = None, transport: httpx.BaseTransport | None = None) -> int:
    environ = os.environ if environ is None else environ  # type: ignore[assignment]
    root = repo_root()
    path = Path(env_file) if env_file else root / ".env"
    current = read_env(path)
    interactive = not assume_yes
    if interactive and not sys.stdin.isatty() and ask is input:
        out("openkg setup needs a terminal to ask for keys.\n"
            "Without one, export the keys and run:  openkg setup --yes\n"
            "  TAVILY_API_KEY=...  and one of  VALAR_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY")
        return 2
    updates: dict[str, str] = {}
    problems = 0

    def verify(kind: str, key: str) -> bool:
        nonlocal problems
        if skip_checks:
            return True
        state, msg = check_key(kind, key, transport=transport,
                               base_url=current.get("VALAR_BASE_URL") if kind == "valar" else None)
        out(f"  {MARK[state]} {msg}")
        if state == FAIL:
            problems += 1
        return state != FAIL

    def obtain(env: str, kind: str, label: str, url: str) -> bool:
        """True when a usable key ends up configured."""
        have = current.get(env, "")
        if not interactive:
            key = environ.get(env, "").strip() or have
            if not key:
                return False
            ok = verify(kind, key)
            if ok and key != have:
                updates[env] = key
            return ok
        out(f"  Get a key: {url}")
        for _ in range(3):
            prompt = f"  Paste your {label} key (hidden; Enter to {'keep the saved one' if have else 'skip'}): "
            key = ask_secret(prompt).strip()
            if not key:
                if have:
                    out("  ✓ keeping the saved key")
                return bool(have)
            if verify(kind, key):
                updates[env] = key
                return True
            out("  Try again, or press Enter to skip.")
        return bool(have)

    out("OpenKG setup\n" + "─" * 60)
    out("Demo mode needs no keys. Live research needs a Tavily key and one model provider key.\n"
        f"Keys are written to {path} (readable only by you) and are never printed.\n")

    out("Step 1 of 4 · Web research (Tavily)")
    has_tavily = obtain("TAVILY_API_KEY", "tavily", "Tavily", TAVILY_URL)
    if not has_tavily:
        out("  – skipped: runs will be simulations on synthetic data until a key is added")

    out("\nStep 2 of 4 · Model provider")
    chosen = provider or environ.get("MODEL_PROVIDER", "") or current.get("MODEL_PROVIDER") or ""
    if not interactive:
        if not chosen:
            chosen = next((p for p in PROVIDERS if environ.get(PROVIDERS[p]["env"]) or current.get(PROVIDERS[p]["env"])), "")
    else:
        names = list(PROVIDERS)
        for i, p in enumerate(names, 1):
            saved = "  (key saved)" if current.get(PROVIDERS[p]["env"]) else ""
            out(f"  {i}) {PROVIDERS[p]['label']:<10} {PROVIDERS[p]['blurb']}{saved}")
        default = str(names.index(chosen) + 1) if chosen in names else "1"
        answer = ask(f"  Choose 1-{len(names)}, or s to skip [{default}]: ").strip().lower() or default
        chosen = names[int(answer) - 1] if answer.isdigit() and 1 <= int(answer) <= len(names) else ""
    has_model = False
    if chosen in PROVIDERS:
        info = PROVIDERS[chosen]
        has_model = obtain(info["env"], chosen, info["label"], info["url"])
        if has_model:
            updates["MODEL_PROVIDER"] = chosen
    if not has_model:
        out("  – skipped: no model provider configured")

    out("\nStep 3 of 4 · Spending limit")
    cap = current.get("DEFAULT_RUN_BUDGET_USD", "1.0")
    if interactive:
        answer = ask(f"  Default cost cap per research run, in USD [{cap}]: ").strip()
        if answer:
            try:
                value = float(answer)
                if not 0 < value <= 1000:
                    raise ValueError
                updates["DEFAULT_RUN_BUDGET_USD"] = f"{value:g}"
                if value > float(current.get("MAX_RUN_BUDGET_USD", "25")):
                    updates["MAX_RUN_BUDGET_USD"] = f"{value:g}"
            except ValueError:
                out(f"  ! not a number between 0 and 1000; keeping {cap}")
    try:
        shown = f"${float(updates.get('DEFAULT_RUN_BUDGET_USD', cap)):.2f}"
    except ValueError:
        shown = f"${cap}"
    out(f"  ✓ each run stops at {shown} unless you raise it when starting the run")

    out("\nStep 4 of 4 · Save")
    if updates or not path.exists():
        write_env(path, updates, template=root / ".env.example")
        out(f"  ✓ wrote {path}")
    else:
        out(f"  ✓ nothing to change in {path}")
    local = root / ".env.local"
    if local.exists() and path != local:
        clash = sorted(set(read_env(local)) & set(updates))
        if clash:
            out(f"  ! {local.name} also sets {', '.join(clash)} and takes precedence outside Docker")

    live = has_tavily and has_model
    out("\n" + "─" * 60)
    out("Live research: " + ("ready" if live else "not configured (demo and simulated runs work)"))
    out("Next:\n  make dev        start the API and web app  →  http://localhost:3100\n"
        "  make doctor     check the installation")
    return 1 if problems and not live else 0


# --- doctor -------------------------------------------------------------------------------------

def _version(cmd: list[str]) -> str | None:
    if not shutil.which(cmd[0]):
        return None
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip().splitlines()[0]
    except Exception:  # noqa: BLE001
        return None


def _port_open(port: int) -> bool:
    with socket.socket() as sk:
        sk.settimeout(0.5)
        return sk.connect_ex(("127.0.0.1", port)) == 0


def doctor(*, online: bool = False, out: Callable[[str], None] = print) -> int:
    from sqlalchemy import inspect

    from .agent.models import ModelRouter
    from .config import get_settings

    rows: list[tuple[str, str, str]] = []
    add = lambda state, name, detail: rows.append((state, name, detail))  # noqa: E731
    root = repo_root()
    st = get_settings()

    v = sys.version_info
    add(OK if v >= (3, 12) else FAIL, "Python", f"{v.major}.{v.minor}.{v.micro}" + ("" if v >= (3, 12) else " (need 3.12+)"))
    in_container = Path("/.dockerenv").exists()
    if in_container:
        add(OK, "Runtime", "container image (configuration comes from the environment and Compose's .env)")
    else:
        uv = _version(["uv", "--version"])
        add(OK if uv else WARN, "uv", uv or "not found; install from https://docs.astral.sh/uv/ (not needed with Docker)")
        node = _version(["node", "--version"])
        major = int(re.sub(r"\D", "", node.split(".")[0]) or 0) if node else 0
        add(OK if major >= 20 else WARN, "Node.js", (node or "not found") + ("" if major >= 20 else " (need 20+ for the web app; not needed with Docker)"))
        web = root / "apps" / "web"
        if web.is_dir():
            add(OK if (web / "node_modules").is_dir() else WARN, "Web dependencies",
                "installed" if (web / "node_modules").is_dir() else "missing; run: cd apps/web && npm install")
        files = [f.name for f in (root / ".env", root / ".env.local") if f.exists()]
        add(OK if files else WARN, "Config file", ", ".join(files) if files else "none yet; `make setup` creates one (demo mode works without it)")

    try:
        from .db import get_engine
        with get_engine().connect() as conn:
            tables = set(inspect(conn).get_table_names())
        add(OK, "Database", f"{st.database_url.split(':', 1)[0]}, {len(tables)} tables" if "graphs" in tables
            else f"{st.database_url.split(':', 1)[0]}, empty (tables and demo data are created on first start)")
    except Exception as e:  # noqa: BLE001
        add(FAIL, "Database", f"cannot connect ({type(e).__name__}); check DATABASE_URL")

    if st.execution_mode == "celery":
        try:
            import redis
            redis.Redis.from_url(st.redis_url, socket_connect_timeout=2).ping()
            add(OK, "Redis", "reachable")
        except Exception as e:  # noqa: BLE001
            add(FAIL, "Redis", f"unreachable ({type(e).__name__}); needed when EXECUTION_MODE=celery")
    else:
        add(OK, "Worker", f"{st.execution_mode} (runs execute inside the API process)")

    if st.tavily_api_key:
        state, msg = check_key("tavily", st.tavily_api_key.get_secret_value(), base_url=st.tavily_base_url) if online else (OK, "set")
        add(state, "Tavily key", msg)
    else:
        add(WARN, "Tavily key", f"missing; live research is off. Get one at {TAVILY_URL}")
    router = ModelRouter(st)
    primary = router.primary_provider()
    if primary:
        key = st.provider_key(primary)
        state, msg = (check_key(primary, key.get_secret_value(), base_url=st.valar_base_url if primary == "valar" else None)
                      if online and key else (OK, "set"))
        add(state, "Model provider", f"{primary}: {msg}")
        for role in ("planner", "researcher", "extractor", "verifier"):
            try:
                add(OK, f"  {role}", str(router.spec(role)))
            except Exception as e:  # noqa: BLE001
                add(FAIL, f"  {role}", f"{type(e).__name__}: {e}")
    else:
        add(WARN, "Model provider", "none configured; live research is off. Add one with: make setup")
    if st.is_production and st.demo_mode:
        add(FAIL, "Production safety", "DEMO_MODE must be false when ENVIRONMENT=production")

    for port, name in ((8100, "API"),) if in_container else ((8100, "API"), (3100, "Web app")):
        add(OK, name, f"listening on :{port}" if _port_open(port) else f"not running (port {port} is free)")

    width = max(len(n) for _, n, _ in rows)
    out("OpenKG doctor\n" + "─" * 60)
    for state, name, detail in rows:
        out(f" {MARK[state]} {name:<{width}}  {detail}")
    out("─" * 60)
    failed = sum(1 for s, _, _ in rows if s == FAIL)
    live = st.live_research_available
    out("Live research: " + ("available" if live else "unavailable (demo and simulated runs still work)"))
    if not online and live:
        out("Keys were not contacted. Run `openkg doctor --online` to verify them (free calls).")
    out(f"{failed} problem(s) found." if failed else "No blocking problems.")
    return 1 if failed else 0
