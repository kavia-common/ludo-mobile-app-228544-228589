from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.errors import register_exception_handlers
from src.api.middleware.request_context import RequestContextMiddleware
from src.api.routers import api_router
from src.core.config import get_settings
from src.core.logging import configure_logging, get_logger
from src.infrastructure.cache.redis_client import close_redis, init_redis
from src.infrastructure.db.seeds import seed_initial_data
from src.infrastructure.db.session import close_engine, get_session_factory, init_engine


def _parse_csv(value: object) -> list[str]:
    """
    Parse environment variable values that may arrive as:
    - list[str] already
    - comma-separated string
    - "*" wildcard
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    s = str(value).strip()
    if not s:
        return []
    if s == "*":
        return ["*"]
    return [part.strip() for part in s.split(",") if part.strip()]


# PUBLIC_INTERFACE
def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        FastAPI: ASGI application instance.
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger(__name__)

    openapi_tags = [
        {"name": "health", "description": "Service health and readiness endpoints."},
    ]

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Server-authoritative backend for Ludo gameplay, matchmaking, and player services.",
        openapi_tags=openapi_tags,
    )

    # Best-effort infra init:
    # - In preview environments env vars may not be configured yet.
    # - We keep the API up even if DB/Redis aren't available, and later endpoints will enforce requirements.
    @app.on_event("startup")
    async def on_startup() -> None:
        if settings.postgres_url:
            try:
                init_engine(settings)
                # Apply seeds in a small transaction.
                factory = get_session_factory()
                async with factory() as session:
                    async with session.begin():
                        await seed_initial_data(session)
                logger.info("Database configured and seeds applied")
            except Exception:
                logger.exception("Database initialization failed (continuing without DB)")
        else:
            logger.info("POSTGRES_URL not set; running without database")

        if settings.redis_url:
            try:
                init_redis(settings)
                logger.info("Redis configured")
            except Exception:
                logger.exception("Redis initialization failed (continuing without Redis)")
        else:
            logger.info("REDIS_URL not set; running without redis cache")

    @app.on_event("shutdown")
    async def on_shutdown() -> None:
        # Close Redis/DB if they were initialized.
        try:
            await close_redis()
        except Exception:
            logger.exception("Error closing Redis")

        try:
            await close_engine()
        except Exception:
            logger.exception("Error closing database engine")

    # Middleware: request context (request id + timing)
    if settings.enable_request_logging:
        app.add_middleware(RequestContextMiddleware)

    # Middleware: CORS
    allow_origins = _parse_csv(settings.allowed_origins) or ["*"]
    allow_methods = _parse_csv(settings.allowed_methods) or ["*"]
    allow_headers = _parse_csv(settings.allowed_headers) or ["*"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=bool(settings.cors_allow_credentials),
        allow_methods=allow_methods,
        allow_headers=allow_headers,
        max_age=int(settings.cors_max_age),
    )

    # Routes: keep existing health at GET /, and also provide versioned /v1/health.
    @app.get("/", summary="Health check")
    # PUBLIC_INTERFACE
    def health_check() -> dict:
        """Legacy/root health check maintained for compatibility."""
        return {"message": "Healthy"}

    app.include_router(api_router)

    # Centralized exception handlers
    register_exception_handlers(app)

    return app


# Stable ASGI entrypoint required by task: src.api.main:app
app = create_app()
