from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from src.core.config import get_settings
from src.core.logging import get_logger
from src.domain.gameplay.exceptions import DegradedModeError, EngineValidationError
from src.domain.gameplay.rng import get_rng_service
from src.domain.gameplay.rules import apply_turn, start_match_state
from src.infrastructure.cache import redis_client
from src.infrastructure.db.models import Turn
from src.infrastructure.db.repositories import MatchPlayerRepository, MatchRepository, TurnRepository
from src.infrastructure.events.publisher import get_event_publisher

logger = get_logger(__name__)

_MATCH_STATE_TTL_SECONDS = 6 * 3600


def _require_db(session: Optional[AsyncSession]) -> AsyncSession:
    if session is None:
        raise DegradedModeError("Database not available")
    return session


def _require_redis() -> None:
    settings = get_settings()
    if not settings.redis_url:
        raise DegradedModeError("Redis not available")


async def _load_players(session: AsyncSession, match_id: uuid.UUID) -> list[dict[str, Any]]:
    players = await MatchPlayerRepository(session).list_by_match(match_id)
    return [
        {"user_id": str(p.user_id), "seat_no": int(p.seat_no), "color": str(p.color), "has_left": bool(p.has_left)}
        for p in players
    ]


async def _rebuild_state_from_turns(session: AsyncSession, *, match_id: uuid.UUID) -> dict[str, Any]:
    """
    Rebuild canonical match state by replaying turns from DB.

    We store full state snapshots in turn payloads to keep rebuild deterministic and easy.
    """
    match = await MatchRepository(session).get(match_id)
    if not match:
        raise EngineValidationError("Match not found")

    players = await _load_players(session, match_id)
    state = start_match_state(players=players, mode=str(match.mode or "classic"))

    page = await TurnRepository(session).list_turns(match_id=match_id, limit=10000, offset=0)
    for t in page.items:
        payload = t.payload or {}
        snapshot = payload.get("state_after")
        if isinstance(snapshot, dict):
            state = snapshot
    return state


async def _get_cached_state(match_id: str) -> Optional[dict[str, Any]]:
    try:
        return await redis_client.get_match_state(match_id=match_id)
    except Exception:
        logger.exception("Failed reading match state from Redis")
        return None


async def _cache_state(match_id: str, state: dict) -> None:
    try:
        await redis_client.cache_match_state(match_id=match_id, state=state, ttl_seconds=_MATCH_STATE_TTL_SECONDS)
    except Exception:
        logger.exception("Failed caching match state (best-effort)")


async def _get_current_turn_no_from_db(session: AsyncSession, match_id: uuid.UUID) -> int:
    stmt = select(Turn.turn_no).where(Turn.match_id == match_id).order_by(Turn.turn_no.desc()).limit(1)
    row = (await session.execute(stmt)).first()
    if not row:
        return 0
    return int(row[0])


def _player_color_for_user(state: dict, user_id: str) -> Optional[str]:
    for p in state.get("players") or []:
        if str(p.get("user_id")) == str(user_id):
            return str(p.get("color"))
    return None


# PUBLIC_INTERFACE
async def ensure_match_initialized(*, session: AsyncSession, match_id: uuid.UUID) -> dict[str, Any]:
    """
    Ensure a match has a canonical state cached (best-effort) and at least a genesis snapshot in DB.

    Behavior:
    - If Redis has state, return it.
    - Else rebuild from DB turn snapshots if any exist.
    - Else create initial state and store as a genesis turn_no=0 snapshot.

    Returns:
        dict: canonical match state
    """
    # Try cache first.
    cached = await _get_cached_state(str(match_id))
    if cached:
        return cached

    # Rebuild from DB if possible.
    state = await _rebuild_state_from_turns(session, match_id=match_id)

    # If no turns exist, create a genesis snapshot turn 0 (idempotent with unique constraint).
    current_last = await _get_current_turn_no_from_db(session, match_id)
    if current_last == 0:
        try:
            TurnRepository(session).append_turn  # keep import used
            await TurnRepository(session).append_turn(
                match_id=match_id,
                turn_no=0,
                user_id=None,
                payload={"type": "genesis", "state_after": state},
            )
            await session.commit()
        except IntegrityError:
            await session.rollback()
        except Exception:
            await session.rollback()
            logger.exception("Failed creating genesis turn; continuing without it")

    await _cache_state(str(match_id), state)
    return state


