# OpenKG: instructions for coding agents

You are an AI coding agent (Claude Code, Cursor, Codex, Copilot or similar) helping someone install, run or
change OpenKG. Follow this file top to bottom. Every step says what to run, what you should see, and what to do
if you see something else.

OpenKG is a self-hosted platform where research agents build evidence-backed knowledge graphs from the open web.
It has a Python API with an embedded worker (`apps/api`) and a Next.js web app (`apps/web`).

## Rules that always apply

1. **Never print, log, echo or commit an API key.** Do not `cat` `.env` or `.env.local`. To see what is
   configured, run `make doctor`, which reports only "set" or "missing".
2. **Never start a live research run without the user's explicit go-ahead.** Live runs spend real money on
   Tavily and a model provider. Demo runs on synthetic data are free and need no permission.
3. **Ask the user for keys; do not invent, reuse or search for them.** Step 3 gives two safe ways to enter them.
4. Do not use ports 3000, 5432 or 6379 for OpenKG. It uses 3100 (web) and 8100 (API) so it can sit beside other
   local stacks.

## Install

### Step 1. Check prerequisites

```bash
git --version && uv --version && node --version
```

You need **uv** (any recent version; it installs Python 3.12+ itself) and **Node.js 20 or newer** (22 LTS
recommended).

- `uv: command not found` → `curl -LsSf https://astral.sh/uv/install.sh | sh`, then open a new shell.
- Node missing or older than 20 → ask the user to install Node 22 LTS from <https://nodejs.org>, or use the
  Docker path below.
