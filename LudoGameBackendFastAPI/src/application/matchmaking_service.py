from __future__ import annotations

import json
import secrets
import string
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal, Optional

from src.core.config import get_settings
from src.core.logging import get_logger
from src.infrastructure.cache import redis_client

logger = get_logger(__name__)


@dataclass(frozen=True)
class MatchmakingKeys:
    """Redis key layout for matchmaking + lobbies + presence."""

    # Lobbies
    lobby_hash_prefix: str = "lobby:meta:"  # hash
    lobby_members_prefix: str = "lobby:members:"  # list
    lobby_invites_prefix: str = "lobby:invites:"  # set per lobby
    lobby_code_to_id_prefix: str = "lobby:code:"  # string -> lobby_id

    # Match -> lobby mapping (optional)
    match_to_lobby_prefix: str = "match:lobby:"  # string

    # Presence
    presence_prefix: str = "presence:"  # presence:{scope}:{scope_id}:{user_id} -> string payload

    # Queues
    queue_prefix: str = "mm:queue:"  # list entries
    ticket_prefix: str = "mm:ticket:"  # ticket:{queue}:{user_id} -> string payload


_KEYS = MatchmakingKeys()

# Defaults chosen to be light but useful for mobile reconnect semantics.
_PRESENCE_TTL_SECONDS = 45
_LOBBY_TTL_SECONDS = 6 * 3600
_INVITE_TTL_SECONDS = 24 * 3600
_TICKET_TTL_SECONDS = 10 * 60


def _now_ts() -> int:
    return int(time.time())


def _redis_enabled() -> bool:
    return bool(get_settings().redis_url)


def _client():
    # Will throw if not initialized; callers handle.
    return redis_client._get_client()  # type: ignore[attr-defined]


def _random_code(length: int = 6) -> str:
    alphabet = string.ascii_uppercase + string.digits
    # Avoid ambiguous chars
    alphabet = alphabet.replace("0", "").replace("O", "").replace("1", "").replace("I", "")
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _presence_key(scope: str, scope_id: str, user_id: str) -> str:
    return f"{_KEYS.presence_prefix}{scope}:{scope_id}:{user_id}"


def _lobby_meta_key(lobby_id: str) -> str:
    return f"{_KEYS.lobby_hash_prefix}{lobby_id}"


def _lobby_members_key(lobby_id: str) -> str:
    return f"{_KEYS.lobby_members_prefix}{lobby_id}"


def _lobby_invites_key(lobby_id: str) -> str:
    return f"{_KEYS.lobby_invites_prefix}{lobby_id}"


def _lobby_code_key(code: str) -> str:
    return f"{_KEYS.lobby_code_to_id_prefix}{code}"


def _queue_key(queue: str) -> str:
    return f"{_KEYS.queue_prefix}{queue}"


def _ticket_key(queue: str, user_id: str) -> str:
    return f"{_KEYS.ticket_prefix}{queue}:{user_id}"


# PUBLIC_INTERFACE
async def presence_heartbeat(*, user_id: str, scope: Literal["match", "lobby"], scope_id: str, status: str) -> int:
    """
    Record a presence heartbeat with TTL.

    Returns:
        int: TTL in seconds that was applied (server's expiration window).

    Notes:
        - Best-effort; if Redis isn't configured, returns default TTL without storing.
    """
    if not _redis_enabled():
        return _PRESENCE_TTL_SECONDS

    payload = {"user_id": user_id, "scope": scope, "scope_id": scope_id, "status": status, "ts": _now_ts()}
    key = _presence_key(scope, scope_id, user_id)
    try:
        client = _client()
        await client.set(key, json.dumps(payload), ex=_PRESENCE_TTL_SECONDS)
        return _PRESENCE_TTL_SECONDS
    except Exception:
        logger.exception("Presence heartbeat failed (continuing best-effort)")
        return _PRESENCE_TTL_SECONDS


# PUBLIC_INTERFACE
async def presence_list(*, scope: Literal["match", "lobby"], scope_id: str, user_ids: list[str]) -> dict[str, bool]:
    """
    Check which users are currently present in a given scope.

    Returns:
        dict[user_id, bool]: True if present (key exists).
    """
    if not _redis_enabled():
        return {uid: False for uid in user_ids}

    keys = [_presence_key(scope, scope_id, uid) for uid in user_ids]
    try:
        client = _client()
        vals = await client.mget(keys)
        return {uid: bool(val) for uid, val in zip(user_ids, vals)}
    except Exception:
        logger.exception("Presence list failed")
        return {uid: False for uid in user_ids}


