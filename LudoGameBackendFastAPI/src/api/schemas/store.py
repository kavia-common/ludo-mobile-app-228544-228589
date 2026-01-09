from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class StoreItem(BaseModel):
    """A purchasable or grantable cosmetic item in the storefront."""

    sku: str = Field(..., description="Unique product identifier (SKU) used by clients and IAP stores.")
    title: str = Field(..., description="Display title for the item.")
    description: str = Field(default="", description="Longer description.")
    item_type: str = Field(default="cosmetic", description="Type/category (theme, board, dice, etc.).")
    price_display: Optional[str] = Field(
        default=None,
        description="Human readable price (client/store owned). Server may omit if unknown.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional item metadata (colors, assets, etc.).")


class StoreListResponse(BaseModel):
    """List of storefront items."""

    items: list[StoreItem] = Field(..., description="Store items.")


class InventoryItemResponse(BaseModel):
    """Inventory item owned by a user."""

    sku: str = Field(..., description="Owned SKU.")
    item_type: str = Field(..., description="Type/category (theme, board, dice, etc.).")
    is_equipped: bool = Field(..., description="Whether item is currently equipped.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Per-item metadata stored server-side.")
    acquired_at: Optional[datetime] = Field(default=None, description="When the item was granted/purchased.")


class InventoryResponse(BaseModel):
    """List inventory items for a player."""

    items: list[InventoryItemResponse] = Field(..., description="Inventory items.")
    limit: int = Field(..., description="Pagination limit.")
    offset: int = Field(..., description="Pagination offset.")


class GrantItemRequest(BaseModel):
    """Admin-safe stub request to grant a cosmetic item."""

    user_id: str = Field(..., description="Target user id (UUID).")
    sku: str = Field(..., description="SKU to grant.")
    item_type: str = Field(default="cosmetic", description="Item type/category.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Optional metadata to store with item.")


class EquipRequest(BaseModel):
    """Equip an owned item."""

    sku: str = Field(..., description="SKU to equip.")
    item_type: Optional[str] = Field(
        default=None,
        description="Optional item type for single-slot equip semantics (if provided, unequip others of same type).",
    )


class UnequipRequest(BaseModel):
    """Unequip an owned item."""

    sku: str = Field(..., description="SKU to unequip.")


class ReceiptPlatform(str):
    """Allowed purchase platforms."""

    ios = "ios"
    android = "android"


class VerifyPurchaseRequest(BaseModel):
    """Request to verify an in-app purchase receipt."""

    platform: str = Field(..., description="Purchase platform: ios or android.")
    product_sku: str = Field(..., description="SKU being purchased.")
    transaction_id: str = Field(..., description="Platform transaction id / order id (idempotency key).")
    receipt: dict[str, Any] = Field(
        ...,
        description="Raw receipt payload. iOS: signedTransactionInfo/JWS fields; Android: purchaseToken + signature etc.",
    )


class ReceiptStatusResponse(BaseModel):
    """Persisted receipt status response."""

    platform: str = Field(..., description="ios/android")
    product_sku: str = Field(..., description="SKU verified/received.")
    transaction_id: str = Field(..., description="Unique transaction id (idempotency key).")
    status: str = Field(..., description="pending|verified|failed|refunded (pending maps to DB 'received').")
    detail: Optional[str] = Field(default=None, description="Extra info / reason when pending/failed.")
    verified_at: Optional[datetime] = Field(default=None, description="Verification timestamp when verified/refunded.")


class RestorePurchasesRequest(BaseModel):
    """Request to restore a set of historical purchases."""

    platform: str = Field(..., description="ios/android")
    receipts: list[VerifyPurchaseRequest] = Field(
        ...,
        description="Receipts to restore; each will be processed idempotently.",
    )


class RestorePurchasesResponse(BaseModel):
    """Restore response."""

    results: list[ReceiptStatusResponse] = Field(..., description="Per-receipt processing results.")


class MarkRefundRequest(BaseModel):
    """Manually mark a transaction as refunded (webhook/admin stub)."""

    platform: str = Field(..., description="ios/android")
    transaction_id: str = Field(..., description="Transaction id to mark refunded.")
    reason: Optional[str] = Field(default=None, description="Optional reason/message for audit.")


class MarkRefundResponse(BaseModel):
    """Refund marking response."""

    platform: str = Field(..., description="ios/android")
    transaction_id: str = Field(..., description="Transaction id.")
    status: str = Field(..., description="refunded or not_found.")
    detail: Optional[str] = Field(default=None, description="Additional detail.")
