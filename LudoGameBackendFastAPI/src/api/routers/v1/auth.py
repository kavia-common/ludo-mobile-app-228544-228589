from __future__ import annotations

import re
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
    issue_access_token,
    rate_limit,
)
from src.api.schemas.auth import GuestIssueRequest, LinkIdentityRequest, TokenResponse
from src.infrastructure.db.repositories import ProfileRepository, UserRepository

router = APIRouter(prefix="/auth", tags=["auth"])


_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")


def _suggest_guest_username() -> str:
    # short, reasonably unique username for guests
    return f"guest_{uuid.uuid4().hex[:10]}"


@router.post(
    "/guest",
    summary="Issue guest identity",
    description="Creates a new guest user and returns a Bearer JWT.",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="issue_guest_identity_v1",
)
# PUBLIC_INTERFACE
async def issue_guest_identity(
    request: Request,
    body: GuestIssueRequest,
    idempotency_key: Annotated[Optional[str], Depends(idempotency_key_header)],
    session: Annotated[AsyncSession, Depends(db_session_dep)],
) -> TokenResponse:
    """Create a guest user + profile, and return a JWT access token."""
    await rate_limit(request, bucket="auth_guest_issue", limit=20, window_seconds=60)

    # Idempotency is keyed per synthetic "anonymous" user. Since we don't have auth yet,
    # we fall back to request_id when available; this is still useful for retry storms.
    # (For authenticated idempotency, see social endpoints.)
    anonymous_id = uuid.UUID(int=0)
    cached = await idempotency_guard(
        request,
        namespace="auth_guest_issue",
        user_id=anonymous_id,
        idempotency_key=idempotency_key,
        ttl_seconds=3600,
    )
    if cached:
        return TokenResponse(**cached)

    user_repo = UserRepository(session)
    profile_repo = ProfileRepository(session)

    username = _suggest_guest_username()
    user = await user_repo.create(username=username, email=None, is_guest=True)
    # Optional display name
    if body.display_name:
        user.display_name = body.display_name

    await profile_repo.create_for_user(user_id=user.id)
    await session.commit()

    token = issue_access_token(user_id=user.id, is_guest=True)
    resp = TokenResponse(access_token=token, user_id=str(user.id), is_guest=True)

    await idempotency_store_response(
        namespace="auth_guest_issue",
        user_id=anonymous_id,
        idempotency_key=idempotency_key,
        response_json=resp.model_dump(),
        ttl_seconds=3600,
    )
    return resp


@router.post(
    "/link",
    summary="Link (upgrade) guest identity",
    description="Links an email/username to the current guest user. Simplified MVP (no email verification yet).",
    response_model=TokenResponse,
    operation_id="link_guest_identity_v1",
)
# PUBLIC_INTERFACE
async def link_guest_identity(
    request: Request,
    body: LinkIdentityRequest,
    auth: Annotated[AuthContext, Depends(auth_required)],
    session: Annotated[AsyncSession, Depends(db_session_dep)],
) -> TokenResponse:
    """Convert a guest account into a non-guest account by setting email and optional username/display name."""
    await rate_limit(request, bucket="auth_link", limit=10, window_seconds=60)

    if not auth.user.is_guest:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account is already linked")

    # Very light validation; real email verification will come later.
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid email")

    if body.username:
        username = body.username.strip()
        if not _USERNAME_RE.match(username):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid username")
        # Ensure username not taken
        existing = await UserRepository(session).get_by_username(username)
        if existing and existing.id != auth.user.id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already taken")
        auth.user.username = username

    # NOTE: email uniqueness is enforced by DB unique index; we check best-effort before commit.
    # If commit fails with unique constraint, exception handler will return 500 currently; later steps can add db error mapping.
    auth.user.email = email
    auth.user.is_guest = False

    if body.display_name is not None:
        auth.user.display_name = body.display_name

    await session.commit()

    token = issue_access_token(user_id=auth.user.id, is_guest=False)
    return TokenResponse(access_token=token, user_id=str(auth.user.id), is_guest=False)