# PUBLIC_INTERFACE
async def get_current_state(*, session: AsyncSession, match_id: uuid.UUID) -> dict[str, Any]:
    """
    Get current canonical state for a match.

    Requires DB (for fallback rebuild) but uses Redis as fast path.
    """
    state = await _get_cached_state(str(match_id))
    if state:
        return state
    state = await ensure_match_initialized(session=session, match_id=match_id)
    return state


# PUBLIC_INTERFACE
async def roll_dice(*, session: AsyncSession, match_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, Any]:
    """
    Server-side dice roll. Stores a pending roll in Redis and writes an audited roll to DB as a Turn.

    Idempotency:
    - If a pending roll exists for the match for this same user and current turn_no, returns it.
    - Otherwise generates a new audited roll, persists it as a Turn payload and caches pending roll.

    Returns:
        dict: {turn_no, dice, rng_audit, current_color}
    """
    _require_redis()
    # Ensure state exists.
    state = await get_current_state(session=session, match_id=match_id)
    player_color = _player_color_for_user(state, str(user_id))
    if not player_color:
        raise EngineValidationError("User is not a player in this match")

    if str(state.get("current_color")) != player_color:
        raise EngineValidationError("Not your turn")

    # If pending roll already exists and matches turn/user, return it.
    pending = None
    try:
        pending = await redis_client.get_pending_roll(match_id=str(match_id))
    except Exception:
        logger.exception("Failed reading pending roll; continuing")
    if pending and str(pending.get("user_id")) == str(user_id) and int(pending.get("turn_no")) == int(state.get("turn_no") or 1):
        return pending

    # Roll using verifiable RNG.
    rng = get_rng_service()
    audit = rng.roll_d6(match_id=str(match_id), player_id=str(user_id), turn_no=int(state.get("turn_no") or 1))
    dice = int(audit.dice_value)

    # Persist as a dedicated turn event (roll) with unique (match_id, turn_no) enforced by DB,
    # so we store roll as a turn_no=current_turn_no (state.turn_no).
    turn_no = int(state.get("turn_no") or 1)

    payload = {
        "type": "roll",
        "turn_no": turn_no,
        "user_id": str(user_id),
        "color": player_color,
        "dice": dice,
        "rng_audit": {
            "match_id": audit.match_id,
            "player_id": audit.player_id,
            "turn_no": audit.turn_no,
            "rolled_at_ms": audit.rolled_at_ms,
            "seed_nonce_hex": audit.seed_nonce_hex,
            "commitment_hash": audit.commitment_hash,
            "dice_value": audit.dice_value,
        },
        # Include state snapshot after roll for easier rebuild.
        "state_after": {**state, "dice": dice, "dice_rolled_by": str(user_id)},
    }

    repo = TurnRepository(session)
    try:
        await repo.append_turn(match_id=match_id, turn_no=turn_no, user_id=user_id, payload=payload)
        await session.commit()
    except IntegrityError:
        # Another roll already wrote the turn for this turn_no; fetch it and return (idempotent-ish).
        await session.rollback()
        existing = await session.execute(
            select(Turn).where(Turn.match_id == match_id, Turn.turn_no == turn_no).limit(1)
        )
        t = existing.scalar_one_or_none()
        if not t:
            raise
        pl = t.payload or {}
        if pl.get("type") == "roll":
            # Cache pending from existing.
            resp = {"turn_no": turn_no, "dice": pl.get("dice"), "rng_audit": pl.get("rng_audit"), "user_id": str(user_id), "current_color": player_color}
            try:
                await redis_client.cache_pending_roll(match_id=str(match_id), payload=resp, ttl_seconds=300)
            except Exception:
                pass
            return resp
        raise EngineValidationError("Turn already committed")
    except ValueError as exc:
        await session.rollback()
        raise DegradedModeError(str(exc)) from exc
    except Exception:
        await session.rollback()
        logger.exception("Failed persisting roll")
        raise

    # Update cached state with dice.
    new_state = {**state, "dice": dice, "dice_rolled_by": str(user_id)}
    await _cache_state(str(match_id), new_state)

    resp = {
        "turn_no": turn_no,
        "dice": dice,
        "rng_audit": payload["rng_audit"],
        "user_id": str(user_id),
        "current_color": player_color,
    }
    try:
        await redis_client.cache_pending_roll(match_id=str(match_id), payload=resp, ttl_seconds=300)
    except Exception:
        logger.exception("Failed caching pending roll (best-effort)")
    return resp


