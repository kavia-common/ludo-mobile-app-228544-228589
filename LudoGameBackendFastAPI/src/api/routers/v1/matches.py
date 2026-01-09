from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from src.api.deps import AuthContext, auth_required, db_session_dep, rate_limit
from src.api.schemas.gameplay import DiceRollResponse, MatchStateResponse
from src.application import gameplay_service
from src.core.config import get_settings
from src.core.logging import get_logger
from src.infrastructure.cache import redis_client

logger = get_logger(__name__)

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get(
    "/{match_id}/state",
    summary="Get current canonical match state",
    description="Returns the current server-authoritative match state. Uses Redis cache if available, otherwise rebuilds from DB turn snapshots.",
    response_model=MatchStateResponse,
    operation_id="match_get_state_v1",
)
# PUBLIC_INTERFACE
async def get_match_state(
    request: Request,
    match_id: str,
    auth: Annotated[AuthContext, Depends(auth_required)],
    session: Annotated[AsyncSession, Depends(db_session_dep)],
) -> MatchStateResponse:
    """Fetch the canonical match state snapshot for the given match_id."""
    await rate_limit(request, bucket="match_state_get", limit=120, window_seconds=60)

    try:
        match_uuid = uuid.UUID(match_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid match_id") from exc

    # Best effort: if Redis isn't configured, state comes from DB only.
    settings = get_settings()
    if settings.redis_url:
        cached = None
        try:
            cached = await redis_client.get_match_state(match_id=str(match_uuid))
        except Exception:
            logger.exception("Redis read failed; continuing with DB")
        if cached:
            return MatchStateResponse(match_id=str(match_uuid), state=cached, source="redis")

    try:
        state = await gameplay_service.get_current_state(session=session, match_id=match_uuid)
        return MatchStateResponse(match_id=str(match_uuid), state=state, source="db")
    except Exception as exc:
        raise gameplay_service.map_engine_error(exc) from exc


@router.post(
    "/{match_id}/roll",
    summary="Roll dice (server-side, audited)",
    description="Server-authoritative dice roll with audit record. Requires Redis for pending roll state and DB for persistence.",
    response_model=DiceRollResponse,
    operation_id="match_roll_dice_v1",
)
# PUBLIC_INTERFACE
async def roll_dice(
    request: Request,
    match_id: str,
    auth: Annotated[AuthContext, Depends(auth_required)],
    session: Annotated[AsyncSession, Depends(db_session_dep)],
) -> DiceRollResponse:
    """Roll a dice for the authenticated user if it is their turn."""
    await rate_limit(request, bucket="match_roll", limit=60, window_seconds=60)

    try:
        match_uuid = uuid.UUID(match_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid match_id") from exc

    # Redis must be configured for roll flow.
    settings = get_settings()
    if not settings.redis_url:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis not configured; rolling unavailable")

    try:
        resp = await gameplay_service.roll_dice(session=session, match_id=match_uuid, user_id=auth.user.id)
        return DiceRollResponse(**resp)
    except ValueError as exc:
        # missing RNG salt
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:
        raise gameplay_service.map_engine_error(exc) from exc
