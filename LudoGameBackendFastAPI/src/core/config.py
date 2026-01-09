from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_dotenv_once() -> None:
    """
    Load environment variables from a .env file if present.

    We load in this order:
    1) Current working directory .env (common for local dev)
    2) Container root .env if repository structure differs

    This is intentionally non-failing to keep preview stable.
    """
    # Best-effort load: don't error if file missing.
    load_dotenv(override=False)

    # Try to load .env from common repo locations relative to this file.
    # src/core/config.py -> src/core -> src -> (backend root)
    backend_root = Path(__file__).resolve().parents[2]
    candidate = backend_root / ".env"
    if candidate.exists():
        load_dotenv(candidate, override=False)


class Settings(BaseSettings):
    """
    Application configuration loaded from environment variables.

    Note: This only declares variables; values are not required at this stage.
    Defaults are chosen to keep local preview working without extra setup.
    """

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    # --- App metadata ---
    app_name: str = Field(default="Ludo Game Backend", description="Service name used in logs and OpenAPI.")
    app_version: str = Field(default="0.1.0", description="Service version.")
    environment: str = Field(default="development", description="Runtime environment (development/staging/production).")

    # --- HTTP / network ---
    host: str = Field(default="0.0.0.0", description="Bind host for the HTTP server.")
    port: int = Field(default=3001, description="Bind port for the HTTP server.")
    trust_proxy: bool = Field(default=False, description="Whether to trust X-Forwarded-* headers from a proxy.")

    # --- CORS ---
    # In our preview environment, .env already includes ALLOWED_ORIGINS/HEADERS/METHODS.
    allowed_origins: List[str] = Field(
        default_factory=lambda: ["*"],
        description="CORS allowed origins. Use comma-separated list via ALLOWED_ORIGINS env var.",
        validation_alias="ALLOWED_ORIGINS",
    )
    allowed_methods: List[str] = Field(
        default_factory=lambda: ["*"],
        description="CORS allowed methods. Use comma-separated list via ALLOWED_METHODS env var.",
        validation_alias="ALLOWED_METHODS",
    )
    allowed_headers: List[str] = Field(
        default_factory=lambda: ["*"],
        description="CORS allowed headers. Use comma-separated list via ALLOWED_HEADERS env var.",
        validation_alias="ALLOWED_HEADERS",
    )
    cors_allow_credentials: bool = Field(
        default=True,
        description="Whether to allow credentials for CORS requests.",
        validation_alias="CORS_ALLOW_CREDENTIALS",
    )
    cors_max_age: int = Field(default=3600, description="CORS max age in seconds.", validation_alias="CORS_MAX_AGE")

    # --- Persistence & cache (declared only; values optional for now) ---
    postgres_url: Optional[str] = Field(
        default=None,
        description="PostgreSQL connection URL. Example: postgresql+psycopg://user:pass@host:5432/db",
        validation_alias="POSTGRES_URL",
    )
    redis_url: Optional[str] = Field(
        default=None,
        description="Redis connection URL. Example: redis://:pass@host:6379/0",
        validation_alias="REDIS_URL",
    )

    # --- Auth / security (declared only; values optional for now) ---
    jwt_secret: Optional[str] = Field(
        default=None,
        description="JWT signing secret (HS256) or key material reference.",
        validation_alias="JWT_SECRET",
    )
    jwt_issuer: Optional[str] = Field(default=None, description="JWT issuer.", validation_alias="JWT_ISSUER")
    jwt_audience: Optional[str] = Field(default=None, description="JWT audience.", validation_alias="JWT_AUDIENCE")
    jwt_exp_seconds: int = Field(
        default=3600,
        description="JWT expiration time in seconds.",
        validation_alias="JWT_EXP_SECONDS",
    )

    # --- Gameplay / fairness ---
    rng_audit_salt: Optional[str] = Field(
        default=None,
        description="Secret salt used to hash/audit dice roll commitments. Strong random string recommended.",
        validation_alias="RNG_AUDIT_SALT",
    )

    # --- Feature flags ---
    feature_flags_enabled: bool = Field(
        default=True,
        description="Global toggle for feature flags/remote config system.",
        validation_alias="FEATURE_FLAGS_ENABLED",
    )

    # --- Observability ---
    log_level: str = Field(default="INFO", description="Python logging level.", validation_alias="LOG_LEVEL")
    enable_request_logging: bool = Field(
        default=True,
        description="Enable request logging middleware (request id, timing).",
        validation_alias="ENABLE_REQUEST_LOGGING",
    )


@lru_cache(maxsize=1)
# PUBLIC_INTERFACE
def get_settings() -> Settings:
    """Return a cached Settings instance loaded from environment variables."""
    _load_dotenv_once()
    # BaseSettings will read from os.environ; we avoid requiring any mandatory vars yet.
    return Settings()
