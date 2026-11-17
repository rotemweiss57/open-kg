"""`openkg` command: serve, worker, seed, migrate, openapi."""
from __future__ import annotations

import argparse
import json
import sys
import time


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="openkg")
    sub = p.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="Run the API (and the embedded worker if EXECUTION_MODE=embedded)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8100)
    serve.add_argument("--reload", action="store_true")
    sub.add_parser("worker", help="Run a standalone polling worker (no Redis needed)")
    seed = sub.add_parser("seed", help="Create the synthetic demo workspace")
    seed.add_argument("--force", action="store_true", help="Delete and recreate the demo graphs")
    sub.add_parser("openapi", help="Print the OpenAPI schema")
    sub.add_parser("check", help="Validate configuration without printing secrets")
    setup = sub.add_parser("setup", help="Guided configuration: API keys, model provider, spending limit")
    setup.add_argument("--yes", action="store_true", help="No prompts: take keys from the environment")
    setup.add_argument("--provider", choices=["valar", "anthropic", "openai"], help="Model provider to use")
    setup.add_argument("--env-file", help="File to write (default: .env in the repository root)")
    setup.add_argument("--skip-checks", action="store_true", help="Do not contact providers to validate keys")
    doctor = sub.add_parser("doctor", help="Check the installation and explain what to fix")
    doctor.add_argument("--online", action="store_true", help="Also verify keys with free provider calls")
    research = sub.add_parser("research", help="Run one budget-capped LIVE research run from the terminal")
    research.add_argument("question")
    research.add_argument("--confirm-spend", action="store_true", help="Required: acknowledges real API spend")
    research.add_argument("--max-usd", type=float, default=1.0)
    research.add_argument("--max-web-calls", type=int, default=30)
    research.add_argument("--max-model-calls", type=int, default=120)
    research.add_argument("--minutes", type=int, default=20)
    research.add_argument("--export", choices=["markdown", "json", "csv", "graphml"], help="Print or save the graph afterwards")
    research.add_argument("--out", help="File for --export (default: stdout)")
    smoke = sub.add_parser("live-smoke", help="Small, budget-capped LIVE Deep Agents + Tavily run (spends real credits)")
    smoke.add_argument("--confirm-spend", action="store_true", help="Required: acknowledges real API spend")
    smoke.add_argument("--question", default="Which companies announced a Series B round this month, and who led each round?")
    smoke.add_argument("--max-usd", type=float, default=0.5)
    smoke.add_argument("--max-web-calls", type=int, default=6)
    smoke.add_argument("--max-model-calls", type=int, default=25)
    args = p.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn
        uvicorn.run("openkg.api.app:app", host=args.host, port=args.port, reload=args.reload)
    elif args.cmd == "worker":
        from .worker.embedded import EmbeddedWorker
        w = EmbeddedWorker()
        w.start()
        print(f"worker {w.identity} polling for runs (Ctrl-C to stop)")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            w.stop()
    elif args.cmd == "seed":
        from .seed import seed_demo
        print("seeded graphs:", seed_demo(force=args.force))
    elif args.cmd == "openapi":
        from .api.app import app
        json.dump(app.openapi(), sys.stdout, indent=1)
    elif args.cmd == "check":
        from .agent.models import ModelRouter
        from .config import get_settings
        st = get_settings()
        print(f"environment={st.environment} demo_mode={st.demo_mode} execution_mode={st.execution_mode}")
        print(f"database={st.database_url.split(':', 1)[0]} tavily={'set' if st.tavily_api_key else 'missing'}")
        for row in ModelRouter(st).status():
            print(f"  {row['provider']:<10} {'configured' if row['configured'] else 'not configured':<15} {row['model'] or ''} {row['note']}")
        print("live research:", "available" if st.live_research_available else "unavailable (simulation only)")
    elif args.cmd == "setup":
        from .setup import setup as run_setup
        return run_setup(assume_yes=args.yes, provider=args.provider, env_file=args.env_file, skip_checks=args.skip_checks)
    elif args.cmd == "doctor":
        from .setup import doctor as run_doctor
        return run_doctor(online=args.online)
    elif args.cmd == "research":
        return live_smoke(args)
    elif args.cmd == "live-smoke":
        return live_smoke(args)
    return 0


