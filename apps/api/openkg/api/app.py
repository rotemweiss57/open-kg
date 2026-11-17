from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from ..config import get_settings
from ..db import get_engine, session_scope
from .routes import router

log = logging.getLogger("openkg.api")
MAX_BODY_BYTES = 1_000_000


def problem(status: int, title: str, detail: str = "", **extra) -> JSONResponse:
    """RFC 9457 problem details for every error response."""
    return JSONResponse({"type": "about:blank", "title": title, "status": status, "detail": detail or title, **extra},
                        status_code=status, media_type="application/problem+json")


@asynccontextmanager
async def lifespan(app: FastAPI):
    st = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if get_engine().dialect.name == "sqlite":
        from ..seed import init_db
        init_db()  # Postgres deployments run Alembic migrations instead (see infra/docker)
    if st.demo_mode and st.environment != "test":
        from ..seed import seed_demo
        try:
            seed_demo()
        except Exception:  # noqa: BLE001
            log.exception("demo seeding failed")
    try:  # one-time data hygiene for graphs recorded before refinement handling existed
        from sqlalchemy import select

        from ..domain.review import (
            apply_policy,
            drop_off_subject_evidence,
            merge_duplicate_organisations,
            reverify_evidence,
        )
        from ..models import Graph
        with session_scope() as s:
            for g in s.scalars(select(Graph)):
                if not g.is_synthetic:
                    reverify_evidence(s, g)
                    drop_off_subject_evidence(s, g)
                    merge_duplicate_organisations(s, g)
                apply_policy(s, g)
    except Exception:  # noqa: BLE001
        log.exception("startup policy sweep failed")
    app.state.worker = None
    if st.execution_mode == "embedded":
        from ..worker.embedded import EmbeddedWorker
        app.state.worker = EmbeddedWorker()
        app.state.worker.start()
        log.info("embedded worker started")
    yield
    if app.state.worker:
        app.state.worker.stop()


def create_app() -> FastAPI:
    st = get_settings()
    app = FastAPI(title="OpenKG API", version="0.1.0", lifespan=lifespan,
                  description="Evidence-backed knowledge graphs built by long-running research agents.",
                  docs_url=None if st.is_production else "/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    if st.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in st.cors_origins.split(",")],
                           allow_credentials=True, allow_methods=["*"], allow_headers=["X-CSRF-Token", "Content-Type"])

    @app.middleware("http")
    async def hardening(request: Request, call_next):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            return problem(413, "Payload too large", "Request bodies are limited to 1 MB.")
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        return problem(exc.status_code, str(exc.detail) if exc.status_code < 500 else "Server error", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        errors = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        first = errors[0] if errors else {"field": "", "message": "Invalid request"}
        return problem(422, "Invalid request", f"{first['field']}: {first['message']}".strip(": "), errors=errors)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)
        return problem(500, "Server error", "Something went wrong on the server. The error was logged.")

    @app.get("/healthz", tags=["system"])
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz", tags=["system"])
    def readyz():
        try:
            with session_scope() as s:
                s.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001
            return problem(503, "Not ready", "Database is unreachable.")
        worker = getattr(app.state, "worker", None)
        if st.execution_mode == "embedded" and not (worker and worker.thread and worker.thread.is_alive()):
            return problem(503, "Not ready", "Embedded worker is not running.")
        return {"status": "ready", "execution_mode": st.execution_mode}

    app.include_router(router)
    return app


app = create_app()
