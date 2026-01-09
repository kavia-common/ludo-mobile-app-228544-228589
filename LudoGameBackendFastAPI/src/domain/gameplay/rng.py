from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from src.core.config import get_settings


def _now_ms() -> int:
    return int(time.time() * 1000)


def _require_salt() -> str:
    settings = get_settings()
    if not settings.rng_audit_salt:
        # Kept as ValueError so application layer can map to 503 (not configured).
        raise ValueError("RNG_AUDIT_SALT not configured")
    return settings.rng_audit_salt


def _hmac_sha256_hex(*, key: str, msg: str) -> str:
    return hmac.new(key.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class DiceAuditRecord:
    """
    Immutable record returned by the RNG service for persistence and auditing.

    This is designed to be stored inside Turn.payload["rng_audit"].
    """

    match_id: str
    player_id: str
    turn_no: int
    rolled_at_ms: int
    seed_nonce_hex: str
    commitment_hash: str
    dice_value: int


class VerifiableRngService:
    """
    Verifiable RNG for dice rolls using a simple commit-with-audited-seed approach.

    Approach:
    - Server generates a random nonce (seed_nonce_hex).
    - Server computes an HMAC-based commitment hash using RNG_AUDIT_SALT over:
        "{match_id}:{player_id}:{turn_no}:{seed_nonce_hex}"
    - Dice value is derived deterministically from the commitment hash bytes:
        (int(hash[0:8], 16) % 6) + 1

    Notes:
    - This does not require an explicit client reveal step; it is "auditable" because
      the server stores both nonce + commitment + derived dice in the immutable turn log.
    - A future evolution can implement true commit-reveal where clients contribute entropy.
    """

    # PUBLIC_INTERFACE
    def roll_d6(self, *, match_id: str, player_id: str, turn_no: int) -> DiceAuditRecord:
        """
        Roll a D6 with an auditable commitment record.

        Args:
            match_id: Match identifier (uuid as string).
            player_id: Player identifier (uuid as string).
            turn_no: Current turn number (server authoritative).

        Returns:
            DiceAuditRecord: includes commitment hash, nonce and dice value.

        Raises:
            ValueError: if RNG_AUDIT_SALT not configured.
        """
        salt = _require_salt()
        rolled_at_ms = _now_ms()
        seed_nonce_hex = secrets.token_hex(16)

        msg = f"{match_id}:{player_id}:{int(turn_no)}:{seed_nonce_hex}"
        commitment_hash = _hmac_sha256_hex(key=salt, msg=msg)

        # Deterministic dice from commitment hash (8 hex chars = 32 bits is plenty).
        dice_value = (int(commitment_hash[:8], 16) % 6) + 1

        return DiceAuditRecord(
            match_id=match_id,
            player_id=player_id,
            turn_no=int(turn_no),
            rolled_at_ms=rolled_at_ms,
            seed_nonce_hex=seed_nonce_hex,
            commitment_hash=commitment_hash,
            dice_value=int(dice_value),
        )

    # PUBLIC_INTERFACE
    def verify_record(self, record: DiceAuditRecord) -> bool:
        """
        Verify that a stored DiceAuditRecord is consistent with RNG_AUDIT_SALT.

        Returns:
            bool: True if the commitment hash and dice derivation match.

        Raises:
            ValueError: if RNG_AUDIT_SALT not configured.
        """
        salt = _require_salt()
        msg = f"{record.match_id}:{record.player_id}:{int(record.turn_no)}:{record.seed_nonce_hex}"
        commitment_hash = _hmac_sha256_hex(key=salt, msg=msg)
        if not hmac.compare_digest(commitment_hash, record.commitment_hash):
            return False
        expected_dice = (int(commitment_hash[:8], 16) % 6) + 1
        return int(expected_dice) == int(record.dice_value)


# PUBLIC_INTERFACE
def get_rng_service() -> VerifiableRngService:
    """Return a singleton-like RNG service (stateless)."""
    return VerifiableRngService()
