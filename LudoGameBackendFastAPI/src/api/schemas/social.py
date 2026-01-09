from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class PublicProfile(BaseModel):
    """Public-facing profile information."""

    user_id: str = Field(..., description="User id.")
    username: str = Field(..., description="Unique username.")
    display_name: Optional[str] = Field(default=None, description="Display name.")
    avatar_url: Optional[str] = Field(default=None, description="Avatar URL.")
    bio: Optional[str] = Field(default=None, description="Bio/about text.")
    country_code: Optional[str] = Field(default=None, description="ISO country code (2 letters).")
    mmr: int = Field(..., description="Matchmaking rating (MMR).")
    is_guest: bool = Field(..., description="Whether this account is a guest identity.")


class ProfileUpdateRequest(BaseModel):
    """Update current user's profile."""

    display_name: Optional[str] = Field(default=None, description="Updated display name.")
    avatar_url: Optional[str] = Field(default=None, description="Updated avatar URL.")
    bio: Optional[str] = Field(default=None, description="Updated bio.")
    country_code: Optional[str] = Field(default=None, description="Updated ISO country code (2 letters).")


class FriendActionRequest(BaseModel):
    """Friend actions target a user id."""

    target_user_id: str = Field(..., description="Target user id.")


class FriendRelationship(BaseModel):
    """Directed relationship row."""

    user_id: str = Field(..., description="Owner user id.")
    friend_user_id: str = Field(..., description="Related user id.")
    is_blocked: bool = Field(..., description="Whether the owner blocked the related user.")


class FriendListResponse(BaseModel):
    """Paginated friends list."""

    items: list[FriendRelationship] = Field(..., description="Relationship items.")
    limit: int = Field(..., description="Limit used.")
    offset: int = Field(..., description="Offset used.")


class SafeChatSettings(BaseModel):
    """Safe chat controls for the current user (MVP stored in Redis)."""

    safe_chat_enabled: bool = Field(..., description="If true, only allow safe-chat phrases/emotes.")
    allow_friend_requests: bool = Field(..., description="If true, user can receive friend requests.")
