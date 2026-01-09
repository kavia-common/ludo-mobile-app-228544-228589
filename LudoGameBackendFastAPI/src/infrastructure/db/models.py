from __future__ import annotations

import datetime as dt
import enum
import uuid
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.db.base import Base, TimestampMixin


class UserStatus(str, enum.Enum):
    """Status for user accounts."""

    active = "active"
    disabled = "disabled"
    deleted = "deleted"


class MatchStatus(str, enum.Enum):
    """Lifecycle status of a match."""

    pending = "pending"
    active = "active"
    finished = "finished"
    cancelled = "cancelled"


class ReceiptStatus(str, enum.Enum):
    """Status of an IAP receipt verification.

    Notes:
    - DB enum was initially created with: received, verified, rejected, refunded.
    - Step 6 introduces API-level statuses: pending, failed.
    - To avoid a migration in this step, we map:
        pending -> received
        failed  -> rejected
      when persisting.
    """

    # Existing persisted values
    received = "received"   # used as "pending" at API-level
    verified = "verified"
    rejected = "rejected"   # used as "failed" at API-level
    refunded = "refunded"

    # API-level aliases (not persisted directly without mapping)
    pending = "pending"
    failed = "failed"


class FeatureFlagScope(str, enum.Enum):
    """Scope of feature flags."""

    global_scope = "global"
    user = "user"
    platform = "platform"


class User(Base, TimestampMixin):
    """Core player identity."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True, unique=True, index=True)
    username: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus, name="user_status"), nullable=False, default=UserStatus.active)
    is_guest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    profile: Mapped["Profile"] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")
    inventory_items: Mapped[list["InventoryItem"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    receipts: Mapped[list["Receipt"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Profile(Base, TimestampMixin):
    """Public player profile."""

    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    country_code: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    mmr: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)

    user: Mapped["User"] = relationship(back_populates="profile")

    __table_args__ = (
        Index("ix_profiles_user_id", "user_id"),
    )


class Friend(Base, TimestampMixin):
    """Friend relationship (directed); mutual friendship is represented by two rows."""

    __tablename__ = "friends"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    friend_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("user_id", "friend_user_id", name="uq_friends_user_friend"),
        CheckConstraint("user_id <> friend_user_id", name="ck_friends_not_self"),
        Index("ix_friends_user_id", "user_id"),
        Index("ix_friends_friend_user_id", "friend_user_id"),
    )


class Match(Base, TimestampMixin):
    """A match lobby and canonical metadata for a game session."""

    __tablename__ = "matches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[MatchStatus] = mapped_column(Enum(MatchStatus, name="match_status"), nullable=False, default=MatchStatus.pending)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="classic")  # e.g. classic, quick, ranked
    is_ranked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_players: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    rng_seed: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    match_players: Mapped[list["MatchPlayer"]] = relationship(back_populates="match", cascade="all, delete-orphan")
    turns: Mapped[list["Turn"]] = relationship(back_populates="match", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_matches_status_created_at", "status", "created_at"),
    )


class MatchPlayer(Base, TimestampMixin):
    """Membership of a user in a match, including seat/color."""

    __tablename__ = "match_players"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    match_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("matches.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    seat_no: Mapped[int] = mapped_column(Integer, nullable=False)  # 0..3
    color: Mapped[str] = mapped_column(String(16), nullable=False)  # red/blue/green/yellow
    is_winner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_left: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    match: Mapped["Match"] = relationship(back_populates="match_players")

    __table_args__ = (
        UniqueConstraint("match_id", "user_id", name="uq_match_players_match_user"),
        UniqueConstraint("match_id", "seat_no", name="uq_match_players_match_seat"),
        Index("ix_match_players_match_id", "match_id"),
        Index("ix_match_players_user_id", "user_id"),
    )


class Turn(Base, TimestampMixin):
    """Immutable turn log for auditing and reconnect support."""

    __tablename__ = "turns"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    match_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("matches.id", ondelete="CASCADE"), nullable=False)
    turn_no: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # payload captures dice roll, moves, captures, etc. (canonicalized in future steps)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # server timestamp when the turn was committed (separate from created_at for clarity)
    committed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: dt.datetime.now(dt.timezone.utc),
    )

    match: Mapped["Match"] = relationship(back_populates="turns")

    __table_args__ = (
        UniqueConstraint("match_id", "turn_no", name="uq_turns_match_turn_no"),
        Index("ix_turns_match_id_turn_no", "match_id", "turn_no"),
    )


class InventoryItem(Base, TimestampMixin):
    """Cosmetic inventory item owned by a user."""

    __tablename__ = "inventory_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    item_type: Mapped[str] = mapped_column(String(32), nullable=False, default="cosmetic")  # theme, dice, board, etc.
    is_equipped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    user: Mapped["User"] = relationship(back_populates="inventory_items")

    __table_args__ = (
        UniqueConstraint("user_id", "sku", name="uq_inventory_user_sku"),
        Index("ix_inventory_items_user_id", "user_id"),
        Index("ix_inventory_items_sku", "sku"),
    )


class Receipt(Base, TimestampMixin):
    """In-app purchase receipt record (platform-agnostic)."""

    __tablename__ = "receipts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    platform: Mapped[str] = mapped_column(String(16), nullable=False)  # ios/android
    product_sku: Mapped[str] = mapped_column(String(64), nullable=False)
    transaction_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[ReceiptStatus] = mapped_column(
        Enum(ReceiptStatus, name="receipt_status"),
        nullable=False,
        default=ReceiptStatus.received,
    )
    raw_receipt: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    verified_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="receipts")

    __table_args__ = (
        UniqueConstraint("platform", "transaction_id", name="uq_receipts_platform_transaction"),
        Index("ix_receipts_user_id", "user_id"),
        Index("ix_receipts_product_sku", "product_sku"),
        Index("ix_receipts_status", "status"),
    )


class FeatureFlag(Base, TimestampMixin):
    """Feature flag entries. Can be global, per-user, or per-platform."""

    __tablename__ = "feature_flags"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    scope: Mapped[FeatureFlagScope] = mapped_column(
        Enum(FeatureFlagScope, name="feature_flag_scope"),
        nullable=False,
        default=FeatureFlagScope.global_scope,
    )
    scope_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)  # e.g. user_id or platform name
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("key", "scope", "scope_id", name="uq_feature_flags_key_scope"),
        Index("ix_feature_flags_key", "key"),
    )
