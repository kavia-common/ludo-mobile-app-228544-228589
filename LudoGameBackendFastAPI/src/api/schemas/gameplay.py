from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MatchStateResponse(BaseModel):
    """Response containing the canonical match state (for reconnect and polling)."""

    match_id: str = Field(..., description="Match id (UUID).")
    state: Dict[str, Any] = Field(..., description="Canonical match state snapshot.")
    source: str = Field(..., description="Where the state was loaded from: redis|db.")


class DiceRollResponse(BaseModel):
    """Server-authoritative dice roll with audit record."""

    turn_no: int = Field(..., description="Turn number the roll applies to.")
    user_id: str = Field(..., description="User who rolled.")
    current_color: str = Field(..., description="Acting color for this turn.")
    dice: int = Field(..., ge=1, le=6, description="Dice value 1..6.")
    rng_audit: Dict[str, Any] = Field(..., description="Audit record containing nonce+commitment hash.")


class SubmitMoveRequest(BaseModel):
    """Request to submit a move after a roll."""

    match_id: str = Field(..., description="Match id (UUID).")
    turn_no: int = Field(..., description="Current turn number from state/dice roll.")
    piece_index: Optional[int] = Field(None, ge=0, le=3, description="Which piece to move; null indicates pass if no moves exist.")


class TurnHistoryItem(BaseModel):
    """A single persisted turn log entry."""

    turn_no: int = Field(..., description="Turn number (unique per match).")
    user_id: Optional[str] = Field(None, description="User who acted (null for genesis).")
    payload: Dict[str, Any] = Field(..., description="Canonical payload (roll/move/genesis).")
    committed_at: Optional[str] = Field(None, description="Server committed timestamp (ISO8601).")


class TurnHistoryResponse(BaseModel):
    """Paginated turn history response."""

    match_id: str = Field(..., description="Match id.")
    items: List[TurnHistoryItem] = Field(default_factory=list, description="Ordered turn list.")
    limit: int = Field(..., description="Page size.")
    offset: int = Field(..., description="Offset.")
