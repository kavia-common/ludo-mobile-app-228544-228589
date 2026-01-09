from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Optional

from src.core.config import get_settings
from src.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class GoogleVerificationResult:
    """Result of verifying a Google Play purchase (stubbed)."""

    ok: bool
    status: str  # verified|pending|failed|refunded
    transaction_id: Optional[str] = None
    product_sku: Optional[str] = None
    detail: Optional[str] = None
    raw: dict[str, Any] | None = None


def _safe_json_loads(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


# PUBLIC_INTERFACE
def verify_google_receipt(*, receipt: dict[str, Any], transaction_id: str, product_sku: str) -> GoogleVerificationResult:
    """
    Verify a Google Play purchase token (server-side validation stub).

    Expected receipt fields (MVP):
    - purchaseToken: string
    - signature: string (optional in stub)
    - signedData: JSON string/dict (optional; legacy signature flow)

    Graceful behavior:
    - If IAP_VERIFICATION_ENABLED is false OR required config not set, returns pending with detail.
    - Performs minimal structure checks; does NOT call Google Android Publisher API.

    TODO (production):
    - Call Google Android Publisher API:
      purchases.products.get or purchases.subscriptions.get using GOOGLE_IAP_SERVICE_ACCOUNT_JSON.
    - Validate orderId/purchaseState/acknowledgementState, and ensure packageName matches.
    - If still using legacy signature flow, verify RSA signature using GOOGLE_IAP_PUBLIC_KEY_B64.
    """
    settings = get_settings()
    if not settings.iap_verification_enabled:
        return GoogleVerificationResult(
            ok=False,
            status="pending",
            transaction_id=transaction_id,
            product_sku=product_sku,
            detail="IAP verification disabled (IAP_VERIFICATION_ENABLED=false); receipt stored as pending.",
            raw={"note": "verification_disabled"},
        )

    if not settings.google_iap_package_name:
        return GoogleVerificationResult(
            ok=False,
            status="pending",
            transaction_id=transaction_id,
            product_sku=product_sku,
            detail="GOOGLE_IAP_PACKAGE_NAME not configured; receipt stored as pending.",
            raw={"note": "missing_package_name"},
        )

    purchase_token = receipt.get("purchaseToken") or receipt.get("purchase_token")
    if not isinstance(purchase_token, str) or not purchase_token:
        return GoogleVerificationResult(
            ok=False,
            status="failed",
            transaction_id=transaction_id,
            product_sku=product_sku,
            detail="Missing purchaseToken in receipt payload.",
            raw={"note": "missing_purchase_token"},
        )

    # Minimal checks on signedData fields if provided (non-authoritative in stub).
    signed_data = _safe_json_loads(receipt.get("signedData") or receipt.get("signed_data"))
    if signed_data:
        pkg = signed_data.get("packageName")
        if pkg and pkg != settings.google_iap_package_name:
            return GoogleVerificationResult(
                ok=False,
                status="failed",
                transaction_id=transaction_id,
                product_sku=product_sku,
                detail="Package name mismatch.",
                raw={"packageName": pkg},
            )
        decoded_sku = signed_data.get("productId")
        if decoded_sku and decoded_sku != product_sku:
            return GoogleVerificationResult(
                ok=False,
                status="failed",
                transaction_id=transaction_id,
                product_sku=product_sku,
                detail="Product SKU mismatch.",
                raw={"productId": decoded_sku},
            )
        order_id = signed_data.get("orderId")
        if order_id and str(order_id) != transaction_id:
            # Many apps treat orderId as transaction id; for product purchases it is stable.
            return GoogleVerificationResult(
                ok=False,
                status="failed",
                transaction_id=transaction_id,
                product_sku=product_sku,
                detail="Transaction id/orderId mismatch.",
                raw={"orderId": order_id},
            )

    # Stub "signature checks": we just ensure fields are plausibly present if a public key is configured.
    if settings.google_iap_public_key_b64:
        try:
            base64.b64decode(settings.google_iap_public_key_b64.encode("utf-8"), validate=False)
        except Exception:
            logger.exception("Invalid GOOGLE_IAP_PUBLIC_KEY_B64")
            return GoogleVerificationResult(
                ok=False,
                status="pending",
                transaction_id=transaction_id,
                product_sku=product_sku,
                detail="Invalid GOOGLE_IAP_PUBLIC_KEY_B64; receipt stored as pending.",
                raw={"note": "bad_public_key"},
            )

    return GoogleVerificationResult(
        ok=True,
        status="verified",
        transaction_id=transaction_id,
        product_sku=product_sku,
        detail="Stub verification passed (did not call Google APIs).",
        raw={"note": "stub_no_google_api_call"},
    )
