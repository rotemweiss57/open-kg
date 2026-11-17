# Security model

## Controls in place

- **Authentication**: Argon2id password hashes; opaque session tokens stored hashed; `HttpOnly`, `SameSite=Lax`
  cookie (`Secure` in production or with `COOKIE_SECURE=true`); login and registration rate-limited per process.
- **CSRF**: every unsafe request needs an `X-CSRF-Token` header matching the session.
- **Tenancy**: every route resolves its object through the caller's workspace and returns 404 otherwise, including
  SSE. Tested for all read and write routes (`tests/test_api.py::test_cross_tenant_access_is_denied_everywhere`).
- **Demo mode**: `POST /auth/demo` exists only when `DEMO_MODE=true`; the server refuses to start with
  `DEMO_MODE=true` and `ENVIRONMENT=production`. The demo account has no password.
- **Secrets**: keys are read from the server environment as `SecretStr`. No endpoint returns them; tested.
- **Agent containment**: no shell, SQL, file or arbitrary-HTTP tool. Web access goes through Tavily only. URLs are
  checked against an SSRF guard (public http/https hosts only). Map/crawl is limited to domains already in scope.
  Tool output is size-capped and wrapped in explicit untrusted-data delimiters.
- **Prompt injection**: treated as inevitable. The model can only *propose*; quotes are verified against retrieved
  text, confidence is computed from sources, and nothing is committed without review or an explicit, logged policy.
- **Output safety**: React escapes all text; only `http(s)` URLs become links (`rel="noopener noreferrer nofollow"`);
  CSV exports neutralise formula prefixes; request bodies are limited to 1 MB.
- **Audit**: graph changes, run controls and review decisions are written to `audit_events`.

## Known limitations

- Rate limiting is in-memory per API process. Put a real limiter or WAF in front for internet exposure.
- No email verification, password reset, SSO, or per-graph roles (one workspace per user today).
- No Content-Security-Policy header yet.
- Retrieved page text (capped at 20k characters) is stored for excerpt verification. Deleting a graph deletes it;
  there is no automatic retention expiry.
- `robots.txt` and site terms are delegated to Tavily; OpenKG does not check them itself.
- Not independently audited.

## Operations

- Health: `GET /healthz` (liveness), `GET /readyz` (database and worker).
- Backup: `pg_dump` the `openkg` database; restore with `psql`. Graph state lives entirely in PostgreSQL.
- Migrations: `alembic upgrade head` (the API container runs this on start).
- Report vulnerabilities as described in [SECURITY.md](../SECURITY.md).
