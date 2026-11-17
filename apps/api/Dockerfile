FROM python:3.13-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY openkg ./openkg
COPY alembic ./alembic
COPY alembic.ini ./
RUN uv sync --frozen --no-dev && useradd --system --uid 10001 openkg
USER openkg
EXPOSE 8100
# Migrations run before the API starts; workers wait for the API to be healthy.
CMD ["sh", "-c", "alembic upgrade head && uvicorn openkg.api.app:app --host 0.0.0.0 --port 8100"]
