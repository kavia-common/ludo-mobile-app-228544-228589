from __future__ import annotations

import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from src.api.deps import (
    AuthContext,
    auth_required,
    db_session_dep,
    idempotency_guard,
    idempotency_key_header,
    idempotency_store_response,
    rate_limit,
)
from src.api.schemas.store import (
    EquipRequest,
    GrantItemRequest,
    InventoryItemResponse,
    InventoryResponse,
    MarkRefundRequest,
    MarkRefundResponse,
    ReceiptStatusResponse,
    RestorePurchasesRequest,
    RestorePurchasesResponse,
    StoreItem,
    StoreListResponse,
    UnequipRequest,
    VerifyPurchaseRequest,
)
from src.application.store_service import StoreService
from src.core.config import get_settings
from src.infrastructure.db.repositories import InventoryRepository

router = APIRouter(prefix="/store", tags=["store"])


def _map_inventory_item(row) -> InventoryItemResponse:
    return InventoryItemResponse(
        sku=row.sku,
        item_type=row.item_type,
        is_equipped=bool(row.is_equipped),
        metadata=row.metadata or {},
        acquired_at=row.created_at,
    )


def _receipt_status_response(*, platform: str, sku: str, tx: str, status_str: str, detail: Optional[str], verified_at) -> ReceiptStatusResponse:
    return ReceiptStatusResponse(
        platform=platform,
        product_sku=sku,
        transaction_id=tx,
        status=status_str,
        detail=detail,
        verified_at=verified_at,
    )


@router.get(
    "/catalog",
    summary="List storefront cosmetics",
    description="Returns a static catalog of cosmetics/themes available for purchase or grant.",
    response_model=StoreListResponse,
    operation_id="list_store_catalog_v1",
)
# PUBLIC_INTERFACE
async def list_catalog() -> StoreListResponse:
    """List available cosmetics in the store (static MVP catalog)."""
    # MVP static catalog. In production, this should come from a CMS/remote-config system.
    items = [
        StoreItem(
            sku="theme_classic_red",
            title="Classic Red Theme",
            description="A classic red board theme.",
            item_type="theme",
            price_display=None,
            metadata={"rarity": "common"},
        ),
        StoreItem(
            sku="dice_gold",
            title="Gold Dice",
            description="Shiny gold dice skin.",
            item_type="dice",
            price_display=None,
            metadata={"rarity": "rare"},
        ),
    ]
    return StoreListResponse(items=items)


@router.get(
    "/inventory",
    summary="Get player inventory",
    description="Lists cosmetic items owned by the authenticated player.",
    response_model=InventoryResponse,
    operation_id="get_player_inventory_v1",
)
# PUBLIC_INTERFACE
async def get_inventory(
    request: Request,
    auth: Annotated[AuthContext, Depends(auth_required)],
    session: Annotated[AsyncSession, Depends(db_session_dep)],
    limit: int = 100,
    offset: int = 0,
) -> InventoryResponse:
    """List inventory items for the current player."""
    await rate_limit(request, bucket="inventory_list", limit=120, window_seconds=60)
    repo = InventoryRepository(session)
    page = await repo.list_items(user_id=auth.user.id, limit=min(limit, 200), offset=max(offset, 0))
    return InventoryResponse(
        items=[_map_inventory_item(i) for i in page.items],
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/inventory/equip",
    summary="Equip an owned cosmetic item",
    description="Marks a given owned SKU as equipped. Optionally enforces single-slot per item_type by unequipping others.",
    response_model=InventoryResponse,
    operation_id="equip_inventory_item_v1",
)
# PUBLIC_INTERFACE
async def equip_item(
    request: Request,
    body: EquipRequest,
    auth: Annotated[AuthContext, Depends(auth_required)],
    session: Annotated[AsyncSession, Depends(db_session_dep)],
) -> InventoryResponse:
    """Equip a cosmetic item for the authenticated user."""
    await rate_limit(request, bucket="inventory_equip", limit=120, window_seconds=60)
    repo = InventoryRepository(session)
    row = await repo.get_by_sku(user_id=auth.user.id, sku=body.sku)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not owned")

    if body.item_type:
        await repo.unequip_all_of_type(user_id=auth.user.id, item_type=body.item_type)

    await repo.set_equipped(user_id=auth.user.id, sku=body.sku, is_equipped=True)
    await session.commit()

    page = await repo.list_items(user_id=auth.user.id, limit=200, offset=0)
    return InventoryResponse(items=[_map_inventory_item(i) for i in page.items], limit=page.limit, offset=page.offset)


