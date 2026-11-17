"""Server-side configuration. Secrets live only here and are never serialized to clients."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Local default: one SQLite file at a fixed place, so `make dev`, `openkg research`, `openkg doctor` and
# every other entry point see the same data whatever directory they are started from. In a source
# checkout that is apps/api/.data; for an installed package it is ~/.openkg.
_SOURCE_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _SOURCE_ROOT / ".data" if (_SOURCE_ROOT / "pyproject.toml").exists() else Path.home() / ".openkg"
DEFAULT_SQLITE_URL = f"sqlite:///{_DATA_DIR / 'openkg.db'}"

# Conservative published-rate estimates used only for *estimated* cost display.
TAVILY_USD_PER_CREDIT = 0.008


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../../.env", ".env.local", "../../.env.local"), extra="ignore")

    environment: Literal["development", "production", "test"] = "development"
    database_url: str = DEFAULT_SQLITE_URL
    redis_url: str = "redis://localhost:6379/0"
    # embedded: API process runs an in-process durable worker (no Redis needed).
    # celery: runs are dispatched to Celery workers; a beat reconciler re-publishes lost work.
    execution_mode: Literal["embedded", "celery", "none"] = "embedded"

    demo_mode: bool = True
    allow_registration: bool = True
    session_ttl_hours: int = 24 * 14
    cookie_secure: bool = False
    cors_origins: str = ""

    tavily_api_key: SecretStr | None = None
    tavily_base_url: str = "https://api.tavily.com"
    model_provider: Literal["openai", "anthropic", "valar", ""] = ""
    model_name: str = ""
    model_fallbacks: str = ""  # comma separated provider[:model] list, explicit opt-in
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    valar_api_key: SecretStr | None = None
    valar_base_url: str = "https://api.valarhq.ai/v1"
    # Valar documents Responses as its primary endpoint; "chat" forces Chat Completions.
    valar_api_mode: Literal["responses", "chat"] = "responses"
    # With streaming on, this is the longest allowed silence between chunks, not a cap on the whole reply.
    provider_timeout_seconds: float = 90.0
    model_streaming: bool = True
    # Hard wall-clock limit for one model call; a runaway generation is aborted and retried.
    model_call_max_seconds: float = 180.0
    # Optional role overrides, e.g. MODEL_ROLE_VERIFIER=anthropic:claude-opus-5-5
    model_role_planner: str = ""
    model_role_researcher: str = ""
    model_role_extractor: str = ""
    model_role_verifier: str = ""

    # Used only to *estimate* model spend; set to your provider's published rates.
    model_input_usd_per_mtok: float = 3.0
    model_output_usd_per_mtok: float = 15.0

    default_run_budget_usd: float = 1.0
    default_max_tavily_calls: int = 40
    default_max_model_calls: int = 150
    default_run_deadline_minutes: int = 30
    max_run_budget_usd: float = 25.0

    lease_seconds: int = 45
    max_run_attempts: int = 4
    fixture_step_delay: float = Field(default=1.1, description="Seconds between simulated steps")
    snapshot_node_limit: int = 400

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def provider_key(self, provider: str) -> SecretStr | None:
        return {
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
            "valar": self.valar_api_key,
        }.get(provider)

    def configured_providers(self) -> list[str]:
        return [p for p in ("anthropic", "openai", "valar") if self.provider_key(p)]

    @property
    def live_research_available(self) -> bool:
        return bool(self.tavily_api_key) and bool(self.configured_providers())


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.is_production and s.demo_mode:
        raise RuntimeError("DEMO_MODE must be false when ENVIRONMENT=production")
    return s
