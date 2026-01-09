from __future__ import annotations

import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from src.api.deps import (
    AuthContext,
    auth_required,
    db_session_dep,
    idempotency_guard,
    idempotency_key_header,
    idempotency_store_response,
    rate_limit,
)
from src.api.schemas.matchmaking import (
    MatchStartResponse,
    QuickPlayDequeueRequest,
    QuickPlayDequeueResponse,
    QuickPlayEnqueueRequest,
    QuickPlayEnqueueResponse,
)
from src.application import matchmaking_service
from src.application.session_service import create_match_with_players
from src.core.config import get_settings
from src.core.logging import get_logger
from src.infrastructure.db.repositories import ProfileRepository

logger = get_logger(__name__)

router = APIRouter(prefix="/matchmaking", tags=["matchmaking"])


async def _require_db_session(session: AsyncSession) -> AsyncSession:
    # session is already required by dep; placeholder for future.
    return session


async def _maybe_get_mmr(session: AsyncSession, user_id: uuid.UUID) -> int:
    # MMR bucketing can be added later; for now it's informational.
    prof = await ProfileRepository(session).get_by_user_id(user_id)
    return int(getattr(prof, "mmr", 1000) if prof else 1000)


@router.post(
    "/quickplay/enqueue",
    summary="Enqueue for quick-play matchmaking",
    description="Adds the authenticated user to a Redis-backed quick-play queue (ranked/unranked). Idempotent with Idempotency-Key.",
    response_model=QuickPlayEnqueueResponse,
    operation_id="quickplay_enqueue_v1",
)
# PUBLIC_INTERFACE
async def quickplay_enqueue(
    request: Request,
    body: QuickPlayEnqueueRequest,
    auth: Annotated[AuthContext, Depends(auth_required)],
    idempotency_key: Annotated[Optional[str], Depends(idempotency_key_header)],
    session: Annotated[AsyncSession, Depends(db_session_dep)],
) -> QuickPlayEnqueueResponse:
    """Enqueue into matchmaking queue; best-effort uses Redis and returns a ticket id."""
    await rate_limit(request, bucket="mm_enqueue", limit=30, window_seconds=60)

    cached = await idempotency_guard(
        request,
        namespace="mm_quickplay_enqueue",
        user_id=auth.user.id,
        idempotency_key=idempotency_key,
        ttl_seconds=3600,
    )
    if cached:
        return QuickPlayEnqueueResponse(**cached)

    settings = get_settings()
    if not settings.redis_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis not configured; matchmaking unavailable",
        )

    # Touch DB (optional) to ensure profile exists / read MMR; DB may be unavailable in preview.
    try:
        _ = await _maybe_get_mmr(session, auth.user.id)
    except Exception:
        # Don't fail queueing because DB is down
        logger.exception("MMR lookup failed (continuing)")

    try:
        ticket_id = await matchmaking_service.quickplay_enqueue(
            user_id=str(auth.user.id),
            queue=body.queue,
            mode=body.mode,
            max_players=body.max_players,
        )
        resp = QuickPlayEnqueueResponse(queue=body.queue, ticket_id=ticket_id)
        await idempotency_store_response(
            namespace="mm_quickplay_enqueue",
            user_id=auth.user.id,
            idempotency_key=idempotency_key,
            response_json=resp.model_dump(),
            ttl_seconds=3600,
        )
        return resp
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.post(
    "/quickplay/dequeue",
    summary="Dequeue from quick-play matchmaking",
    description="Removes the authenticated user from a Redis-backed quick-play queue. Idempotent with Idempotency-Key.",
    response_model=QuickPlayDequeueResponse,
    operation_id="quickplay_dequeue_v1",
)
# PUBLIC_INTERFACE
async def quickplay_dequeue(
    request: Request,
    body: QuickPlayDequeueRequest,
    auth: Annotated[AuthContext, Depends(auth_required)],
    idempotency_key: Annotated[Optional[str], Depends(idempotency_key_header)],
) -> QuickPlayDequeueResponse:
    """Dequeue from matchmaking queue (best-effort)."""
    await rate_limit(request, bucket="mm_dequeue", limit=30, window_seconds=60)

    cached = await idempotency_guard(
        request,
        namespace="mm_quickplay_dequeue",
        user_id=auth.user.id,
        idempotency_key=idempotency_key,
        ttl_seconds=3600,
    )
    if cached:
        return QuickPlayDequeueResponse(**cached)

    settings = get_settings()
    if not settings.redis_url:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis not configured; matchmaking unavailable")

    try:
        removed = await matchmaking_service.quickplay_dequeue(user_id=str(auth.user.id), queue=body.queue)
        resp = QuickPlayDequeueResponse(queue=body.queue, removed=bool(removed))
        await idempotency_store_response(
            namespace="mm_quickplay_dequeue",
            user_id=auth.user.id,
            idempotency_key=idempotency_key,
            response_json=resp.model_dump(),
            ttl_seconds=3600,
        )
        return resp
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.post(
    "/quickplay/try-start",
    summary="Try to start a match from quick-play queue",
    description=(
        "Attempts to form a match by popping a full group from the specified queue. "
        "If successful, persists the match to Postgres when DB is available. "
        "This endpoint is safe to call repeatedly by clients (rate-limited)."
    ),
    response_model=MatchStartResponse,
    operation_id="quickplay_try_start_v1",
)
# PUBLIC_INTERFACE
async def quickplay_try_start(
    request: Request,
    queue: str = "unranked",
    desired_players: int = 4,
    auth: Annotated[AuthContext, Depends(auth_required)] = None,
    session: Annotated[AsyncSession, Depends(db_session_dep)] = None,
) -> MatchStartResponse:
    """
    Try to start a match by matching N tickets from the queue.

    Returns:
        MatchStartResponse: match_id if started.

    Notes:
        - Requires Redis configured.
        - Requires DB for persistence; if DB unavailable, returns 503 (because match creation must be persisted).
    """
    await rate_limit(request, bucket="mm_try_start", limit=20, window_seconds=60)

    settings = get_settings()
    if not settings.redis_url:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis not configured; matchmaking unavailable")

    if desired_players < 2 or desired_players > 4:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="desired_players must be 2..4")

    matched = await matchmaking_service.quickplay_try_match(queue=queue, desired_players=desired_players)
    if not matched:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Not enough players in queue yet")

    # DB persistence is required for canonical match id (step requirement).
    try:
        db_user_ids = [uuid.UUID(str(x["user_id"])) for x in matched]
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Malformed matchmaking ticket") from exc

    try:
        await _require_db_session(session)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database not available; cannot start match") from exc

    # Use the first ticket as source of mode/is_ranked/max_players (they should match in future)
    mode = str(matched[0].get("mode") or "classic")
    is_ranked = bool(queue == "ranked")
    max_players = int(matched[0].get("max_players") or desired_players)

    try:
        match = await create_match_with_players(
            session=session,
            user_ids=db_user_ids,
            mode=mode,
            is_ranked=is_ranked,
            max_players=max_players,
        )
        await session.commit()
        return MatchStartResponse(match_id=str(match.id), lobby_id=None)
    except Exception as exc:
        await session.rollback()
        logger.exception("Failed creating match from queue")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to start match") from exc