@router.post(
    "/inventory/unequip",
    summary="Unequip an owned cosmetic item",
    description="Marks a given owned SKU as unequipped.",
    response_model=InventoryResponse,
    operation_id="unequip_inventory_item_v1",
)
# PUBLIC_INTERFACE
async def unequip_item(
    request: Request,
    body: UnequipRequest,
    auth: Annotated[AuthContext, Depends(auth_required)],
    session: Annotated[AsyncSession, Depends(db_session_dep)],
) -> InventoryResponse:
    """Unequip a cosmetic item for the authenticated user."""
    await rate_limit(request, bucket="inventory_unequip", limit=120, window_seconds=60)
    repo = InventoryRepository(session)
    row = await repo.get_by_sku(user_id=auth.user.id, sku=body.sku)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not owned")

    await repo.set_equipped(user_id=auth.user.id, sku=body.sku, is_equipped=False)
    await session.commit()

    page = await repo.list_items(user_id=auth.user.id, limit=200, offset=0)
    return InventoryResponse(items=[_map_inventory_item(i) for i in page.items], limit=page.limit, offset=page.offset)


@router.post(
    "/admin/grant",
    summary="Grant an item (admin-safe stub)",
    description=(
        "Admin-safe stub for granting items. Requires STORE_ADMIN_KEY header match (if configured). "
        "If not configured, endpoint is disabled."
    ),
    response_model=InventoryResponse,
    operation_id="admin_grant_item_v1",
)
# PUBLIC_INTERFACE
async def admin_grant_item(
    request: Request,
    body: GrantItemRequest,
    session: Annotated[AsyncSession, Depends(db_session_dep)],
    admin_key: Annotated[Optional[str], Header(alias="X-Admin-Key")] = None,
) -> InventoryResponse:
    """Grant an item to a user (stubbed admin endpoint)."""
    await rate_limit(request, bucket="store_admin_grant", limit=30, window_seconds=60)

    settings = get_settings()
    expected = getattr(settings, "store_admin_key", None)  # may not exist in older settings
    if expected is None:
        # Graceful but secure default: disabled unless configured.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Admin grant not configured")
    if not admin_key or admin_key != expected:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid admin key")

    try:
        target_user_id = uuid.UUID(body.user_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid user_id") from exc

    repo = InventoryRepository(session)
    await repo.grant_item_if_missing(user_id=target_user_id, sku=body.sku, item_type=body.item_type, metadata=body.metadata)
    await session.commit()

    page = await repo.list_items(user_id=target_user_id, limit=200, offset=0)
    return InventoryResponse(items=[_map_inventory_item(i) for i in page.items], limit=page.limit, offset=page.offset)


@router.post(
    "/purchase/verify",
    summary="Verify purchase receipt (Apple/Google)",
    description="Idempotently verifies an iOS/Android receipt and grants the purchased SKU to inventory on success.",
    response_model=ReceiptStatusResponse,
    operation_id="verify_purchase_receipt_v1",
)
# PUBLIC_INTERFACE
async def verify_purchase(
    request: Request,
    body: VerifyPurchaseRequest,
    auth: Annotated[AuthContext, Depends(auth_required)],
    idempotency_key: Annotated[Optional[str], Depends(idempotency_key_header)],
    session: Annotated[AsyncSession, Depends(db_session_dep)],
) -> ReceiptStatusResponse:
    """
    Verify a purchase receipt and grant item if verified.

    Idempotency:
    - Header Idempotency-Key is supported (best-effort via Redis) to avoid duplicate work on client retries.
    - Strong idempotency is always enforced at DB-level by unique (platform, transaction_id).
    """
    await rate_limit(request, bucket="purchase_verify", limit=60, window_seconds=60)

    cached = await idempotency_guard(
        request,
        namespace="purchase_verify",
        user_id=auth.user.id,
        idempotency_key=idempotency_key,
        ttl_seconds=24 * 3600,
    )
    if cached:
        return ReceiptStatusResponse(**cached)

    service = StoreService(session)
    result = await service.process_purchase(
        user_id=auth.user.id,
        platform=body.platform,
        product_sku=body.product_sku,
        transaction_id=body.transaction_id,
        receipt=body.receipt,
    )

    resp = _receipt_status_response(
        platform=result.platform,
        sku=result.product_sku,
        tx=result.transaction_id,
        status_str=result.status,
        detail=result.detail,
        verified_at=result.verified_at,
    )

    await idempotency_store_response(
        namespace="purchase_verify",
        user_id=auth.user.id,
        idempotency_key=idempotency_key,
        response_json=resp.model_dump(),
        ttl_seconds=24 * 3600,
    )
    return resp


@router.post(
    "/purchase/restore",
    summary="Restore purchases",
    description="Processes a list of historical receipts idempotently and re-grants verified SKUs if missing.",
    response_model=RestorePurchasesResponse,
    operation_id="restore_purchases_v1",
)
# PUBLIC_INTERFACE
async def restore_purchases(
    request: Request,
    body: RestorePurchasesRequest,
    auth: Annotated[AuthContext, Depends(auth_required)],
    session: Annotated[AsyncSession, Depends(db_session_dep)],
) -> RestorePurchasesResponse:
    """Restore purchases for a user by processing a batch of receipts idempotently."""
    await rate_limit(request, bucket="purchase_restore", limit=20, window_seconds=60)

    service = StoreService(session)
    results: list[ReceiptStatusResponse] = []
    for item in body.receipts:
        # Force platform from outer request if present; allow per-item override in payload for flexibility.
        platform = item.platform or body.platform
        res = await service.process_purchase(
            user_id=auth.user.id,
            platform=platform,
            product_sku=item.product_sku,
            transaction_id=item.transaction_id,
            receipt=item.receipt,
        )
        results.append(
            _receipt_status_response(
                platform=res.platform,
                sku=res.product_sku,
                tx=res.transaction_id,
                status_str=res.status,
                detail=res.detail,
                verified_at=res.verified_at,
            )
        )
    return RestorePurchasesResponse(results=results)


@router.post(
    "/purchase/refund",
    summary="Handle refund (stub endpoint)",
    description="Marks a transaction as refunded. Intended for webhook integration/admin tooling.",
    response_model=MarkRefundResponse,
    operation_id="mark_refunded_v1",
)
# PUBLIC_INTERFACE
async def mark_refund(
    request: Request,
    body: MarkRefundRequest,
    session: Annotated[AsyncSession, Depends(db_session_dep)],
) -> MarkRefundResponse:
    """Mark a transaction as refunded (stub)."""
    await rate_limit(request, bucket="purchase_refund", limit=30, window_seconds=60)

    service = StoreService(session)
    res = await service.mark_refunded(platform=body.platform, transaction_id=body.transaction_id)
    if not res:
        return MarkRefundResponse(platform=body.platform, transaction_id=body.transaction_id, status="not_found", detail="Receipt not found")
    return MarkRefundResponse(platform=res.platform, transaction_id=res.transaction_id, status=res.status, detail=body.reason or res.detail)
