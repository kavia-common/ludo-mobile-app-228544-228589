from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.infrastructure.db.models import ReceiptStatus
from src.infrastructure.db.repositories import InventoryRepository, ReceiptRepository
from src.infrastructure.iap.apple import verify_apple_receipt
from src.infrastructure.iap.google import verify_google_receipt

logger = get_logger(__name__)


@dataclass(frozen=True)
class ReceiptProcessResult:
    """Application-level result for a receipt verification attempt."""

    platform: str
    product_sku: str
    transaction_id: str
    status: str  # pending|verified|failed|refunded
    detail: Optional[str] = None
    verified_at: Optional[dt.datetime] = None


def _to_db_status(api_status: str) -> ReceiptStatus:
    """
    Map API-level receipt status to persisted DB enum.

    pending -> received
    failed  -> rejected
    """
    api_status = (api_status or "").lower().strip()
    if api_status == "pending":
        return ReceiptStatus.received
    if api_status == "failed":
        return ReceiptStatus.rejected
    if api_status == "verified":
        return ReceiptStatus.verified
    if api_status == "refunded":
        return ReceiptStatus.refunded
    # Default safe fallback
    return ReceiptStatus.received


def _from_db_status(db_status: ReceiptStatus) -> str:
    if db_status == ReceiptStatus.received:
        return "pending"
    if db_status == ReceiptStatus.rejected:
        return "failed"
    if db_status == ReceiptStatus.verified:
        return "verified"
    if db_status == ReceiptStatus.refunded:
        return "refunded"
    # If aliases appear, pass through
    return str(db_status.value)


class StoreService:
    """Store/inventory purchase processing service."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._receipts = ReceiptRepository(session)
        self._inventory = InventoryRepository(session)

    async def process_purchase(
        self,
        *,
        user_id,
        platform: str,
        product_sku: str,
        transaction_id: str,
        receipt: dict[str, Any],
    ) -> ReceiptProcessResult:
        """
        Idempotently process a purchase receipt:
        1) get-or-create receipt row keyed by (platform, transaction_id)
        2) if already verified/refunded, return existing status without re-granting
        3) call platform adapter (stub) to verify
        4) on verified => grant inventory item if missing
        5) persist final status and verified_at

        This is DB-idempotent even without Redis.
        """
        platform_norm = platform.lower().strip()
        if platform_norm not in ("ios", "android"):
            return ReceiptProcessResult(
                platform=platform_norm,
                product_sku=product_sku,
                transaction_id=transaction_id,
                status="failed",
                detail="Unsupported platform.",
            )

        rec = await self._receipts.get_or_create_received(
            user_id=user_id,
            platform=platform_norm,
            product_sku=product_sku,
            transaction_id=transaction_id,
            raw_receipt=receipt or {},
        )

        # If the receipt already exists, it may have been processed. Ensure idempotency.
        existing_status = _from_db_status(rec.status)
        if existing_status in ("verified", "refunded"):
            return ReceiptProcessResult(
                platform=platform_norm,
                product_sku=rec.product_sku,
                transaction_id=rec.transaction_id,
                status=existing_status,
                detail="Receipt already processed.",
                verified_at=rec.verified_at,
            )

        # If receipt belongs to another user, do not grant again.
        if rec.user_id != user_id:
            return ReceiptProcessResult(
                platform=platform_norm,
                product_sku=rec.product_sku,
                transaction_id=rec.transaction_id,
                status=existing_status,
                detail="Receipt already associated with another user.",
                verified_at=rec.verified_at,
            )

        # Verify with platform adapter (stub).
        if platform_norm == "ios":
            result = verify_apple_receipt(receipt=receipt or {}, transaction_id=transaction_id, product_sku=product_sku)
            api_status = result.status
            detail = result.detail
        else:
            result = verify_google_receipt(receipt=receipt or {}, transaction_id=transaction_id, product_sku=product_sku)
            api_status = result.status
            detail = result.detail

        # Persist status + grant item if verified
        now = dt.datetime.now(dt.timezone.utc)
        db_status = _to_db_status(api_status)
        verified_at = now if api_status in ("verified", "refunded") else None

        # If verified, grant item idempotently.
        if api_status == "verified":
            await self._inventory.grant_item_if_missing(user_id=user_id, sku=product_sku, item_type="cosmetic", metadata={})

        await self._receipts.set_status(
            platform=platform_norm,
            transaction_id=transaction_id,
            status=db_status,
            verified_at=verified_at,
        )
        await self._session.commit()

        return ReceiptProcessResult(
            platform=platform_norm,
            product_sku=product_sku,
            transaction_id=transaction_id,
            status=_from_db_status(db_status),
            detail=detail,
            verified_at=verified_at,
        )

    async def mark_refunded(self, *, platform: str, transaction_id: str) -> ReceiptProcessResult | None:
        """Mark a receipt as refunded (idempotent)."""
        platform_norm = platform.lower().strip()
        rec = await self._receipts.get_by_transaction(platform=platform_norm, transaction_id=transaction_id)
        if not rec:
            return None

        now = dt.datetime.now(dt.timezone.utc)
        await self._receipts.set_status(
            platform=platform_norm,
            transaction_id=transaction_id,
            status=ReceiptStatus.refunded,
            verified_at=now,
        )
        await self._session.commit()
        return ReceiptProcessResult(
            platform=platform_norm,
            product_sku=rec.product_sku,
            transaction_id=transaction_id,
            status="refunded",
            detail="Marked refunded.",
            verified_at=now,
        )
