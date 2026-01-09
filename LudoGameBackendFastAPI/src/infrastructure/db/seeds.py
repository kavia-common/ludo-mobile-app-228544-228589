from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.infrastructure.db.models import FeatureFlag, FeatureFlagScope, InventoryItem, Profile, User

logger = get_logger(__name__)


# PUBLIC_INTERFACE
async def seed_initial_data(session: AsyncSession) -> None:
    """
    Seed baseline data for development environments.

    This function is idempotent:
    - Upserts a small set of global feature flags
    - Creates a demo user + profile + starter inventory if not present

    Args:
        session: SQLAlchemy AsyncSession.
    """
    # Feature flags (baseline)
    baseline_flags = [
        ("storefront_enabled", True, "Enable cosmetic storefront endpoints."),
        ("realtime_match_state_cache", True, "Cache match state in Redis for quick reconnects."),
        ("iap_receipt_verification", False, "Enable server-side receipt verification pipeline."),
    ]

    for key, enabled, description in baseline_flags:
        stmt = select(FeatureFlag).where(
            FeatureFlag.key == key,
            FeatureFlag.scope == FeatureFlagScope.global_scope,
            FeatureFlag.scope_id.is_(None),
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing:
            existing.enabled = enabled
            existing.description = description
        else:
            session.add(
                FeatureFlag(
                    key=key,
                    enabled=enabled,
                    scope=FeatureFlagScope.global_scope,
                    scope_id=None,
                    description=description,
                )
            )

    # Demo user
    demo_username = "demo_player"
    demo_email = "demo@example.com"
    user_stmt = select(User).where(User.username == demo_username)
    demo_user = (await session.execute(user_stmt)).scalar_one_or_none()

    if demo_user is None:
        demo_user = User(id=uuid.uuid4(), username=demo_username, email=demo_email, is_guest=False)
        session.add(demo_user)
        await session.flush()

        session.add(Profile(user_id=demo_user.id, bio="Demo player for local development", country_code="US"))

        session.add(
            InventoryItem(
                user_id=demo_user.id,
                sku="theme_classic",
                item_type="theme",
                is_equipped=True,
                metadata={"name": "Classic Theme"},
            )
        )

    await session.flush()
    logger.info("Seed data applied")
