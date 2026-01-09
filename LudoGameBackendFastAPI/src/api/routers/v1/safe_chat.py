from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from src.api.deps import AuthContext, auth_required, db_session_dep, rate_limit
from src.api.schemas.social import SafeChatSettings
from src.core.config import get_settings
from src.core.logging import get_logger
from src.infrastructure.cache import redis_client

logger = get_logger(__name__)

router = APIRouter(prefix="/safe-chat", tags=["safe-chat"])


def _redis_key(user_id: str) -> str:
    return f"safe_chat:settings:{user_id}"


def _default_settings() -> SafeChatSettings:
    # MVP defaults: safe chat on for guests, allow friend requests.
    return SafeChatSettings(safe_chat_enabled=True, allow_friend_requests=True)


@router.get(
    "/me",
    summary="Get my safe-chat settings",
    description="Returns per-user safe chat controls (MVP stored in Redis).",
    response_model=SafeChatSettings,
    operation_id="get_safe_chat_settings_v1",
)
# PUBLIC_INTERFACE
async def get_my_safe_chat_settings(
    request: Request,
    auth: Annotated[AuthContext, Depends(auth_required)],
) -> SafeChatSettings:
    """Get safe chat settings for the authenticated user."""
    await rate_limit(request, bucket="safe_chat_get", limit=120, window_seconds=60)

    settings = get_settings()
    if not settings.redis_url:
        return _default_settings()

    try:
        client = redis_client._get_client()  # type: ignore[attr-defined]
        raw = await client.get(_redis_key(str(auth.user.id)))
        if not raw:
            return _default_settings()
        data = json.loads(raw)
        return SafeChatSettings(**data)
    except Exception:
        logger.exception("Failed reading safe-chat settings; returning defaults")
        return _default_settings()


@router.put(
    "/me",
    summary="Update my safe-chat settings",
    description="Updates per-user safe chat controls (MVP stored in Redis).",
    response_model=SafeChatSettings,
    operation_id="update_safe_chat_settings_v1",
)
# PUBLIC_INTERFACE
async def update_my_safe_chat_settings(
    request: Request,
    body: SafeChatSettings,
    auth: Annotated[AuthContext, Depends(auth_required)],
    _: Annotated[AsyncSession, Depends(db_session_dep)],
) -> SafeChatSettings:
    """Update safe chat settings for the authenticated user."""
    await rate_limit(request, bucket="safe_chat_put", limit=60, window_seconds=60)

    settings = get_settings()
    if not settings.redis_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis not configured; safe-chat settings unavailable",
        )

    try:
        client = redis_client._get_client()  # type: ignore[attr-defined]
        await client.set(_redis_key(str(auth.user.id)), json.dumps(body.model_dump()), ex=30 * 24 * 3600)
        return body
    except Exception as exc:
        logger.exception("Failed writing safe-chat settings")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to save settings") from exc