- The user prefers Docker, or cannot install uv and Node → skip to [Docker path](#docker-path).

### Step 2. Get the code and install dependencies

```bash
git clone https://github.com/rotemweiss57/open-kg.git && cd open-kg   # skip if already inside the checkout
./scripts/install.sh --demo
```

`--demo` installs everything and skips the key prompts, so it never blocks waiting for input. Expected last
lines: `No blocking problems.` and a `[5/5] Done` block. If a step fails the script stops and prints the reason
on a line starting with `✗`; fix that and re-run. The script is safe to re-run.

### Step 3. Add API keys

Live research needs **two** keys. Skip this step entirely if the user only wants the demo.

| Key | Where the user gets it | Variable |
| --- | --- | --- |
| Tavily (web search and extraction) | <https://app.tavily.com> | `TAVILY_API_KEY` |
| One model provider: **Valar** (recommended: lowest cost, measured defaults), Anthropic or OpenAI | [Valar quickstart](https://docs.valarhq.ai/quickstart) · [Anthropic](https://console.anthropic.com/settings/keys) · [OpenAI](https://platform.openai.com/api-keys) | `VALAR_API_KEY`, `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` |

Tell the user which keys are needed and where to get them, then use **one** of these:

**Easiest for Tavily: Sign in with Tavily.** No Tavily key is needed. After step 5, the user opens
<http://localhost:3100/settings> and clicks **Sign in with Tavily**. A `TAVILY_API_KEY`, if set, always takes
precedence. The model provider key still comes from option A or B. Hosted (non-localhost) instances must list their
public origin in `CORS_ORIGINS` for Sign in with Tavily.

**Option A (preferred): the user types the keys themselves.** Ask them to run this in their own terminal. Input
is hidden and each key is checked with a free call before it is saved:

```bash
make setup
```

**Option B: the keys are already exported in the shell environment.** Run the non-interactive form. It reads
`TAVILY_API_KEY` and the provider key from the environment, checks them and writes `.env`:

```bash
cd apps/api && uv run openkg setup --yes --provider valar    # or anthropic / openai
```

Do not ask the user to paste a key into the chat. If they do anyway, write it with Option B by exporting it for
that single command only, and never repeat it back.

`openkg setup` writes `.env` in the repository root with owner-only permissions. Both `.env` and `.env.local`
are git-ignored.

### Step 4. Verify

```bash
make doctor ONLINE=1
```

Expected: every line starts with `✓`, then `Live research: available` and `No blocking problems.`
`ONLINE=1` verifies the keys with free, read-only provider calls.

| You see | Do this |
| --- | --- |
| `✗ Tavily  the provider rejected this key` | The key is wrong or revoked. Ask the user for a new one; re-run step 3. |
| `! Tavily  not connected` or `! Model provider  none configured` | Fine for demo mode. For live research, do step 3 (or Sign in with Tavily in Settings). |
| `! … could not reach the provider` | No network access from this machine. Keys are saved but unverified. |
| `✗ Database  cannot connect` | `DATABASE_URL` points somewhere unreachable. Remove it from `.env` to use the built-in SQLite file. |
| `! Web dependencies  missing` | `cd apps/web && npm install` |

### Step 5. Start it

`make dev` runs until stopped, so start it in the background (or in a second terminal) and wait for readiness:

```bash
make dev > /tmp/openkg-dev.log 2>&1 &
until curl -fsS http://127.0.0.1:8100/readyz >/dev/null; do sleep 1; done
until curl -fsS -o /dev/null http://localhost:3100; do sleep 1; done
echo ready
```

Then tell the user to open <http://localhost:3100> and click **Enter demo**. Two synthetic graphs are already
there. **Start research** on one of them plays a simulated run through the real pipeline at no cost.

- `address already in use` on 8100 or 3100 → another OpenKG is already running (`make doctor` shows it). Use
  that one, or stop it first.
- The web app loads but shows "cannot reach the API" → the API is still starting; check `/tmp/openkg-dev.log`.

### Docker path

```bash
./scripts/install.sh --docker --demo   # checks Docker; no key prompts. For live research do step 3 first.
docker compose up --build -d
until curl -fsS http://127.0.0.1:8100/readyz >/dev/null; do sleep 2; done
docker compose exec api openkg doctor
```

This runs the web app, API, a Celery worker, the scheduler, PostgreSQL and Redis. Only the web and API ports are
published. Compose reads keys from `.env` in the repository root, which is the file `make setup` writes. Stop with `docker compose down` (add `-v` only if the user wants the data deleted).

## Run research

**From the web app:** *New graph* → describe the question or pick a playbook → review the charter and budget →
*Start research*. Live runs ask for confirmation of the spending limits.

**From the terminal** (after the user has agreed to the spend):

```bash
cd apps/api && uv run openkg research "Which companies build vector databases, and who funds them?" \
  --confirm-spend --max-usd 1 --max-web-calls 30 --export markdown --out /tmp/graph.md
```

The limits are hard caps enforced by the server. The command prints the outcome, the estimated cost, every
suggestion with its confidence, and a link to the graph in the web app. `--export` accepts `markdown`, `json`,
`csv` or `graphml`. With the default Valar models a run of this size has cost between $0.30 and $1.

**Over HTTP** (the same API the web app uses; the OpenAPI schema is at `/api/openapi.json`):

```bash
J=/tmp/openkg.cookies; API=http://127.0.0.1:8100/api/v1
CSRF=$(curl -s -c $J -X POST $API/auth/demo | python3 -c 'import sys,json; print(json.load(sys.stdin)["csrf_token"])')
H=(-b $J -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json')
curl -s "${H[@]}" $API/graphs                                         # list graphs
curl -s "${H[@]}" -X POST $API/charter/propose -d '{"prompt":"..."}'  # draft a charter and ontology
curl -s "${H[@]}" $API/graphs/$GRAPH_ID/table                          # one row per entity
curl -s "${H[@]}" "$API/graphs/$GRAPH_ID/export?format=json"           # accepted facts, with evidence
curl -s "${H[@]}" "$API/graphs/$GRAPH_ID/export?format=markdown&include_proposed=true"   # cited report, unreviewed items marked
```

Every write needs the session cookie and the `X-CSRF-Token` header. Starting a live run needs
`"engine":"deep_agent","confirm_spend":true` in the body of `POST /graphs/{id}/runs`.

## Working on the code

```bash
make test         # backend tests (SQLite, no network, no paid calls)
make lint         # ruff, eslint, tsc
cd apps/web && npm test        # web unit tests
make e2e          # browser tests; needs `make dev` running
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/architecture.md](docs/architecture.md) first. The rules that
matter most:

- Research engines write **only** through `apps/api/openkg/domain/recorder.py`. Accepted facts change **only**
  in `domain/review.py`. Do not add a shortcut around either.
- A claim's confidence is computed from its evidence. Never let a model's self-reported confidence raise it.
- Never hold a database transaction open across a network or model call.
- Tests must not call paid APIs. Use `apps/api/tests/fakes.py` (scripted chat model, Tavily mock transport).
- Anything simulated must be labelled as simulated in the UI, the API and exports.
- After changing an API response shape, run `make types` (with the API running) to regenerate the web client types.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| A run sits in `queued` | No worker. With `make dev` the worker is inside the API process; check the API log. With Docker, check `docker compose logs worker`. |
| A run ends as `budget_exhausted` | It reached a cap you set. The findings so far are kept. Extend it from the Research tab or start another run. |
| A run ends as `failed` | Open the Research tab for the reason, then **Retry**: it resumes from the last checkpoint and does not repeat paid work. |
| Few facts are approved automatically | Expected. Auto-approval needs two independent sources, or the organisation's own site. Single-source facts wait in Review. |
| `openkg: command not found` | Run it through uv from `apps/api`: `uv run openkg …`, or use the `make` targets. |
| Reset the demo data | `make seed` |