def live_smoke(args) -> int:
    """End-to-end live verification. Never runs in CI and never without --confirm-spend."""
    from sqlalchemy import func, select

    from .agent.charter import heuristic
    from .config import get_settings
    from .db import session_scope
    from .domain.runs import create_run
    from .models import Graph, Run, Suggestion
    from .security import create_user
    from .seed import init_db
    from .worker.executor import execute_run

    st = get_settings()
    if not args.confirm_spend:
        print("Refusing to run: this makes real Tavily and model calls. Re-run with --confirm-spend.")
        return 2
    if not st.live_research_available:
        print("Live research unavailable: set TAVILY_API_KEY and one model provider key (see `openkg check`).")
        return 2
    init_db()
    proposal = heuristic(args.question)
    smoke = args.cmd == "live-smoke"
    minutes = getattr(args, "minutes", 10)
    with session_scope() as s:
        if smoke:
            user, ws = create_user(s, f"smoke-{int(time.time())}@openkg.local", None, name="Live smoke test")
            user_id, ws_id = user.id, ws.id
        else:  # land in the demo workspace so the result is visible in the web app
            from .seed import ensure_demo_user
            user_id, ws_id = ensure_demo_user()
        g = Graph(workspace_id=ws_id, name=(f"Live smoke: {proposal.name}" if smoke else proposal.name)[:200],
                  description=proposal.description, charter=proposal.charter.model_dump(),
                  ontology=proposal.ontology.model_dump(), budget_usd=args.max_usd)
        s.add(g)
        s.flush()
        run = create_run(s, g, engine="deep_agent", confirm_spend=True, user_id=user_id, budget={
            "max_usd": args.max_usd, "max_tavily_calls": args.max_web_calls, "max_model_calls": args.max_model_calls,
            "deadline_minutes": minutes})
        rid, gid = run.id, g.id
    print(f"run {rid}: caps ${args.max_usd:.2f}, {args.max_web_calls} web calls, {args.max_model_calls} model calls, "
          f"{minutes} minutes. Ctrl-C cancels and keeps what was found.", flush=True)
    # A running API has its own worker, which may pick the run up first. Either way, wait for the
    # run to reach a resting state: execute_run is safe to call repeatedly and returns at once when
    # another worker holds the lease. A background thread reports progress while it works.
    import threading
    resting = {"awaiting_review", "completed", "failed", "cancelled", "budget_exhausted", "paused"}
    done = threading.Event()

    def progress() -> None:
        shown = None
        while not done.wait(5):
            with session_scope() as s:
                run = s.get(Run, rid)
                found = s.scalar(select(func.count()).select_from(Suggestion).where(Suggestion.run_id == rid)) or 0
                line = (f"  {run.status:<12} {run.tavily_calls:>3} web calls  {run.model_calls:>3} model calls  "
                        f"${run.est_cost_usd:.2f}  {found} findings  {run.current_focus[:60]}")
            if line != shown:
                print(line, flush=True)
                shown = line

    threading.Thread(target=progress, daemon=True).start()
    give_up = time.time() + minutes * 60 + 120
    try:
        while True:
            outcome = execute_run(rid)
            with session_scope() as s:
                status = s.get(Run, rid).status
            if status in resting or time.time() > give_up:
                break
            time.sleep(3)
    except KeyboardInterrupt:
        from .domain.runs import cancel
        with session_scope() as s:
            cancel(s, s.get(Run, rid))
        execute_run(rid)  # we still hold the lease: this pass sees the request and closes the run cleanly
        print("\nCancelled. Findings so far are kept; see the web app.")
        return 130
    finally:
        done.set()
    with session_scope() as s:
        run = s.get(Run, rid)
        print(f"outcome={outcome} status={run.status} web_calls={run.tavily_calls} credits={run.tavily_credits:g} "
              f"model_calls={run.model_calls} tokens={run.input_tokens}+{run.output_tokens} est_cost=${run.est_cost_usd:.4f}")
        if run.error:
            print("error:", run.error)
        for sug in s.scalars(select(Suggestion).where(Suggestion.graph_id == gid)):
            print(f"  [{sug.kind:<12} {sug.confidence:<6}] {sug.title}")
        print("summary:", run.summary[:600])
        ok = run.status in ("awaiting_review", "completed", "budget_exhausted")
        fmt = getattr(args, "export", None)
        if fmt:
            from .domain.export import export_graph
            body, _, _ = export_graph(s, s.get(Graph, gid), fmt, include_proposed=True)
            if args.out:
                with open(args.out, "w") as fh:
                    fh.write(body)
                print(f"wrote {args.out}")
            else:
                print(body)
        if not smoke:
            print(f"open in the web app: http://localhost:3100/g/{gid}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
