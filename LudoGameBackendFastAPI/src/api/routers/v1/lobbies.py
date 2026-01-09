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
    LobbyCreateRequest,
    LobbyCreateResponse,
    LobbyInviteRequest,
    LobbyInviteResponse,
    LobbyJoinRequest,
    LobbyLeaveResponse,
    LobbyStartMatchRequest,
    LobbyStateResponse,
    MatchStartResponse,
)
from src.application import matchmaking_service
from src.application.session_service import create_match_with_players
from src.core.config import get_settings
from src.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/lobbies", tags=["lobbies"])


@router.post(
    "",
    summary="Create a private lobby",
    description="Creates a lobby in Redis and returns a join code. Idempotent with Idempotency-Key.",
    response_model=LobbyCreateResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="lobby_create_v1",
)
# PUBLIC_INTERFACE
async def lobby_create(
    request: Request,
    body: LobbyCreateRequest,
    auth: Annotated[AuthContext, Depends(auth_required)],
    idempotency_key: Annotated[Optional[str], Depends(idempotency_key_header)],
) -> LobbyCreateResponse:
    """Create a lobby (Redis-backed)."""
    await rate_limit(request, bucket="lobby_create", limit=10, window_seconds=60)

    cached = await idempotency_guard(
        request,
        namespace="lobby_create",
        user_id=auth.user.id,
        idempotency_key=idempotency_key,
        ttl_seconds=3600,
    )
    if cached:
        return LobbyCreateResponse(**cached)

    settings = get_settings()
    if not settings.redis_url:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis not configured; lobbies unavailable")

    try:
        meta = await matchmaking_service.lobby_create(
            owner_user_id=str(auth.user.id),
            max_players=body.max_players,
            mode=body.mode,
            is_ranked=body.is_ranked,
            is_private=body.is_private,
        )
        resp = LobbyCreateResponse(
            lobby_id=meta["lobby_id"],
            code=meta["code"],
            owner_user_id=str(auth.user.id),
            max_players=body.max_players,
            mode=body.mode,
            is_ranked=bool(body.is_ranked),
        )
        await idempotency_store_response(
            namespace="lobby_create",
            user_id=auth.user.id,
            idempotency_key=idempotency_key,
            response_json=resp.model_dump(),
            ttl_seconds=3600,
        )
        return resp
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.get(
    "/{lobby_id}",
    summary="Get lobby state",
    description="Returns lobby state from Redis.",
    response_model=LobbyStateResponse,
    operation_id="lobby_get_state_v1",
)
# PUBLIC_INTERFACE
async def lobby_get_state(
    request: Request,
    lobby_id: str,
    auth: Annotated[AuthContext, Depends(auth_required)],
) -> LobbyStateResponse:
    """Get lobby state snapshot."""
    await rate_limit(request, bucket="lobby_get", limit=120, window_seconds=60)

    settings = get_settings()
    if not settings.redis_url:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis not configured; lobbies unavailable")

    state = await matchmaking_service.lobby_get_state(lobby_id=lobby_id)
    if not state:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lobby not found")
    return LobbyStateResponse(**state)


@router.post(
    "/join",
    summary="Join a lobby",
    description="Join a lobby via lobby_id or join code. Idempotent with Idempotency-Key.",
    response_model=LobbyStateResponse,
    operation_id="lobby_join_v1",
)
# PUBLIC_INTERFACE
async def lobby_join(
    request: Request,
    body: LobbyJoinRequest,
    auth: Annotated[AuthContext, Depends(auth_required)],
    idempotency_key: Annotated[Optional[str], Depends(idempotency_key_header)],
) -> LobbyStateResponse:
    """Join a lobby; private lobbies require invite."""
    await rate_limit(request, bucket="lobby_join", limit=30, window_seconds=60)

    cached = await idempotency_guard(
        request,
        namespace="lobby_join",
        user_id=auth.user.id,
        idempotency_key=idempotency_key,
        ttl_seconds=3600,
    )
    if cached:
        return LobbyStateResponse(**cached)

    if not body.lobby_id and not body.code:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Either lobby_id or code is required")

    settings = get_settings()
    if not settings.redis_url:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis not configured; lobbies unavailable")

    try:
        state = await matchmaking_service.lobby_join(lobby_id=body.lobby_id, code=body.code, user_id=str(auth.user.id))
        resp = LobbyStateResponse(**state)
        await idempotency_store_response(
            namespace="lobby_join",
            user_id=auth.user.id,
            idempotency_key=idempotency_key,
            response_json=resp.model_dump(),
            ttl_seconds=3600,
        )
        return resp
    except RuntimeError as exc:
        # Map join errors to 409 to communicate "can't join"
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg) from exc
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=msg) from exc


