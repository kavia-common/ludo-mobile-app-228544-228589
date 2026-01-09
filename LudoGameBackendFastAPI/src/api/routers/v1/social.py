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
from src.api.schemas.social import FriendActionRequest, FriendListResponse, FriendRelationship
from src.infrastructure.db.repositories import FriendsRepository, UserRepository

router = APIRouter(prefix="/social", tags=["social"])


def _rel_to_schema(rel) -> FriendRelationship:
    return FriendRelationship(user_id=str(rel.user_id), friend_user_id=str(rel.friend_user_id), is_blocked=bool(rel.is_blocked))


@router.get(
    "/friends",
    summary="List friends",
    description="Lists directed friend relationships for the current user.",
    response_model=FriendListResponse,
    operation_id="list_friends_v1",
)
# PUBLIC_INTERFACE
async def list_friends(
    request: Request,
    auth: Annotated[AuthContext, Depends(auth_required)],
    session: Annotated[AsyncSession, Depends(db_session_dep)],
    limit: int = Query(default=50, ge=1, le=200, description="Page size."),
    offset: int = Query(default=0, ge=0, description="Offset for pagination."),
) -> FriendListResponse:
    """List current user's friends (directed)."""
    await rate_limit(request, bucket="social_friends_list", limit=120, window_seconds=60)

    page = await FriendsRepository(session).list_friends(user_id=auth.user.id, limit=limit, offset=offset)
    return FriendListResponse(items=[_rel_to_schema(x) for x in page.items], limit=page.limit, offset=page.offset)


