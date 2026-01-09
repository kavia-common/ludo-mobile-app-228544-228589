from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class GuestIssueRequest(BaseModel):
    """Request body for issuing a guest identity."""

    device_id: Optional[str] = Field(default=None, description="Client device identifier (optional).")
    display_name: Optional[str] = Field(default=None, description="Optional display name to set for the new guest.")


class TokenResponse(BaseModel):
    """JWT response."""

    access_token: str = Field(..., description="Bearer JWT access token.")
    token_type: str = Field(default="bearer", description="Token type (always 'bearer').")
    user_id: str = Field(..., description="User id associated with the token.")
    is_guest: bool = Field(..., description="Whether the user is a guest identity.")


class LinkIdentityRequest(BaseModel):
    """Link/upgrade a guest identity to an email identity (simplified)."""

    email: str = Field(..., description="Email to link to the current guest user.")
    username: Optional[str] = Field(default=None, description="Optional username override during linking.")
    display_name: Optional[str] = Field(default=None, description="Optional display name override during linking.")
