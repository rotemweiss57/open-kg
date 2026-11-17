.PHONY: help install setup doctor demo down dev api web test test-pg lint types e2e seed check
.DEFAULT_GOAL := help

help:            ## List the available commands
	@grep -E '^[a-z-]+: .*##' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  make %-10s %s\n", $$1, $$2}'

install:         ## Guided install: prerequisites, dependencies, API keys, verification
	./scripts/install.sh

setup:           ## Add or change API keys, model provider and spending limit
	cd apps/api && uv run openkg setup

doctor:          ## Check the installation (ONLINE=1 also verifies keys with free calls)
	cd apps/api && uv run openkg doctor $(if $(ONLINE),--online,)

demo:            ## Full stack in Docker (demo mode, no keys needed) → http://localhost:3100
	docker compose up --build

down:            ## Stop the Docker stack
	docker compose down

api:             ## Local API + embedded worker on SQLite → http://127.0.0.1:8100
	cd apps/api && EXECUTION_MODE=embedded uv run openkg serve --port 8100

web:             ## Local web app → http://localhost:3100
	cd apps/web && npm install && npm run dev

dev:             ## API and web together (Ctrl-C stops both)
	$(MAKE) -j2 api web

seed:            ## Recreate the synthetic demo workspace
	cd apps/api && uv run openkg seed --force

check:           ## Show what the server is configured to do (never prints secrets)
	cd apps/api && uv run openkg check

test:            ## Backend tests on SQLite
	cd apps/api && uv run pytest

test-pg:         ## Backend tests on PostgreSQL (needs TEST_DATABASE_URL)
	cd apps/api && uv run pytest

lint:            ## Ruff, ESLint and TypeScript
	cd apps/api && uv run ruff check openkg tests
	cd apps/web && npm run lint && npm run typecheck

types:           ## Regenerate the typed API client from the running API's OpenAPI schema
	cd apps/web && npm run types

e2e:             ## Browser tests against a running stack (make dev or make demo)
	cd apps/web && npx playwright test