@router.post(
    "/friends",
    summary="Add friend",
    description="Creates a directed friendship row for the current user. Idempotent with Idempotency-Key.",
    response_model=FriendRelationship,
    status_code=status.HTTP_201_CREATED,
    operation_id="add_friend_v1",
)
# PUBLIC_INTERFACE
async def add_friend(
    request: Request,
    body: FriendActionRequest,
    auth: Annotated[AuthContext, Depends(auth_required)],
    idempotency_key: Annotated[Optional[str], Depends(idempotency_key_header)],
    session: Annotated[AsyncSession, Depends(db_session_dep)],
) -> FriendRelationship:
    """Add a friend relationship (directed)."""
    await rate_limit(request, bucket="social_friends_add", limit=60, window_seconds=60)

    cached = await idempotency_guard(
        request,
        namespace="social_add_friend",
        user_id=auth.user.id,
        idempotency_key=idempotency_key,
    )
    if cached:
        return FriendRelationship(**cached)

    try:
        target_id = uuid.UUID(body.target_user_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid target_user_id") from exc
    if target_id == auth.user.id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Cannot friend self")

    # Validate target exists
    target = await UserRepository(session).get(target_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target user not found")

    repo = FriendsRepository(session)
    try:
        rel = await repo.add_friend(user_id=auth.user.id, friend_user_id=target_id)
        await session.commit()
    except Exception:
        # Might be unique constraint; treat as idempotent success if already exists.
        await session.rollback()
        # Best-effort fetch the relationship
        page = await repo.list_friends(user_id=auth.user.id, limit=200, offset=0)
        existing = next((x for x in page.items if x.friend_user_id == target_id), None)
        if not existing:
            raise
        rel = existing

    resp = _rel_to_schema(rel)
    await idempotency_store_response(
        namespace="social_add_friend",
        user_id=auth.user.id,
        idempotency_key=idempotency_key,
        response_json=resp.model_dump(),
    )
    return resp


@router.delete(
    "/friends/{target_user_id}",
    summary="Remove friend",
    description="Deletes a directed friendship row. Idempotent with Idempotency-Key.",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="remove_friend_v1",
)
# PUBLIC_INTERFACE
async def remove_friend(
    request: Request,
    target_user_id: str,
    auth: Annotated[AuthContext, Depends(auth_required)],
    idempotency_key: Annotated[Optional[str], Depends(idempotency_key_header)],
    session: Annotated[AsyncSession, Depends(db_session_dep)],
) -> None:
    """Remove a friend relationship (directed)."""
    await rate_limit(request, bucket="social_friends_remove", limit=60, window_seconds=60)

    cached = await idempotency_guard(
        request,
        namespace="social_remove_friend",
        user_id=auth.user.id,
        idempotency_key=idempotency_key,
    )
    if cached is not None:
        # For 204 endpoints we store {} and treat as success.
        return

    try:
        target_id = uuid.UUID(target_user_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid target_user_id") from exc

    await FriendsRepository(session).remove_friend(user_id=auth.user.id, friend_user_id=target_id)
    await session.commit()

    await idempotency_store_response(
        namespace="social_remove_friend",
        user_id=auth.user.id,
        idempotency_key=idempotency_key,
        response_json={},
    )
    return


@router.post(
    "/block",
    summary="Block user",
    description="Blocks a user by setting is_blocked on a directed relationship. Creates the row if needed. Idempotent with Idempotency-Key.",
    response_model=FriendRelationship,
    operation_id="block_user_v1",
)
# PUBLIC_INTERFACE
async def block_user(
    request: Request,
    body: FriendActionRequest,
    auth: Annotated[AuthContext, Depends(auth_required)],
    idempotency_key: Annotated[Optional[str], Depends(idempotency_key_header)],
    session: Annotated[AsyncSession, Depends(db_session_dep)],
) -> FriendRelationship:
    """Block a user (directed)."""
    await rate_limit(request, bucket="social_block", limit=30, window_seconds=60)

    cached = await idempotency_guard(
        request,
        namespace="social_block",
        user_id=auth.user.id,
        idempotency_key=idempotency_key,
    )
    if cached:
        return FriendRelationship(**cached)

    try:
        target_id = uuid.UUID(body.target_user_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid target_user_id") from exc
    if target_id == auth.user.id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Cannot block self")

    # ensure target exists
    target = await UserRepository(session).get(target_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target user not found")

    repo = FriendsRepository(session)
    # create row if missing
    try:
        rel = await repo.add_friend(user_id=auth.user.id, friend_user_id=target_id)
    except Exception:
        await session.rollback()
        # Might already exist
        page = await repo.list_friends(user_id=auth.user.id, limit=200, offset=0)
        rel = next((x for x in page.items if x.friend_user_id == target_id), None)
        if not rel:
            raise

    rel.is_blocked = True
    await session.commit()

    resp = _rel_to_schema(rel)
    await idempotency_store_response(
        namespace="social_block",
        user_id=auth.user.id,
        idempotency_key=idempotency_key,
        response_json=resp.model_dump(),
    )
    return resp


@router.post(
    "/unblock",
    summary="Unblock user",
    description="Unblocks a user by clearing is_blocked on a directed relationship. Idempotent with Idempotency-Key.",
    response_model=FriendRelationship,
    operation_id="unblock_user_v1",
)
# PUBLIC_INTERFACE
async def unblock_user(
    request: Request,
    body: FriendActionRequest,
    auth: Annotated[AuthContext, Depends(auth_required)],
    idempotency_key: Annotated[Optional[str], Depends(idempotency_key_header)],
    session: Annotated[AsyncSession, Depends(db_session_dep)],
) -> FriendRelationship:
    """Unblock a user (directed)."""
    await rate_limit(request, bucket="social_unblock", limit=30, window_seconds=60)

    cached = await idempotency_guard(
        request,
        namespace="social_unblock",
        user_id=auth.user.id,
        idempotency_key=idempotency_key,
    )
    if cached:
        return FriendRelationship(**cached)

    try:
        target_id = uuid.UUID(body.target_user_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid target_user_id") from exc

    repo = FriendsRepository(session)
    page = await repo.list_friends(user_id=auth.user.id, limit=200, offset=0)
    rel = next((x for x in page.items if x.friend_user_id == target_id), None)
    if not rel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Relationship not found")

    rel.is_blocked = False
    await session.commit()

    resp = _rel_to_schema(rel)
    await idempotency_store_response(
        namespace="social_unblock",
        user_id=auth.user.id,
        idempotency_key=idempotency_key,
        response_json=resp.model_dump(),
    )
    return resp
