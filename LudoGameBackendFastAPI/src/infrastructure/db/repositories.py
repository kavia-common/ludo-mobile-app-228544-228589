from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional, Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.db.models import (
    FeatureFlag,
    FeatureFlagScope,
    Friend,
    InventoryItem,
    Match,
    MatchPlayer,
    MatchStatus,
    Profile,
    Receipt,
    ReceiptStatus,
    Turn,
    User,
    UserStatus,
)


@dataclass(frozen=True)
class Page:
    """Simple pagination container."""

    items: Sequence[object]
    limit: int
    offset: int


class UserRepository:
    """Repository for Users."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: uuid.UUID) -> Optional[User]:
        return await self._session.get(User, user_id)

    async def get_by_username(self, username: str) -> Optional[User]:
        stmt = select(User).where(User.username == username)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def create(self, *, username: str, email: Optional[str] = None, is_guest: bool = False) -> User:
        user = User(username=username, email=email, is_guest=is_guest, status=UserStatus.active)
        self._session.add(user)
        await self._session.flush()
        return user

    async def set_status(self, user_id: uuid.UUID, status: UserStatus) -> None:
        stmt = update(User).where(User.id == user_id).values(status=status)
        await self._session.execute(stmt)


class ProfileRepository:
    """Repository for Profiles."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_user_id(self, user_id: uuid.UUID) -> Optional[Profile]:
        stmt = select(Profile).where(Profile.user_id == user_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def create_for_user(
        self,
        *,
        user_id: uuid.UUID,
        avatar_url: Optional[str] = None,
        bio: Optional[str] = None,
        country_code: Optional[str] = None,
    ) -> Profile:
        profile = Profile(user_id=user_id, avatar_url=avatar_url, bio=bio, country_code=country_code)
        self._session.add(profile)
        await self._session.flush()
        return profile

    async def update_mmr(self, user_id: uuid.UUID, mmr: int) -> None:
        stmt = update(Profile).where(Profile.user_id == user_id).values(mmr=mmr)
        await self._session.execute(stmt)


class FriendsRepository:
    """Repository for Friend relationships."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_friend(self, *, user_id: uuid.UUID, friend_user_id: uuid.UUID) -> Friend:
        rel = Friend(user_id=user_id, friend_user_id=friend_user_id, is_blocked=False)
        self._session.add(rel)
        await self._session.flush()
        return rel

    async def remove_friend(self, *, user_id: uuid.UUID, friend_user_id: uuid.UUID) -> None:
        stmt = delete(Friend).where(Friend.user_id == user_id, Friend.friend_user_id == friend_user_id)
        await self._session.execute(stmt)

    async def list_friends(self, *, user_id: uuid.UUID, limit: int = 50, offset: int = 0) -> Page:
        stmt = select(Friend).where(Friend.user_id == user_id).order_by(Friend.created_at.desc()).limit(limit).offset(offset)
        items = (await self._session.execute(stmt)).scalars().all()
        return Page(items=items, limit=limit, offset=offset)


class MatchRepository:
    """Repository for Match aggregates (metadata only)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, match_id: uuid.UUID) -> Optional[Match]:
        return await self._session.get(Match, match_id)

    async def create(self, *, mode: str = "classic", is_ranked: bool = False, max_players: int = 4, rng_seed: Optional[str] = None) -> Match:
        match = Match(mode=mode, is_ranked=is_ranked, max_players=max_players, rng_seed=rng_seed, status=MatchStatus.pending)
        self._session.add(match)
        await self._session.flush()
        return match

    async def set_status(self, match_id: uuid.UUID, status: MatchStatus) -> None:
        stmt = update(Match).where(Match.id == match_id).values(status=status)
        await self._session.execute(stmt)


class MatchPlayerRepository:
    """Repository for MatchPlayer membership."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_player(self, *, match_id: uuid.UUID, user_id: uuid.UUID, seat_no: int, color: str) -> MatchPlayer:
        mp = MatchPlayer(match_id=match_id, user_id=user_id, seat_no=seat_no, color=color)
        self._session.add(mp)
        await self._session.flush()
        return mp

    async def list_by_match(self, match_id: uuid.UUID) -> list[MatchPlayer]:
        stmt = select(MatchPlayer).where(MatchPlayer.match_id == match_id).order_by(MatchPlayer.seat_no.asc())
        return (await self._session.execute(stmt)).scalars().all()


class TurnRepository:
    """Repository for Turn log entries."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append_turn(self, *, match_id: uuid.UUID, turn_no: int, user_id: Optional[uuid.UUID], payload: dict) -> Turn:
        turn = Turn(match_id=match_id, turn_no=turn_no, user_id=user_id, payload=payload)
        self._session.add(turn)
        await self._session.flush()
        return turn

    async def list_turns(self, *, match_id: uuid.UUID, limit: int = 200, offset: int = 0) -> Page:
        stmt = (
            select(Turn)
            .where(Turn.match_id == match_id)
            .order_by(Turn.turn_no.asc())
            .limit(limit)
            .offset(offset)
        )
        items = (await self._session.execute(stmt)).scalars().all()
        return Page(items=items, limit=limit, offset=offset)