# PUBLIC_INTERFACE
async def lobby_create(
    *,
    owner_user_id: str,
    max_players: int,
    mode: str,
    is_ranked: bool,
    is_private: bool,
) -> dict[str, Any]:
    """
    Create a lobby in Redis.

    Returns:
        dict: Lobby metadata including lobby_id and join code.

    Raises:
        RuntimeError: If Redis not configured/available.
    """
    if not _redis_enabled():
        raise RuntimeError("Redis not configured")

    lobby_id = str(uuid.uuid4())

    # Try a few times to avoid code collisions.
    code = None
    for _ in range(6):
        candidate = _random_code(6)
        try:
            client = _client()
            ok = await client.set(_lobby_code_key(candidate), lobby_id, ex=_LOBBY_TTL_SECONDS, nx=True)
            if ok:
                code = candidate
                break
        except Exception as exc:
            raise RuntimeError("Redis unavailable") from exc

    if not code:
        raise RuntimeError("Failed to allocate lobby code")

    meta = {
        "lobby_id": lobby_id,
        "code": code,
        "owner_user_id": owner_user_id,
        "max_players": str(int(max_players)),
        "mode": mode,
        "is_ranked": "1" if is_ranked else "0",
        "is_private": "1" if is_private else "0",
        "status": "open",
        "created_at": str(_now_ts()),
    }

    try:
        client = _client()
        await client.hset(_lobby_meta_key(lobby_id), mapping=meta)
        await client.expire(_lobby_meta_key(lobby_id), _LOBBY_TTL_SECONDS)
        # Members list: owner first
        await client.rpush(_lobby_members_key(lobby_id), owner_user_id)
        await client.expire(_lobby_members_key(lobby_id), _LOBBY_TTL_SECONDS)
        # Invites set TTL
        await client.expire(_lobby_invites_key(lobby_id), _INVITE_TTL_SECONDS)
        return {"lobby_id": lobby_id, "code": code, **meta}
    except Exception as exc:
        logger.exception("Lobby create failed")
        raise RuntimeError("Redis unavailable") from exc


async def _lobby_get_id_by_code(code: str) -> Optional[str]:
    if not _redis_enabled():
        return None
    try:
        client = _client()
        return await client.get(_lobby_code_key(code))
    except Exception:
        logger.exception("Lobby code lookup failed")
        return None


async def _lobby_read_meta(lobby_id: str) -> Optional[dict[str, str]]:
    if not _redis_enabled():
        return None
    try:
        client = _client()
        meta = await client.hgetall(_lobby_meta_key(lobby_id))
        return meta or None
    except Exception:
        logger.exception("Lobby meta read failed")
        return None


# PUBLIC_INTERFACE
async def lobby_get_state(*, lobby_id: Optional[str] = None, code: Optional[str] = None) -> Optional[dict[str, Any]]:
    """
    Get lobby state (meta + members).

    Returns:
        dict | None: Lobby state if found.
    """
    if not _redis_enabled():
        return None

    resolved_id = lobby_id
    if not resolved_id and code:
        resolved_id = await _lobby_get_id_by_code(code)
    if not resolved_id:
        return None

    meta = await _lobby_read_meta(resolved_id)
    if not meta:
        return None

    try:
        client = _client()
        members = await client.lrange(_lobby_members_key(resolved_id), 0, -1)
    except Exception:
        logger.exception("Lobby members read failed")
        members = []

    return {
        "lobby_id": resolved_id,
        "code": meta.get("code", ""),
        "owner_user_id": meta.get("owner_user_id", ""),
        "members": members,
        "max_players": int(meta.get("max_players", "4") or 4),
        "mode": meta.get("mode", "classic"),
        "is_ranked": (meta.get("is_ranked", "0") == "1"),
        "is_private": (meta.get("is_private", "1") == "1"),
        "status": meta.get("status", "open"),
    }


