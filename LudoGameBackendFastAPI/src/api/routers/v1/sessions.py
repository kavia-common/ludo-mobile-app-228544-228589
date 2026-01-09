from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from src.api.deps import AuthContext, auth_required, db_session_dep, rate_limit
from src.api.schemas.matchmaking import (
    PresenceHeartbeatRequest,
    PresenceHeartbeatResponse,
    RestoreStateRequest,
    RestoreStateResponse,
)
from src.application import matchmaking_service
from src.application.session_service import restore_match_state
from src.core.config import get_settings
from src.core.logging import get_logger
from src.infrastructure.cache import redis_client

logger = get_logger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post(
    "/presence/heartbeat",
    summary="Presence heartbeat",
    description="Records presence for match/lobby scopes with TTL in Redis. If Redis is not configured, succeeds as a no-op.",
    response_model=PresenceHeartbeatResponse,
    operation_id="presence_heartbeat_v1",
)
# PUBLIC_INTERFACE
async def presence_heartbeat(
    request: Request,
    body: PresenceHeartbeatRequest,
    auth: Annotated[AuthContext, Depends(auth_required)],
) -> PresenceHeartbeatResponse:
    """Heartbeat endpoint to help server track reconnectability/presence timeouts."""
    await rate_limit(request, bucket="presence_heartbeat", limit=120, window_seconds=60)

    ttl = await matchmaking_service.presence_heartbeat(
        user_id=str(auth.user.id),
        scope=body.scope,
        scope_id=body.scope_id,
        status=body.status,
    )
    return PresenceHeartbeatResponse(ok=True, expires_in_seconds=int(ttl))


@router.post(
    "/restore",
    summary="Restore active match state",
    description="Returns match metadata, players, recent turns from Postgres (if available), and cached match_state from Redis (if present).",
    response_model=RestoreStateResponse,
    operation_id="restore_match_state_v1",
)
# PUBLIC_INTERFACE
async def restore_state(
    request: Request,
    body: RestoreStateRequest,
    auth: Annotated[AuthContext, Depends(auth_required)],
    session: Annotated[AsyncSession, Depends(db_session_dep)],
) -> RestoreStateResponse:
    """Reconnect/state-restore endpoint for clients after disconnect."""
    await rate_limit(request, bucket="restore_state", limit=60, window_seconds=60)

    try:
        match_uuid = uuid.UUID(body.match_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid match_id") from exc

    cached_state = None
    settings = get_settings()
    if settings.redis_url:
        try:
            cached_state = await redis_client.get_match_state(match_id=str(match_uuid))
        except Exception:
            logger.exception("Failed reading cached match state; continuing")

    # DB may be unavailable; the db_session_dep already enforces DB availability.
    # If we want even more graceful behavior, we can add a separate endpoint that does not require DB.
    try:
        payload = await restore_match_state(session=session, match_id=match_uuid, cached_state=cached_state)
        return RestoreStateResponse(**payload)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Restore state failed")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to restore state") from exc
