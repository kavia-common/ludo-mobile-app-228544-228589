from __future__ import annotations

import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
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
from src.api.schemas.gameplay import SubmitMoveRequest, TurnHistoryItem, TurnHistoryResponse
from src.application import gameplay_service
from src.infrastructure.db.repositories import TurnRepository

router = APIRouter(prefix="/turns", tags=["turns"])


@router.post(
    "",
    summary="Submit a move for the current turn",
    description="Submits a move selection after a server-side dice roll. Idempotent via Idempotency-Key when Redis is enabled.",
    response_model=dict,
    operation_id="turn_submit_v1",
)
# PUBLIC_INTERFACE
async def submit_turn(
    request: Request,
    body: SubmitMoveRequest,
    auth: Annotated[AuthContext, Depends(auth_required)],
    session: Annotated[AsyncSession, Depends(db_session_dep)],
    idempotency_key: Annotated[Optional[str], Depends(idempotency_key_header)],
) -> dict:
    """Submit a player's move selection; server validates and persists canonical turn + updated state."""
    await rate_limit(request, bucket="turn_submit", limit=60, window_seconds=60)

    # Best-effort idempotency (Redis-backed). Namespace includes match_id and turn_no.
    cached = await idempotency_guard(
        request,
        namespace=f"turn_submit:{body.match_id}:{int(body.turn_no)}",
        user_id=auth.user.id,
        idempotency_key=idempotency_key,
        ttl_seconds=24 * 3600,
    )
    if cached:
        return cached

    try:
        match_uuid = uuid.UUID(body.match_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid match_id") from exc

    try:
        result = await gameplay_service.submit_move(
            session=session,
            match_id=match_uuid,
            user_id=auth.user.id,
            turn_no=int(body.turn_no),
            piece_index=body.piece_index,
        )
        await idempotency_store_response(
            namespace=f"turn_submit:{body.match_id}:{int(body.turn_no)}",
            user_id=auth.user.id,
            idempotency_key=idempotency_key,
            response_json=result,
            ttl_seconds=24 * 3600,
        )
        return result
    except Exception as exc:
        raise gameplay_service.map_engine_error(exc) from exc


@router.get(
    "",
    summary="Get match turn history",
    description="Returns immutable turn log entries for auditing and reconnect support.",
    response_model=TurnHistoryResponse,
    operation_id="turn_history_v1",
)
# PUBLIC_INTERFACE
async def get_turn_history(
    request: Request,
    match_id: str = Query(..., description="Match id (UUID)"),
    limit: int = Query(200, ge=1, le=1000, description="Max items to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    auth: Annotated[AuthContext, Depends(auth_required)] = None,
    session: Annotated[AsyncSession, Depends(db_session_dep)] = None,
) -> TurnHistoryResponse:
    """Fetch turn history from Postgres (requires DB)."""
    await rate_limit(request, bucket="turn_history", limit=120, window_seconds=60)

    try:
        match_uuid = uuid.UUID(match_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid match_id") from exc

    page = await TurnRepository(session).list_turns(match_id=match_uuid, limit=int(limit), offset=int(offset))
    items = [
        TurnHistoryItem(
            turn_no=int(t.turn_no),
            user_id=(str(t.user_id) if t.user_id else None),
            payload=t.payload or {},
            committed_at=(t.committed_at.isoformat() if getattr(t, "committed_at", None) else None),
        )
        for t in page.items
    ]
    return TurnHistoryResponse(match_id=str(match_uuid), items=items, limit=int(limit), offset=int(offset))
