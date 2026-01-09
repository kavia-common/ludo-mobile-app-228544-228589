from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.errors import register_exception_handlers
from src.api.middleware.request_context import RequestContextMiddleware
from src.api.routers import api_router
from src.core.config import get_settings
from src.core.logging import configure_logging


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

    openapi_tags = [
        {"name": "health", "description": "Service health and readiness endpoints."},
    ]

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Server-authoritative backend for Ludo gameplay, matchmaking, and player services.",
        openapi_tags=openapi_tags,
    )

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
