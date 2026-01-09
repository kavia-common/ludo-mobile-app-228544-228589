from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Optional

from src.core.config import get_settings
from src.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class AppleVerificationResult:
    """Result of verifying an Apple receipt (stubbed)."""

    ok: bool
    status: str  # verified|pending|failed|refunded
    transaction_id: Optional[str] = None
    product_sku: Optional[str] = None
    detail: Optional[str] = None
    raw: dict[str, Any] | None = None


def _b64url_decode(data: str) -> bytes:
    padding = "=" * ((4 - (len(data) % 4)) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("utf-8"))


def _parse_jws_without_verification(jws: str) -> dict[str, Any]:
    """
    Parse JWS payload without verifying the signature.

    This is intentionally a STUB for preview/dev. Production must:
    - validate signature chain (Apple root cert),
    - validate `iss`, `aud`, bundle id, environment,
    - validate `transactionId` / `originalTransactionId`,
    - verify against Apple App Store Server API when appropriate.
    """
    try:
        _header_b64, payload_b64, _sig_b64 = jws.split(".", 2)
    except ValueError as exc:
        raise ValueError("Invalid JWS format") from exc

    payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    return payload


# PUBLIC_INTERFACE
def verify_apple_receipt(*, receipt: dict[str, Any], transaction_id: str, product_sku: str) -> AppleVerificationResult:
    """
    Verify an Apple receipt (server-side validation stub).

    Expected receipt fields (MVP):
    - signedTransactionInfo: a JWS string (App Store Server API v2 style), OR
    - jws: a JWS string (alias)

    Graceful behavior:
    - If IAP_VERIFICATION_ENABLED is false OR required Apple config not set, returns pending with detail.
    - Performs minimal structure checks and (optional) bundle id check by decoding payload without signature verification.

    Returns:
        AppleVerificationResult: verification outcome.

    TODO (production):
    - Verify JWS signature (x5c chain) and validate certificate fingerprint against Apple root.
    - Call App Store Server API /verifyReceipt or transaction history endpoints.
    - Handle revocations/refunds via server notifications.
    """
    settings = get_settings()
    if not settings.iap_verification_enabled:
        return AppleVerificationResult(
            ok=False,
            status="pending",
            transaction_id=transaction_id,
            product_sku=product_sku,
            detail="IAP verification disabled (IAP_VERIFICATION_ENABLED=false); receipt stored as pending.",
            raw={"note": "verification_disabled"},
        )

    if not settings.apple_iap_bundle_id:
        return AppleVerificationResult(
            ok=False,
            status="pending",
            transaction_id=transaction_id,
            product_sku=product_sku,
            detail="APPLE_IAP_BUNDLE_ID not configured; receipt stored as pending.",
            raw={"note": "missing_bundle_id"},
        )

    jws = receipt.get("signedTransactionInfo") or receipt.get("jws")
    if not isinstance(jws, str) or not jws:
        return AppleVerificationResult(
            ok=False,
            status="failed",
            transaction_id=transaction_id,
            product_sku=product_sku,
            detail="Missing signedTransactionInfo/jws in receipt payload.",
            raw={"note": "missing_jws"},
        )

    try:
        payload = _parse_jws_without_verification(jws)
    except Exception:
        logger.exception("Failed to parse Apple JWS payload")
        return AppleVerificationResult(
            ok=False,
            status="failed",
            transaction_id=transaction_id,
            product_sku=product_sku,
            detail="Invalid JWS receipt payload.",
            raw={"note": "invalid_jws"},
        )

    # Best-effort semantic checks (non-authoritative in stub mode).
    bundle_id = payload.get("bundleId") or payload.get("bid")
    if bundle_id and bundle_id != settings.apple_iap_bundle_id:
        return AppleVerificationResult(
            ok=False,
            status="failed",
            transaction_id=transaction_id,
            product_sku=product_sku,
            detail="Bundle id mismatch.",
            raw={"bundleId": bundle_id},
        )

    # SKU check if present
    decoded_sku = payload.get("productId") or payload.get("product_id")
    if decoded_sku and decoded_sku != product_sku:
        return AppleVerificationResult(
            ok=False,
            status="failed",
            transaction_id=transaction_id,
            product_sku=product_sku,
            detail="Product SKU mismatch.",
            raw={"productId": decoded_sku},
        )

    # Transaction id check if present
    decoded_tx = payload.get("transactionId") or payload.get("transaction_id")
    if decoded_tx and str(decoded_tx) != transaction_id:
        return AppleVerificationResult(
            ok=False,
            status="failed",
            transaction_id=transaction_id,
            product_sku=product_sku,
            detail="Transaction id mismatch.",
            raw={"transactionId": decoded_tx},
        )

    # Stub assumes verified if structure OK.
    return AppleVerificationResult(
        ok=True,
        status="verified",
        transaction_id=transaction_id,
        product_sku=product_sku,
        detail="Stub verification passed (signature not verified).",
        raw={"decoded": payload, "note": "stub_no_signature_verification"},
    )