# PUBLIC_INTERFACE
async def submit_move(
    *,
    session: AsyncSession,
    match_id: uuid.UUID,
    user_id: uuid.UUID,
    turn_no: int,
    piece_index: Optional[int],
) -> dict[str, Any]:
    """
    Submit a move for a turn after a dice roll.

    Validation:
    - Requires pending roll in Redis for (match_id, user_id, turn_no) or state contains dice.
    - Must match current_color and turn_no.
    - Applies rules and persists canonical state snapshot in Turn payload.

    Idempotency:
    - If a Turn row exists with (match_id, turn_no) and it is a "turn" payload, returns it.
    """
    _require_redis()
    state = await get_current_state(session=session, match_id=match_id)
    player_color = _player_color_for_user(state, str(user_id))
    if not player_color:
        raise EngineValidationError("User is not a player in this match")

    if int(state.get("turn_no") or 1) != int(turn_no):
        raise EngineValidationError("turn_no does not match current state")

    if str(state.get("current_color")) != player_color:
        raise EngineValidationError("Not your turn")

    # Load dice from pending roll first.
    dice = None
    pending = None
    try:
        pending = await redis_client.get_pending_roll(match_id=str(match_id))
    except Exception:
        logger.exception("Failed reading pending roll")
    if pending and str(pending.get("user_id")) == str(user_id) and int(pending.get("turn_no")) == int(turn_no):
        dice = pending.get("dice")
        rng_audit = pending.get("rng_audit")
    else:
        dice = state.get("dice")
        rng_audit = None

    if not dice:
        raise EngineValidationError("No dice roll available for this turn")

    # Apply rules.
    new_state, turn_payload = apply_turn(state=state, color=player_color, dice=int(dice), piece_index=piece_index)

    # Persist Turn payload; note we already used turn_no for roll, so for the move we store at turn_no+1 to avoid clash.
    # To keep schema unchanged (unique match_id,turn_no), we treat each "turn_no" as a single committed action:
    # - roll is stored at turn_no (current)
    # - move is stored at turn_no+1 (next)
    commit_turn_no = int(turn_no) + 1

    payload = {
        **turn_payload,
        "turn_no": commit_turn_no,
        "rolled_turn_no": int(turn_no),
        "user_id": str(user_id),
        "rng_audit": rng_audit,
        "state_after": new_state,
    }

    # If already committed, return existing.
    existing = await session.execute(select(Turn).where(Turn.match_id == match_id, Turn.turn_no == commit_turn_no).limit(1))
    existing_turn = existing.scalar_one_or_none()
    if existing_turn:
        return existing_turn.payload

    try:
        await TurnRepository(session).append_turn(match_id=match_id, turn_no=commit_turn_no, user_id=user_id, payload=payload)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        # Return the now-existing one.
        existing = await session.execute(select(Turn).where(Turn.match_id == match_id, Turn.turn_no == commit_turn_no).limit(1))
        t = existing.scalar_one_or_none()
        return (t.payload if t else payload)
    except Exception:
        await session.rollback()
        logger.exception("Failed persisting move turn")
        raise

    # Update caches: new state; clear pending roll.
    await _cache_state(str(match_id), new_state)
    try:
        await redis_client.delete_pending_roll(match_id=str(match_id))
    except Exception:
        logger.exception("Failed clearing pending roll (best-effort)")

    # Publish internal event stub.
    try:
        await get_event_publisher().publish_turn_update(match_id=str(match_id), turn_no=commit_turn_no, payload=payload)
    except Exception:
        logger.exception("Turn publish failed (stub)")

    return payload


# PUBLIC_INTERFACE
def map_engine_error(exc: Exception) -> HTTPException:
    """
    Map domain errors to HTTPException with stable status codes/messages for clients.
    """
    if isinstance(exc, DegradedModeError):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, EngineValidationError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Gameplay engine error")
