from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

import redis.asyncio as redis

from src.core.config import Settings
from src.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class RedisKeys:
    """Centralized Redis key layout."""

    session_prefix: str = "session:"
    match_state_prefix: str = "match_state:"


_client: Optional[redis.Redis] = None
_keys = RedisKeys()


# PUBLIC_INTERFACE
def init_redis(settings: Settings) -> None:
    """
    Initialize the global Redis client (async).

    Args:
        settings: Application settings containing REDIS_URL (optional).

    Raises:
        RuntimeError: If REDIS_URL is not configured.
    """
    global _client
    if _client is not None:
        return

    if not settings.redis_url:
        raise RuntimeError("REDIS_URL is not configured; cannot initialize Redis client.")

    _client = redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
    logger.info("Redis client initialized")


# PUBLIC_INTERFACE
async def close_redis() -> None:
    """Close the global Redis client. Safe to call even if not initialized."""
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None
    logger.info("Redis client closed")


def _get_client() -> redis.Redis:
    if _client is None:
        raise RuntimeError("Redis client not initialized. Call init_redis() on startup.")
    return _client


# PUBLIC_INTERFACE
async def cache_session(*, session_id: str, payload: dict[str, Any], ttl_seconds: int = 3600) -> None:
    """Cache a session payload for quick lookup."""
    client = _get_client()
    key = f"{_keys.session_prefix}{session_id}"
    await client.set(key, json.dumps(payload), ex=ttl_seconds)


# PUBLIC_INTERFACE
async def get_session(*, session_id: str) -> Optional[dict[str, Any]]:
    """Get a cached session payload, if any."""
    client = _get_client()
    key = f"{_keys.session_prefix}{session_id}"
    raw = await client.get(key)
    return json.loads(raw) if raw else None


# PUBLIC_INTERFACE
async def delete_session(*, session_id: str) -> None:
    """Delete a session key."""
    client = _get_client()
    key = f"{_keys.session_prefix}{session_id}"
    await client.delete(key)


# PUBLIC_INTERFACE
async def cache_match_state(*, match_id: str, state: dict[str, Any], ttl_seconds: int = 3600) -> None:
    """Cache ephemeral match state for fast turn processing/reconnects."""
    client = _get_client()
    key = f"{_keys.match_state_prefix}{match_id}"
    await client.set(key, json.dumps(state), ex=ttl_seconds)


# PUBLIC_INTERFACE
async def get_match_state(*, match_id: str) -> Optional[dict[str, Any]]:
    """Fetch cached match state, if any."""
    client = _get_client()
    key = f"{_keys.match_state_prefix}{match_id}"
    raw = await client.get(key)
    return json.loads(raw) if raw else None


# PUBLIC_INTERFACE
async def delete_match_state(*, match_id: str) -> None:
    """Remove cached match state."""
    client = _get_client()
    key = f"{_keys.match_state_prefix}{match_id}"
    await client.delete(key)
