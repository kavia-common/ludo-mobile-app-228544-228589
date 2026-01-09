from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from src.api.deps import AuthContext, auth_required, db_session_dep, ensure_profile_exists, rate_limit
from src.api.schemas.social import ProfileUpdateRequest, PublicProfile
from src.infrastructure.db.repositories import ProfileRepository, UserRepository

router = APIRouter(prefix="/players", tags=["players"])


def _to_public_profile(*, user, profile) -> PublicProfile:
    return PublicProfile(
        user_id=str(user.id),
        username=user.username,
        display_name=user.display_name,
        avatar_url=getattr(profile, "avatar_url", None),
        bio=getattr(profile, "bio", None),
        country_code=getattr(profile, "country_code", None),
        mmr=getattr(profile, "mmr", 1000),
        is_guest=bool(user.is_guest),
    )


@router.get(
    "/me",
    summary="Get my profile",
    description="Returns the authenticated user's profile.",
    response_model=PublicProfile,
    operation_id="get_my_profile_v1",
)
# PUBLIC_INTERFACE
async def get_my_profile(
    request: Request,
    auth: Annotated[AuthContext, Depends(auth_required)],
    session: Annotated[AsyncSession, Depends(db_session_dep)],
    _: Annotated[None, Depends(ensure_profile_exists)],
) -> PublicProfile:
    """Fetch the current user's public profile representation."""
    await rate_limit(request, bucket="players_me_get", limit=120, window_seconds=60)

    profile = await ProfileRepository(session).get_by_user_id(auth.user.id)
    if not profile:
        # Shouldn't happen due to ensure_profile_exists, but keep safe.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    return _to_public_profile(user=auth.user, profile=profile)


@router.patch(
    "/me",
    summary="Update my profile",
    description="Updates mutable profile fields for the authenticated user.",
    response_model=PublicProfile,
    operation_id="update_my_profile_v1",
)
# PUBLIC_INTERFACE
async def update_my_profile(
    request: Request,
    body: ProfileUpdateRequest,
    auth: Annotated[AuthContext, Depends(auth_required)],
    session: Annotated[AsyncSession, Depends(db_session_dep)],
    _: Annotated[None, Depends(ensure_profile_exists)],
) -> PublicProfile:
    """Update current user's profile fields."""
    await rate_limit(request, bucket="players_me_patch", limit=60, window_seconds=60)

    # Update user fields (display_name lives on users table)
    if body.display_name is not None:
        auth.user.display_name = body.display_name

    repo = ProfileRepository(session)
    profile = await repo.get_by_user_id(auth.user.id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")

    if body.avatar_url is not None:
        profile.avatar_url = body.avatar_url
    if body.bio is not None:
        profile.bio = body.bio
    if body.country_code is not None:
        profile.country_code = body.country_code

    await session.commit()
    return _to_public_profile(user=auth.user, profile=profile)


@router.get(
    "/{user_id}",
    summary="Get public profile by user id",
    description="Returns the public profile for a given user id.",
    response_model=PublicProfile,
    operation_id="get_public_profile_v1",
)
# PUBLIC_INTERFACE
async def get_public_profile(
    request: Request,
    user_id: str,
    auth: Annotated[AuthContext, Depends(auth_required)],
    session: Annotated[AsyncSession, Depends(db_session_dep)],
) -> PublicProfile:
    """Lookup a public profile by user_id (requires auth)."""
    await rate_limit(request, bucket="players_get_public", limit=120, window_seconds=60)

    try:
        uid = uuid.UUID(user_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid user_id") from exc

    user = await UserRepository(session).get(uid)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    profile = await ProfileRepository(session).get_by_user_id(uid)
    # profile may be missing for older seed data; return defaults
    if not profile:
        return PublicProfile(
            user_id=str(user.id),
            username=user.username,
            display_name=user.display_name,
            avatar_url=None,
            bio=None,
            country_code=None,
            mmr=1000,
            is_guest=bool(user.is_guest),
        )
    return _to_public_profile(user=user, profile=profile)