@router.post(
    "/{lobby_id}/leave",
    summary="Leave a lobby",
    description="Leaves a lobby; if the owner leaves, the lobby is closed. Idempotent with Idempotency-Key.",
    response_model=LobbyLeaveResponse,
    operation_id="lobby_leave_v1",
)
# PUBLIC_INTERFACE
async def lobby_leave(
    request: Request,
    lobby_id: str,
    auth: Annotated[AuthContext, Depends(auth_required)],
    idempotency_key: Annotated[Optional[str], Depends(idempotency_key_header)],
) -> LobbyLeaveResponse:
    """Leave lobby (idempotent)."""
    await rate_limit(request, bucket="lobby_leave", limit=30, window_seconds=60)

    cached = await idempotency_guard(
        request,
        namespace="lobby_leave",
        user_id=auth.user.id,
        idempotency_key=idempotency_key,
        ttl_seconds=3600,
    )
    if cached:
        return LobbyLeaveResponse(**cached)

    settings = get_settings()
    if not settings.redis_url:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis not configured; lobbies unavailable")

    try:
        removed = await matchmaking_service.lobby_leave(lobby_id=lobby_id, user_id=str(auth.user.id))
        resp = LobbyLeaveResponse(lobby_id=lobby_id, left=bool(removed))
        await idempotency_store_response(
            namespace="lobby_leave",
            user_id=auth.user.id,
            idempotency_key=idempotency_key,
            response_json=resp.model_dump(),
            ttl_seconds=3600,
        )
        return resp
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.post(
    "/invite",
    summary="Invite a user to a lobby",
    description="Creates a lobby invite in Redis (best-effort). Idempotent with Idempotency-Key.",
    response_model=LobbyInviteResponse,
    operation_id="lobby_invite_v1",
)
# PUBLIC_INTERFACE
async def lobby_invite(
    request: Request,
    body: LobbyInviteRequest,
    auth: Annotated[AuthContext, Depends(auth_required)],
    idempotency_key: Annotated[Optional[str], Depends(idempotency_key_header)],
) -> LobbyInviteResponse:
    """Invite another user to a lobby."""
    await rate_limit(request, bucket="lobby_invite", limit=30, window_seconds=60)

    cached = await idempotency_guard(
        request,
        namespace="lobby_invite",
        user_id=auth.user.id,
        idempotency_key=idempotency_key,
        ttl_seconds=3600,
    )
    if cached:
        return LobbyInviteResponse(**cached)

    settings = get_settings()
    if not settings.redis_url:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis not configured; lobbies unavailable")

    try:
        # Only owner can invite (MVP)
        state = await matchmaking_service.lobby_get_state(lobby_id=body.lobby_id)
        if not state:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lobby not found")
        if state.get("owner_user_id") != str(auth.user.id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only owner can invite")

        # Validate UUID format early
        try:
            uuid.UUID(body.target_user_id)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid target_user_id") from exc

        await matchmaking_service.lobby_invite(
            lobby_id=body.lobby_id,
            inviter_user_id=str(auth.user.id),
            target_user_id=body.target_user_id,
        )
        resp = LobbyInviteResponse(lobby_id=body.lobby_id, invited_user_id=body.target_user_id)
        await idempotency_store_response(
            namespace="lobby_invite",
            user_id=auth.user.id,
            idempotency_key=idempotency_key,
            response_json=resp.model_dump(),
            ttl_seconds=3600,
        )
        return resp
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.post(
    "/start",
    summary="Start a match from a lobby",
    description="Owner starts a match from lobby membership; creates match + players in Postgres and marks lobby in Redis.",
    response_model=MatchStartResponse,
    operation_id="lobby_start_match_v1",
)
# PUBLIC_INTERFACE
async def lobby_start_match(
    request: Request,
    body: LobbyStartMatchRequest,
    auth: Annotated[AuthContext, Depends(auth_required)],
    session: Annotated[AsyncSession, Depends(db_session_dep)],
) -> MatchStartResponse:
    """Start match from lobby (requires DB and Redis)."""
    await rate_limit(request, bucket="lobby_start_match", limit=10, window_seconds=60)

    settings = get_settings()
    if not settings.redis_url:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis not configured; lobbies unavailable")

    # Must load lobby state
    state = await matchmaking_service.lobby_get_state(lobby_id=body.lobby_id)
    if not state:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lobby not found")
    if state.get("owner_user_id") != str(auth.user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only owner can start match")
    if state.get("status") != "open":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Lobby is not open")

    members: list[str] = list(state.get("members") or [])
    if len(members) < 2:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Not enough players to start")

    # DB required to persist match creation
    try:
        db_user_ids = [uuid.UUID(uid) for uid in members]
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Malformed lobby members") from exc

    mode = body.mode or str(state.get("mode") or "classic")
    is_ranked = bool(state.get("is_ranked", False))
    max_players = int(state.get("max_players") or 4)

    try:
        match = await create_match_with_players(
            session=session,
            user_ids=db_user_ids,
            mode=mode,
            is_ranked=is_ranked,
            max_players=max_players,
        )
        await session.commit()
    except Exception as exc:
        await session.rollback()
        logger.exception("Failed creating match from lobby")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to start match") from exc

    # Mark lobby in match (Redis)
    try:
        await matchmaking_service.lobby_mark_in_match(lobby_id=body.lobby_id, match_id=str(match.id))
    except Exception:
        # If marking fails, keep match created; lobby will remain open but match exists.
        logger.exception("Failed to mark lobby in match (match still created)")

    return MatchStartResponse(match_id=str(match.id), lobby_id=body.lobby_id)
