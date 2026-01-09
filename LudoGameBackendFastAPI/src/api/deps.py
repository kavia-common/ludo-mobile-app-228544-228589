from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass
from typing import Annotated, Optional

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from src.core.config import get_settings
from src.core.logging import get_logger
from src.infrastructure.cache import redis_client
from src.infrastructure.db.session import get_session_factory
from src.infrastructure.db.repositories import ProfileRepository, UserRepository
from src.infrastructure.db import models as db_models

logger = get_logger(__name__)


@dataclass(frozen=True)
class AuthContext:
    """Authenticated request context."""

    user: db_models.User


async def _get_db_session() -> AsyncSession:
    """
    Create a new AsyncSession from the global session factory.

    Raises 503 if DB is not configured/initialized.
    """
    try:
        factory = get_session_factory()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not available",
        ) from exc

    return factory()


# PUBLIC_INTERFACE
async def db_session_dep() -> AsyncSession:
    """FastAPI dependency that yields an AsyncSession and closes it afterward."""
    session = await _get_db_session()
    try:
        yield session
    finally:
        await session.close()


def _bearer_token_from_header(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2:
        return None
    if parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def _require_setting(name: str, value: Optional[str]) -> str:
    if not value:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{name} not configured",
        )
    return value


def _b64url_encode(data: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    import base64

    # Pad for base64 decoding.
    padding = "=" * ((4 - (len(data) % 4)) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("utf-8"))


def _jwt_sign_hs256(secret: str, signing_input: bytes) -> bytes:
    import hmac

    return hmac.new(secret.encode("utf-8"), signing_input, digestmod="sha256").digest()


def _jwt_encode(payload: dict, secret: str) -> str:
    import json

    header = {"alg": "HS256", "typ": "JWT"}
    header_b = _b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    payload_b = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{header_b}.{payload_b}".encode("utf-8")
    sig = _b64url_encode(_jwt_sign_hs256(secret, signing_input))
    return f"{header_b}.{payload_b}.{sig}"


def _jwt_decode(token: str, secret: str) -> dict:
    import json
    import hmac

    try:
        header_b64, payload_b64, sig_b64 = token.split(".", 2)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    expected_sig = _jwt_sign_hs256(secret, signing_input)
    provided_sig = _b64url_decode(sig_b64)

    # Constant-time comparison
    if not hmac.compare_digest(expected_sig, provided_sig):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token signature")

    payload_raw = _b64url_decode(payload_b64)
    try:
        payload = json.loads(payload_raw.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload") from exc
    return payload


# PUBLIC_INTERFACE
def issue_access_token(*, user_id: uuid.UUID, is_guest: bool) -> str:
    """
    Issue a signed HS256 JWT for the given user.

    Notes:
    - Uses JWT_SECRET from environment; 503 if not configured.
    - Includes `sub` (user_id), `is_guest`, `iss`/`aud` if configured, and `exp`.
    """
    settings = get_settings()
    secret = _require_setting("JWT_SECRET", settings.jwt_secret)
    now = int(time.time())

    payload: dict = {
        "sub": str(user_id),
        "is_guest": bool(is_guest),
        "iat": now,
        "exp": now + int(settings.jwt_exp_seconds),
    }
    if settings.jwt_issuer:
        payload["iss"] = settings.jwt_issuer
    if settings.jwt_audience:
        payload["aud"] = settings.jwt_audience
    return _jwt_encode(payload, secret)


def _validate_claims(payload: dict) -> None:
    settings = get_settings()
    now = int(time.time())

    exp = payload.get("exp")
    if not isinstance(exp, int) or exp <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")

    if settings.jwt_issuer:
        if payload.get("iss") != settings.jwt_issuer:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token issuer")

    if settings.jwt_audience:
        aud = payload.get("aud")
        if aud != settings.jwt_audience:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token audience")


# PUBLIC_INTERFACE
async def auth_required(
    authorization: Annotated[Optional[str], Header(alias="Authorization")] = None,
    session: Annotated[AsyncSession, Depends(db_session_dep)] = None,
) -> AuthContext:
    """
    FastAPI dependency that authenticates a request using a Bearer JWT.

    Returns:
        AuthContext with the loaded User.

    Raises:
        401 if token is missing/invalid
        403 if user is disabled/deleted
        503 if JWT_SECRET is not configured
    """
    settings = get_settings()
    secret = _require_setting("JWT_SECRET", settings.jwt_secret)

    token = _bearer_token_from_header(authorization)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    payload = _jwt_decode(token, secret)
    _validate_claims(payload)

    sub = payload.get("sub")
    try:
        user_id = uuid.UUID(str(sub))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject") from exc

    user_repo = UserRepository(session)
    user = await user_repo.get(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown user")

    if user.status != db_models.UserStatus.active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User not active")

    return AuthContext(user=user)


# ---------------------------
# Rate limiting (best-effort)
# ---------------------------

def _rate_limit_key(request: Request, bucket: str) -> str:
    # Prefer authenticated identity; fallback to client IP (from Starlette).
    user_id = getattr(getattr(request.state, "auth_user_id", None), "__str__", lambda: "")()
    if user_id:
        ident = f"user:{user_id}"
    else:
        client_host = getattr(getattr(request.client, "host", None), "__str__", lambda: "unknown")()
        ident = f"ip:{client_host}"
    return f"ratelimit:{bucket}:{ident}"


# PUBLIC_INTERFACE
async def rate_limit(request: Request, *, bucket: str, limit: int, window_seconds: int) -> None:
    """
    Enforce a simple fixed-window rate limit.

    - Uses Redis INCR + EXPIRE when available.
    - If Redis not configured, falls back to allowing all requests (preview-safe).
    """
    settings = get_settings()
    if not settings.redis_url:
        return

    key = _rate_limit_key(request, bucket)
    try:
        # redis_client uses a global client; it throws if not initialized.
        client = redis_client._get_client()  # type: ignore[attr-defined]
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, window_seconds)
        if int(count) > int(limit):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
            )
    except HTTPException:
        raise
    except Exception:
        # Fail-open to keep preview resilient
        logger.exception("Rate limit check failed; allowing request")


# ---------------------------
# Idempotency (best-effort)
# ---------------------------

_IDEMPOTENCY_HEADER = "Idempotency-Key"


def _idem_cache_key(*, namespace: str, key: str, user_id: uuid.UUID) -> str:
    digest = hashlib.sha256(f"{namespace}:{user_id}:{key}".encode("utf-8")).hexdigest()
    return f"idempotency:{namespace}:{digest}"


# PUBLIC_INTERFACE
async def idempotency_guard(
    request: Request,
    *,
    namespace: str,
    user_id: uuid.UUID,
    idempotency_key: Optional[str],
    ttl_seconds: int = 24 * 3600,
) -> Optional[dict]:
    """
    Idempotency helper for write endpoints.

    Behavior:
    - If Redis is available and the key was used before, returns stored response JSON.
    - If first time, stores a placeholder marker and returns None.
    - If Redis unavailable, returns None (no idempotency guarantee).

    The handler should, on success, call `idempotency_store_response`.
    """
    settings = get_settings()
    if not settings.redis_url:
        return None
    if not idempotency_key:
        return None

    cache_key = _idem_cache_key(namespace=namespace, key=idempotency_key, user_id=user_id)
    try:
        client = redis_client._get_client()  # type: ignore[attr-defined]
        existing = await client.get(cache_key)
        if existing:
            import json

            return json.loads(existing)
        # set placeholder to prevent thundering herd; keep short until handler stores response
        await client.set(cache_key, "{}", ex=min(60, ttl_seconds), nx=True)
        return None
    except Exception:
        logger.exception("Idempotency lookup failed; allowing request")
        return None


# PUBLIC_INTERFACE
async def idempotency_store_response(
    *,
    namespace: str,
    user_id: uuid.UUID,
    idempotency_key: Optional[str],
    response_json: dict,
    ttl_seconds: int = 24 * 3600,
) -> None:
    """Store the final response for an idempotent request."""
    settings = get_settings()
    if not settings.redis_url or not idempotency_key:
        return
    cache_key = _idem_cache_key(namespace=namespace, key=idempotency_key, user_id=user_id)
    try:
        import json

        client = redis_client._get_client()  # type: ignore[attr-defined]
        await client.set(cache_key, json.dumps(response_json), ex=ttl_seconds)
    except Exception:
        logger.exception("Idempotency store failed")


# PUBLIC_INTERFACE
def idempotency_key_header(
    idempotency_key: Annotated[Optional[str], Header(alias=_IDEMPOTENCY_HEADER)] = None,
) -> Optional[str]:
    """FastAPI dependency to read the Idempotency-Key header."""
    return idempotency_key


# PUBLIC_INTERFACE
async def ensure_profile_exists(
    auth: Annotated[AuthContext, Depends(auth_required)],
    session: Annotated[AsyncSession, Depends(db_session_dep)],
) -> None:
    """Ensure a profile row exists for the authenticated user (best-effort create)."""
    repo = ProfileRepository(session)
    profile = await repo.get_by_user_id(auth.user.id)
    if profile is None:
        await repo.create_for_user(user_id=auth.user.id)
        await session.commit()
