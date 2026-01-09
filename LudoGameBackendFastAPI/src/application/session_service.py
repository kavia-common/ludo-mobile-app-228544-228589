from __future__ import annotations

import secrets
import uuid
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.infrastructure.db import models as db_models
from src.infrastructure.db.repositories import MatchPlayerRepository, MatchRepository, TurnRepository

logger = get_logger(__name__)

_COLORS = ["red", "blue", "green", "yellow"]


def _generate_rng_seed() -> str:
    # Short seed; canonical fairness logging can expand later.
    return secrets.token_hex(16)


def _seat_color_for_index(i: int) -> tuple[int, str]:
    seat = int(i)
    color = _COLORS[i % len(_COLORS)]
    return seat, color


# PUBLIC_INTERFACE
async def create_match_with_players(
    *,
    session: AsyncSession,
    user_ids: list[uuid.UUID],
    mode: str,
    is_ranked: bool,
    max_players: int,
) -> db_models.Match:
    """
    Create a Match + MatchPlayers in PostgreSQL.

    Returns:
        Match: Persisted match row (pending status).

    Raises:
        Exception: If DB operation fails; caller should handle rollback.
    """
    match_repo = MatchRepository(session)
    mp_repo = MatchPlayerRepository(session)
    rng_seed = _generate_rng_seed()

    match = await match_repo.create(mode=mode, is_ranked=is_ranked, max_players=max_players, rng_seed=rng_seed)

    for idx, uid in enumerate(user_ids):
        seat_no, color = _seat_color_for_index(idx)
        await mp_repo.add_player(match_id=match.id, user_id=uid, seat_no=seat_no, color=color)

    return match


# PUBLIC_INTERFACE
async def restore_match_state(
    *,
    session: Optional[AsyncSession],
    match_id: uuid.UUID,
    cached_state: Optional[dict[str, Any]],
    turns_limit: int = 200,
) -> dict[str, Any]:
    """
    Restore match state for reconnects (DB + Redis cached state).

    Args:
        session: Optional DB session; if None, returns minimal restore payload.
        match_id: Match id.
        cached_state: Redis cached match_state (if any).
        turns_limit: Max turns to return.

    Returns:
        dict: Payload with match info, players, turns, cached_state.

    Notes:
        - Designed to degrade when DB is unavailable.
    """
    if session is None:
        return {
            "match_id": str(match_id),
            "status": "unknown",
            "players": [],
            "turns": [],
            "cached_state": cached_state,
            "can_reconnect": bool(cached_state),
        }

    match = await MatchRepository(session).get(match_id)
    if not match:
        return {
            "match_id": str(match_id),
            "status": "not_found",
            "players": [],
            "turns": [],
            "cached_state": cached_state,
            "can_reconnect": False,
        }

    players = await MatchPlayerRepository(session).list_by_match(match_id)
    turns_page = await TurnRepository(session).list_turns(match_id=match_id, limit=turns_limit, offset=0)

    return {
        "match_id": str(match.id),
        "status": str(match.status.value if hasattr(match.status, "value") else match.status),
        "players": [
            {
                "user_id": str(p.user_id),
                "seat_no": int(p.seat_no),
                "color": str(p.color),
                "has_left": bool(p.has_left),
            }
            for p in players
        ],
        "turns": [
            {
                "turn_no": int(t.turn_no),
                "user_id": (str(t.user_id) if t.user_id else None),
                "payload": t.payload,
                "committed_at": (t.committed_at.isoformat() if getattr(t, "committed_at", None) else None),
            }
            for t in turns_page.items
        ],
        "cached_state": cached_state,
        "can_reconnect": bool(cached_state) or str(match.status) in ("active", "pending"),
    }
