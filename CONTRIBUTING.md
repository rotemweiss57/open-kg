# Contributing

## Setup

```bash
make install      # prerequisites, dependencies, optional API keys, verification
make dev          # API (SQLite + embedded worker) on :8100 and web on :3100
make doctor       # what is installed and configured, and what to fix
```

Requires [uv](https://docs.astral.sh/uv/) (it provides Python 3.12+) and Node 20+. `make demo` runs the full Docker
stack instead. Coding agents: start with [AGENTS.md](AGENTS.md).

## Checks (CI runs lint, tests and the web build; browser tests are a manual CI job)

```bash
make lint         # ruff, eslint, tsc
make test         # backend tests on SQLite
TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/openkg_test make test-pg
cd apps/web && npm test          # unit tests
make e2e          # Playwright against a running stack; uses the demo workspace
```

Tests never call paid APIs. Use the fakes in `apps/api/tests/fakes.py` (scripted chat model, Tavily mock transport).

## Conventions

- Change the API → run `make types` to regenerate `apps/web/lib/schema.d.ts`. Do not hand-write request shapes.
- Change `models.py` → `cd apps/api && uv run alembic revision --autogenerate -m "..."` against PostgreSQL.
- Research engines write only through `domain/recorder.py`. Graph facts change only in `domain/review.py`.
- Never hold a database transaction open across a network call.
- Anything simulated must be labelled as simulated in the UI, API and exports.
- UI changes: check dark and light themes and a phone-width viewport; `node scripts/shot.mjs <path> out.png` helps.
