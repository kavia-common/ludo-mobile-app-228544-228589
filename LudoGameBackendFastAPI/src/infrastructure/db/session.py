from __future__ import annotations

from typing import AsyncIterator, Optional

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from src.core.config import Settings
from src.core.logging import get_logger

logger = get_logger(__name__)

_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def _normalize_async_driver(url: str) -> str:
    """
    Ensure SQLAlchemy uses an async driver.

    We accept:
    - postgresql+asyncpg://...
    - postgresql://... (will be normalized to postgresql+asyncpg://...)
    - postgresql+psycopg://... (sync driver; will be normalized to postgresql+asyncpg://...)
    """
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


# PUBLIC_INTERFACE
def init_engine(settings: Settings) -> None:
    """
    Initialize the global async SQLAlchemy engine and session factory.

    This function is idempotent; subsequent calls are no-ops.

    Args:
        settings: Application settings containing POSTGRES_URL (optional).

    Raises:
        RuntimeError: If POSTGRES_URL is not configured.
    """
    global _engine, _session_factory

    if _engine is not None and _session_factory is not None:
        return

    if not settings.postgres_url:
        raise RuntimeError("POSTGRES_URL is not configured; cannot initialize database engine.")

    database_url = _normalize_async_driver(settings.postgres_url)

    # Note: pool sizing is left to env tuning later.
    _engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        future=True,
    )
    _session_factory = async_sessionmaker(
        bind=_engine,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
    logger.info("Database engine initialized")


# PUBLIC_INTERFACE
async def close_engine() -> None:
    """
    Dispose the global SQLAlchemy engine.

    Safe to call even if engine is not initialized.
    """
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
    logger.info("Database engine disposed")


# PUBLIC_INTERFACE
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """
    Return the global AsyncSession factory.

    Raises:
        RuntimeError: If the engine has not been initialized.
    """
    if _session_factory is None:
        raise RuntimeError("Database session factory not initialized. Call init_engine() on startup.")
    return _session_factory


# PUBLIC_INTERFACE
async def session_scope() -> AsyncIterator[AsyncSession]:
    """
    Yield an AsyncSession in a context manager-like fashion.

    This is intentionally simple; application layer can manage transactions explicitly.
    """
    factory = get_session_factory()
    async with factory() as session:
        yield session