class InventoryRepository:
    """Repository for InventoryItem."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_sku(self, *, user_id: uuid.UUID, sku: str) -> Optional[InventoryItem]:
        stmt = select(InventoryItem).where(InventoryItem.user_id == user_id, InventoryItem.sku == sku)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def grant_item(
        self,
        *,
        user_id: uuid.UUID,
        sku: str,
        item_type: str = "cosmetic",
        metadata: Optional[dict] = None,
    ) -> InventoryItem:
        item = InventoryItem(user_id=user_id, sku=sku, item_type=item_type, metadata=metadata or {}, is_equipped=False)
        self._session.add(item)
        await self._session.flush()
        return item

    async def grant_item_if_missing(
        self,
        *,
        user_id: uuid.UUID,
        sku: str,
        item_type: str = "cosmetic",
        metadata: Optional[dict] = None,
    ) -> InventoryItem:
        existing = await self.get_by_sku(user_id=user_id, sku=sku)
        if existing:
            return existing
        return await self.grant_item(user_id=user_id, sku=sku, item_type=item_type, metadata=metadata)

    async def list_items(self, *, user_id: uuid.UUID, limit: int = 100, offset: int = 0) -> Page:
        stmt = (
            select(InventoryItem)
            .where(InventoryItem.user_id == user_id)
            .order_by(InventoryItem.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = (await self._session.execute(stmt)).scalars().all()
        return Page(items=items, limit=limit, offset=offset)

    async def set_equipped(self, *, user_id: uuid.UUID, sku: str, is_equipped: bool) -> None:
        stmt = update(InventoryItem).where(InventoryItem.user_id == user_id, InventoryItem.sku == sku).values(is_equipped=is_equipped)
        await self._session.execute(stmt)

    async def unequip_all_of_type(self, *, user_id: uuid.UUID, item_type: str) -> None:
        stmt = update(InventoryItem).where(InventoryItem.user_id == user_id, InventoryItem.item_type == item_type).values(is_equipped=False)
        await self._session.execute(stmt)


class ReceiptRepository:
    """Repository for receipts."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_transaction(self, *, platform: str, transaction_id: str) -> Optional[Receipt]:
        stmt = select(Receipt).where(Receipt.platform == platform, Receipt.transaction_id == transaction_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        platform: str,
        product_sku: str,
        transaction_id: str,
        raw_receipt: dict,
    ) -> Receipt:
        rec = Receipt(
            user_id=user_id,
            platform=platform,
            product_sku=product_sku,
            transaction_id=transaction_id,
            status=ReceiptStatus.received,
            raw_receipt=raw_receipt,
        )
        self._session.add(rec)
        await self._session.flush()
        return rec

    async def get_or_create_received(
        self,
        *,
        user_id: uuid.UUID,
        platform: str,
        product_sku: str,
        transaction_id: str,
        raw_receipt: dict,
    ) -> Receipt:
        """
        Idempotently get-or-create a receipt row.

        - Uniqueness is (platform, transaction_id).
        - If an existing receipt belongs to a different user, we do not overwrite ownership;
          the service should treat it as already-processed and return that status.
        """
        existing = await self.get_by_transaction(platform=platform, transaction_id=transaction_id)
        if existing:
            # Keep raw receipt updated best-effort for audit/debug (without changing status).
            existing.raw_receipt = raw_receipt or existing.raw_receipt
            existing.product_sku = product_sku or existing.product_sku
            await self._session.flush()
            return existing
        return await self.create(
            user_id=user_id,
            platform=platform,
            product_sku=product_sku,
            transaction_id=transaction_id,
            raw_receipt=raw_receipt,
        )

    async def set_status(
        self,
        *,
        platform: str,
        transaction_id: str,
        status: ReceiptStatus,
        verified_at,
    ) -> None:
        stmt = (
            update(Receipt)
            .where(Receipt.platform == platform, Receipt.transaction_id == transaction_id)
            .values(status=status, verified_at=verified_at)
        )
        await self._session.execute(stmt)


class FeatureFlagRepository:
    """Repository for feature flags."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_flag(
        self, *, key: str, scope: FeatureFlagScope = FeatureFlagScope.global_scope, scope_id: Optional[str] = None
    ) -> Optional[FeatureFlag]:
        stmt = select(FeatureFlag).where(FeatureFlag.key == key, FeatureFlag.scope == scope, FeatureFlag.scope_id == scope_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def upsert(
        self,
        *,
        key: str,
        enabled: bool,
        scope: FeatureFlagScope = FeatureFlagScope.global_scope,
        scope_id: Optional[str] = None,
        description: Optional[str] = None,
    ) -> FeatureFlag:
        existing = await self.get_flag(key=key, scope=scope, scope_id=scope_id)
        if existing:
            existing.enabled = enabled
            existing.description = description
            await self._session.flush()
            return existing

        flag = FeatureFlag(key=key, enabled=enabled, scope=scope, scope_id=scope_id, description=description)
        self._session.add(flag)
        await self._session.flush()
        return flag
