#!/usr/bin/env bash
# OpenKG installer: checks prerequisites, installs dependencies, configures keys, verifies.
#
#   ./scripts/install.sh              guided (asks for API keys; input is hidden)
#   ./scripts/install.sh --yes        no prompts; reads TAVILY_API_KEY and a provider key from the environment
#   ./scripts/install.sh --demo       no keys at all: demo mode with synthetic data
#   ./scripts/install.sh --docker     configure only; you will run the stack with `docker compose up`
#
# Safe to re-run. It never prints keys and never makes a paid API call.
set -euo pipefail

MODE=guided; DOCKER=0
for arg in "$@"; do
  case "$arg" in
    --yes|-y) MODE=yes ;;
    --demo) MODE=demo ;;
    --docker) DOCKER=1 ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m'; else B=; G=; Y=; R=; N=; fi
step() { printf '\n%s[%s/%s] %s%s\n' "$B" "$1" "$TOTAL" "$2" "$N"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$1"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$1"; }
die()  { printf '  %s✗%s %s\n' "$R" "$N" "$1" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
TOTAL=5

# ---------------------------------------------------------------------------------------------
step 1 "Checking prerequisites"
if [ "$DOCKER" = 1 ]; then
  have docker || die "Docker not found. Install Docker Desktop: https://docs.docker.com/get-docker/"
  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 not found (need 'docker compose')."
  ok "Docker $(docker --version | sed 's/Docker version //; s/,.*//')"
else
  if ! have uv; then
    if [ "$MODE" = guided ] && [ -t 0 ]; then
      printf '  uv (the Python package manager) is not installed. Install it now from astral.sh? [Y/n] '
      read -r reply
      case "$reply" in n|N|no) die "uv is required: https://docs.astral.sh/uv/getting-started/installation/" ;; esac
      curl -LsSf https://astral.sh/uv/install.sh | sh
      export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
      have uv || die "uv was installed but is not on PATH. Open a new terminal and re-run this script."
    else
      die "uv not found. Install it, then re-run:  curl -LsSf https://astral.sh/uv/install.sh | sh"
    fi
  fi
  ok "$(uv --version)"
  have node || die "Node.js not found. Install Node 22 LTS: https://nodejs.org  (or use --docker)"
  NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
  [ "$NODE_MAJOR" -ge 20 ] || die "Node.js $NODE_MAJOR is too old; need 20 or newer (22 LTS recommended)."
  ok "Node.js $(node --version)"
  have npm || die "npm not found (it ships with Node.js)."
fi

# ---------------------------------------------------------------------------------------------
step 2 "Installing dependencies"
if [ "$DOCKER" = 1 ]; then
  ok "skipped: Docker builds the images on first start"
else
  (cd apps/api && uv sync --quiet) || die "Python dependencies failed to install (see the output above)."
  ok "API dependencies (Python $(cd apps/api && uv run --quiet python -c 'import platform; print(platform.python_version())'))"
  if [ -f apps/web/package-lock.json ]; then
    (cd apps/web && npm ci --silent --no-audit --no-fund) || die "Web dependencies failed to install."
  else
    (cd apps/web && npm install --silent --no-audit --no-fund) || die "Web dependencies failed to install."
  fi
  ok "Web dependencies"
fi

# ---------------------------------------------------------------------------------------------
step 3 "API keys"
if [ "$MODE" = demo ]; then
  ok "skipped (--demo): synthetic data and simulated runs only. Add keys later with: make setup"
elif [ "$DOCKER" = 1 ] && ! have uv; then
  [ -f .env ] || { cp .env.example .env; chmod 600 .env; }
  warn "Edit .env and fill in TAVILY_API_KEY plus one of VALAR_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY."
elif [ "$MODE" = yes ]; then
  (cd apps/api && uv run --quiet openkg setup --yes) || warn "Setup reported a problem; see above."
else
  (cd apps/api && uv run --quiet openkg setup) || warn "Setup did not finish; re-run it any time with: make setup"
fi

# ---------------------------------------------------------------------------------------------
step 4 "Verifying"
if [ "$DOCKER" = 1 ] && ! have uv; then
  ok "skipped: run 'docker compose exec api openkg doctor' once the stack is up"
else
  (cd apps/api && uv run --quiet openkg doctor) || die "Doctor found a blocking problem (see above)."
fi

# ---------------------------------------------------------------------------------------------
step 5 "Done"
if [ "$DOCKER" = 1 ]; then
  echo "  Start:   docker compose up --build"
else
  echo "  Start:   make dev"
fi
cat <<'TXT'
  Open:    http://localhost:3100   (click "Enter demo")

  Later:   make setup     change keys, provider or spending limit
           make doctor    check the installation (add ONLINE=1 to verify keys)
           make help      everything else
TXT
