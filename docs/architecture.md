# Architecture

## Decisions

| # | Decision | Why |
| --- | --- | --- |
| 1 | One Python distribution (`apps/api/openkg`) with module boundaries `api`, `domain`, `agent`, `worker`, instead of separate packages | Same contracts with far less packaging overhead. `agent` may only write through `domain.recorder`; only `domain.review` changes graph facts. |
| 2 | **Sync** SQLAlchemy 2 (rather than async) | The trust logic is shared by the API and by sync Celery workers. One implementation beats two. SSE, the only long-lived endpoint, holds no session. |
| 3 | The `runs` table is the outbox | A run is committed before anything is published. The reconciler dispatches any runnable row without a live lease, so a lost message or dead worker cannot strand a run. |
| 4 | Leases + step idempotency, not exactly-once delivery | Celery is at-least-once (`acks_late`). A worker must win `UPDATE … WHERE lease is free`; duplicates lose and exit. |
| 5 | PostgreSQL adjacency tables + JSONB; no graph database, no pgvector | Neighbourhood queries on indexed `from_id`/`to_id` are enough at this scale. Identity resolution is deterministic. |
| 6 | SQLite supported for local dev and the default test run | Zero-dependency start. SQLite runs every transaction as `BEGIN IMMEDIATE` so savepoints and rollbacks behave like Postgres. |
| 7 | Raw `httpx` Tavily client rather than the SDK | Explicit timeouts, bounded retries, circuit breaker and an injectable transport for contract tests. |
| 8 | Native provider model unless fallbacks are configured | Keeps provider features such as Anthropic prompt caching. A `FallbackChatModel` wrapper is used only when `MODEL_FALLBACKS` is set. |
| 9 | Valar uses the Responses API with `store=false` by default | Follows Valar's compatibility notes; `VALAR_API_MODE=chat` switches to Chat Completions. |
| 10 | Force-directed layout that tethers existing nodes | New discoveries appear next to their neighbours without reshuffling the map. |

## Data model

Claim-centric. `entities` and `relationships` are projections; trust lives in `claims` → `claim_evidence` → `sources`.

- `claims`: subject (entity or relationship), predicate, value, `valid_from`/`valid_to` (valid time), `observed_at`/
  `verified_at`/`accepted_at` (observation time), confidence, status
  (`proposed · accepted · contested · superseded · retracted · rejected`).
- `claim_evidence`: excerpt, stance (`supports`/`contradicts`), and whether the excerpt was found in the retrieved text.
- `sources`: canonical URL, content hash (change detection), syndication key (independence), capped text kept
  server-side only.
- `suggestions`: `entity · relationship · claim · contradiction · merge`, with rationale, risk and decision record.
- `runs`, `run_steps` (idempotency keys), `run_events` (gapless per-run sequence), `research_notes`, `frontier_questions`.
- `monitors`, `monitor_runs`, `change_events`, `notifications`; `provider_usage`, `audit_events`.

Every graph-scoped row carries `graph_id`; uniqueness constraints are per graph. Deleting a graph cascades.

## Run lifecycle

`queued → planning → researching → verifying → awaiting_review | completed`, with
`pausing → paused → resuming`, `cancel_requested → cancelled`, `budget_exhausted` (resumable with a new budget) and `failed`.

- **Control** is cooperative: the API sets `requested_action`; engines check it before every paid step and during waits.
- **Budgets** (cost, web calls, model calls, deadline) are checked before each tool call and each model call.
- **Transient errors** (429/5xx/timeouts) are retried with jittered exponential backoff, at most `MAX_RUN_ATTEMPTS`.
- **Events** get their sequence number from an atomic `UPDATE runs SET last_event_seq = last_event_seq + 1 RETURNING`.

### Recovery boundaries

| Failure | What happens |
| --- | --- |
| Queue message lost / never published | Reconciler re-dispatches within seconds. |
| Message delivered twice | Second worker fails to take the lease and exits. |
| Worker killed mid-step (simulation) | The step's transaction rolls back; after lease expiry (45s) the run resumes at that step. |
| Worker killed mid-run (Deep Agents) | Resumes from the last LangGraph checkpoint. An interrupted subagent task is re-issued; its earlier searches and reads are answered from the per-run cache at no cost and re-recorded candidates are no-ops. Model tokens for the interrupted task are spent again. |
| Cancel during a provider call | That call may complete and be charged; no further call starts. |
| Browser closed / SSE dropped | Nothing is lost. Reconnect replays from `Last-Event-ID`. |

## Deep Agents

`agent/deep.py` builds `create_deep_agent(model, tools, subagents, middleware, checkpointer)`:

- **Coordinator**: budget, plan, delegation, frontier ranking, final dossier.
- **discovery-scout** (`web_search`, `site_map`, lookups), **entity-investigator** (`web_extract`, `site_crawl`,
  `record_candidate_*`), **evidence-verifier** (search/extract, re-record with new evidence, record conflicts).
- The default in-state backend gives agents a virtual scratch filesystem; the built-in `execute` tool is inert.
- Distributed lifecycle is deliberately *not* delegated to Deep Agents; see the table above.

## Monitoring

A check loads its target, observes (one search; a model call only if a source is new or changed), then records.
Candidate claims pass through the normal recorder; a change event and notification are created only for watched
predicates whose evidence quote was verified. Scheduling is bounded: after downtime each overdue monitor runs once.