# PUBLIC_INTERFACE
async def lobby_invite(*, lobby_id: str, inviter_user_id: str, target_user_id: str) -> None:
    """
    Store a lobby invite in Redis (best-effort).

    Notes:
        - Currently does not validate friendship; higher layers can enforce later.
        - Idempotent by using a Redis SET membership.
    """
    if not _redis_enabled():
        raise RuntimeError("Redis not configured")
    # Ensure lobby exists
    state = await lobby_get_state(lobby_id=lobby_id)
    if not state:
        raise RuntimeError("Lobby not found")

    try:
        client = _client()
        await client.sadd(_lobby_invites_key(lobby_id), target_user_id)
        await client.expire(_lobby_invites_key(lobby_id), _INVITE_TTL_SECONDS)
        # Also set a small user-centric key for quick lookup (optional)
        await client.set(f"user:invite:{target_user_id}:{lobby_id}", inviter_user_id, ex=_INVITE_TTL_SECONDS)
    except Exception as exc:
        logger.exception("Lobby invite failed")
        raise RuntimeError("Redis unavailable") from exc


async def _lobby_can_join(*, lobby_state: dict[str, Any], user_id: str) -> bool:
    if lobby_state.get("status") != "open":
        return False
    members: list[str] = list(lobby_state.get("members") or [])
    if user_id in members:
        return True  # idempotent join
    if len(members) >= int(lobby_state.get("max_players", 4)):
        return False
    if not bool(lobby_state.get("is_private", True)):
        return True
    # If private, must be owner or invited
    if user_id == lobby_state.get("owner_user_id"):
        return True
    try:
        client = _client()
        invited = await client.sismember(_lobby_invites_key(lobby_state["lobby_id"]), user_id)
        return bool(invited)
    except Exception:
        logger.exception("Invite membership check failed")
        return False


# PUBLIC_INTERFACE
async def lobby_join(*, lobby_id: Optional[str], code: Optional[str], user_id: str) -> dict[str, Any]:
    """
    Join a lobby (idempotent).

    Returns:
        dict: Updated lobby state.

    Raises:
        RuntimeError: On missing Redis or lobby not found / join denied.
    """
    if not _redis_enabled():
        raise RuntimeError("Redis not configured")

    state = await lobby_get_state(lobby_id=lobby_id, code=code)
    if not state:
        raise RuntimeError("Lobby not found")

    if not await _lobby_can_join(lobby_state=state, user_id=user_id):
        raise RuntimeError("Join not allowed")

    try:
        client = _client()
        members_key = _lobby_members_key(state["lobby_id"])
        members = await client.lrange(members_key, 0, -1)
        if user_id not in members:
            await client.rpush(members_key, user_id)
        # Refresh TTLs on activity
        await client.expire(members_key, _LOBBY_TTL_SECONDS)
        await client.expire(_lobby_meta_key(state["lobby_id"]), _LOBBY_TTL_SECONDS)
        return (await lobby_get_state(lobby_id=state["lobby_id"])) or state
    except Exception as exc:
        logger.exception("Lobby join failed")
        raise RuntimeError("Redis unavailable") from exc


# PUBLIC_INTERFACE
async def lobby_leave(*, lobby_id: str, user_id: str) -> bool:
    """
    Leave a lobby (idempotent).

    Returns:
        bool: True if removed, False if not present.
    """
    if not _redis_enabled():
        raise RuntimeError("Redis not configured")

    state = await lobby_get_state(lobby_id=lobby_id)
    if not state:
        return False

    try:
        client = _client()
        removed = await client.lrem(_lobby_members_key(lobby_id), 0, user_id)
        # If owner left, close lobby
        if user_id == state.get("owner_user_id"):
            await client.hset(_lobby_meta_key(lobby_id), mapping={"status": "closed"})
        # Refresh TTL on close/leave
        await client.expire(_lobby_meta_key(lobby_id), _LOBBY_TTL_SECONDS)
        await client.expire(_lobby_members_key(lobby_id), _LOBBY_TTL_SECONDS)
        return int(removed or 0) > 0
    except Exception as exc:
        logger.exception("Lobby leave failed")
        raise RuntimeError("Redis unavailable") from exc


