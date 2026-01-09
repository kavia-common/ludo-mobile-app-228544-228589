from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class LobbyCreateRequest(BaseModel):
    """Request to create a private lobby."""

    max_players: int = Field(default=4, ge=2, le=4, description="Max players in the lobby (2..4).")
    mode: str = Field(default="classic", min_length=1, max_length=32, description="Game mode (e.g., classic).")
    is_ranked: bool = Field(default=False, description="Whether the lobby is ranked (MMR).")
    is_private: bool = Field(default=True, description="Whether the lobby is private (invite-only).")


class LobbyCreateResponse(BaseModel):
    """Response from lobby creation."""

    lobby_id: str = Field(..., description="Lobby id.")
    code: str = Field(..., description="Short join code (shareable).")
    owner_user_id: str = Field(..., description="Lobby owner user id.")
    max_players: int = Field(..., description="Max players.")
    mode: str = Field(..., description="Game mode.")
    is_ranked: bool = Field(..., description="Is ranked.")


class LobbyJoinRequest(BaseModel):
    """Join a lobby either via lobby_id or join code."""

    lobby_id: Optional[str] = Field(default=None, description="Lobby id to join.")
    code: Optional[str] = Field(default=None, description="Join code to join.")


class LobbyLeaveResponse(BaseModel):
    """Response from leaving a lobby."""

    lobby_id: str = Field(..., description="Lobby id.")
    left: bool = Field(..., description="True if user left/was not present (idempotent).")


class LobbyStateResponse(BaseModel):
    """Current lobby state snapshot (Redis-backed)."""

    lobby_id: str = Field(..., description="Lobby id.")
    code: str = Field(..., description="Join code.")
    owner_user_id: str = Field(..., description="Owner user id.")
    members: list[str] = Field(..., description="Ordered list of member user ids.")
    max_players: int = Field(..., description="Max players.")
    mode: str = Field(..., description="Mode.")
    is_ranked: bool = Field(..., description="Is ranked.")
    is_private: bool = Field(..., description="Is private.")
    status: Literal["open", "in_match", "closed"] = Field(..., description="Lobby status.")


class LobbyInviteRequest(BaseModel):
    """Invite a user to a lobby (best-effort stored in Redis)."""

    lobby_id: str = Field(..., description="Lobby id.")
    target_user_id: str = Field(..., description="User id to invite.")


class LobbyInviteResponse(BaseModel):
    """Response for lobby invite."""

    lobby_id: str = Field(..., description="Lobby id.")
    invited_user_id: str = Field(..., description="Invited user id.")


class LobbyStartMatchRequest(BaseModel):
    """Start a match from a lobby (owner-only)."""

    lobby_id: str = Field(..., description="Lobby id to start from.")
    mode: Optional[str] = Field(default=None, description="Optional override mode; defaults to lobby mode.")


class MatchStartResponse(BaseModel):
    """Response with created match id."""

    match_id: str = Field(..., description="Match id.")
    lobby_id: Optional[str] = Field(default=None, description="Lobby id if started from a lobby.")


class QuickPlayEnqueueRequest(BaseModel):
    """Enqueue for quick-play matchmaking."""

    queue: Literal["ranked", "unranked"] = Field(..., description="Queue type.")
    mode: str = Field(default="classic", min_length=1, max_length=32, description="Mode for matchmaking.")
    max_players: int = Field(default=4, ge=2, le=4, description="Desired max players (2..4).")


class QuickPlayEnqueueResponse(BaseModel):
    """Enqueue response."""

    queue: str = Field(..., description="Queue type.")
    ticket_id: str = Field(..., description="Ticket id (per-user, per-queue).")


class QuickPlayDequeueRequest(BaseModel):
    """Dequeue from quick-play."""

    queue: Literal["ranked", "unranked"] = Field(..., description="Queue type.")


class QuickPlayDequeueResponse(BaseModel):
    """Dequeue response."""

    queue: str = Field(..., description="Queue type.")
    removed: bool = Field(..., description="True if removed; false if not present (idempotent).")


class PresenceHeartbeatRequest(BaseModel):
    """Presence heartbeat for a match (or lobby)."""

    scope: Literal["match", "lobby"] = Field(..., description="Presence scope.")
    scope_id: str = Field(..., description="Match id or Lobby id.")
    status: Literal["online", "in_game", "away"] = Field(default="online", description="Client-reported presence status.")


class PresenceHeartbeatResponse(BaseModel):
    """Heartbeat response."""

    ok: bool = Field(..., description="True if accepted.")
    expires_in_seconds: int = Field(..., description="Seconds until presence expires without further heartbeats.")


class RestoreStateRequest(BaseModel):
    """Request to restore active match state after reconnect."""

    match_id: str = Field(..., description="Match id to restore.")


class RestoreStateResponse(BaseModel):
    """Reconnection restore response (best-effort)."""

    match_id: str = Field(..., description="Match id.")
    status: str = Field(..., description="Match status.")
    players: list[dict] = Field(..., description="Players in match (seat/color/has_left).")
    cached_state: Optional[dict] = Field(default=None, description="Cached ephemeral state from Redis, if present.")
    turns: list[dict] = Field(..., description="Recent turn log entries (from DB when available).")
    can_reconnect: bool = Field(..., description="Whether the server believes reconnect is possible.")