# PUBLIC_INTERFACE
async def lobby_mark_in_match(*, lobby_id: str, match_id: str) -> None:
    """
    Mark lobby as in-match and store match->lobby mapping.

    Best-effort; if Redis is unavailable, raises.
    """
    if not _redis_enabled():
        raise RuntimeError("Redis not configured")
    try:
        client = _client()
        await client.hset(_lobby_meta_key(lobby_id), mapping={"status": "in_match"})
        await client.set(f"{_KEYS.match_to_lobby_prefix}{match_id}", lobby_id, ex=_LOBBY_TTL_SECONDS)
        await client.expire(_lobby_meta_key(lobby_id), _LOBBY_TTL_SECONDS)
        await client.expire(_lobby_members_key(lobby_id), _LOBBY_TTL_SECONDS)
    except Exception as exc:
        logger.exception("Failed marking lobby in match")
        raise RuntimeError("Redis unavailable") from exc


# PUBLIC_INTERFACE
async def quickplay_enqueue(
    *,
    user_id: str,
    queue: Literal["ranked", "unranked"],
    mode: str,
    max_players: int,
) -> str:
    """
    Enqueue user into matchmaking queue (idempotent per-user).

    Returns:
        str: ticket_id (stable for this user+queue until it expires or dequeued)
    """
    if not _redis_enabled():
        raise RuntimeError("Redis not configured")

    ticket_key = _ticket_key(queue, user_id)
    ticket_id = f"{queue}:{user_id}"
    payload = {"ticket_id": ticket_id, "user_id": user_id, "queue": queue, "mode": mode, "max_players": max_players, "ts": _now_ts()}

    try:
        client = _client()
        existing = await client.get(ticket_key)
        if existing:
            # Idempotent return
            return ticket_id

        # Write ticket first (NX) to avoid duplicate enqueues
        ok = await client.set(ticket_key, json.dumps(payload), ex=_TICKET_TTL_SECONDS, nx=True)
        if not ok:
            return ticket_id

        await client.rpush(_queue_key(queue), json.dumps(payload))
        return ticket_id
    except Exception as exc:
        logger.exception("Enqueue failed")
        raise RuntimeError("Redis unavailable") from exc


# PUBLIC_INTERFACE
async def quickplay_dequeue(*, user_id: str, queue: Literal["ranked", "unranked"]) -> bool:
    """
    Dequeue user from matchmaking (best-effort O(n) list removal).

    Returns:
        bool: True if removed, False if not present (idempotent).
    """
    if not _redis_enabled():
        raise RuntimeError("Redis not configured")

    ticket_key = _ticket_key(queue, user_id)
    try:
        client = _client()
        raw = await client.get(ticket_key)
        if not raw:
            return False
        await client.delete(ticket_key)
        # Remove one matching payload occurrence from list.
        removed = await client.lrem(_queue_key(queue), 0, raw)
        return int(removed or 0) > 0
    except Exception as exc:
        logger.exception("Dequeue failed")
        raise RuntimeError("Redis unavailable") from exc


# PUBLIC_INTERFACE
async def quickplay_try_match(
    *,
    queue: Literal["ranked", "unranked"],
    desired_players: int = 4,
) -> Optional[list[dict[str, Any]]]:
    """
    Attempt to pop a full group from a queue.

    Returns:
        list[dict] | None: A list of ticket payload dicts if enough players were matched.

    Notes:
        - This uses a simple Lua script to atomically pop N items.
        - If script fails, falls back to None to avoid partial matches.
        - This is intentionally simple (no MMR bucketing yet).
    """
    if not _redis_enabled():
        return None

    script = """
local key = KEYS[1]
local n = tonumber(ARGV[1])
local len = redis.call('LLEN', key)
if len < n then
  return {}
end
local res = {}
for i=1,n do
  local v = redis.call('LPOP', key)
  if not v then
    return {}
  end
  table.insert(res, v)
end
return res
"""
    try:
        client = _client()
        raw_items = await client.eval(script, numkeys=1, keys=[_queue_key(queue)], args=[str(int(desired_players))])
        if not raw_items:
            return None
        items: list[dict[str, Any]] = []
        for raw in raw_items:
            try:
                items.append(json.loads(raw))
            except Exception:
                continue
        if len(items) != int(desired_players):
            return None
        # Clean up ticket keys for these matched players (best-effort)
        for it in items:
            uid = str(it.get("user_id") or "")
            if uid:
                await client.delete(_ticket_key(queue, uid))
        return items
    except Exception:
        logger.exception("Try match failed")
        return None
