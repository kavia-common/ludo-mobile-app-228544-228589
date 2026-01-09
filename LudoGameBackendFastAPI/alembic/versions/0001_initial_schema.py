"""Initial schema for Ludo backend core entities.

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-01-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enums
    op.execute("CREATE TYPE user_status AS ENUM ('active','disabled','deleted')")
    op.execute("CREATE TYPE match_status AS ENUM ('pending','active','finished','cancelled')")
    op.execute("CREATE TYPE receipt_status AS ENUM ('received','verified','rejected','refunded')")
    op.execute("CREATE TYPE feature_flag_scope AS ENUM ('global','user','platform')")

    # users
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=True),
        sa.Column("status", sa.Enum(name="user_status"), nullable=False, server_default="active"),
        sa.Column("is_guest", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    # profiles
    op.create_table(
        "profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("mmr", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_profiles_user_id"),
    )
    op.create_index("ix_profiles_user_id", "profiles", ["user_id"], unique=False)

    # friends
    op.create_table(
        "friends",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "friend_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "friend_user_id", name="uq_friends_user_friend"),
        sa.CheckConstraint("user_id <> friend_user_id", name="ck_friends_not_self"),
    )
    op.create_index("ix_friends_user_id", "friends", ["user_id"], unique=False)
    op.create_index("ix_friends_friend_user_id", "friends", ["friend_user_id"], unique=False)

    # matches
    op.create_table(
        "matches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("status", sa.Enum(name="match_status"), nullable=False, server_default="pending"),
        sa.Column("mode", sa.String(length=32), nullable=False, server_default="classic"),
        sa.Column("is_ranked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("max_players", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("rng_seed", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_matches_status_created_at", "matches", ["status", "created_at"], unique=False)

    # match_players
    op.create_table(
        "match_players",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "match_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("matches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seat_no", sa.Integer(), nullable=False),
        sa.Column("color", sa.String(length=16), nullable=False),
        sa.Column("is_winner", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("has_left", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("match_id", "user_id", name="uq_match_players_match_user"),
        sa.UniqueConstraint("match_id", "seat_no", name="uq_match_players_match_seat"),
    )
    op.create_index("ix_match_players_match_id", "match_players", ["match_id"], unique=False)
    op.create_index("ix_match_players_user_id", "match_players", ["user_id"], unique=False)

    # turns
    op.create_table(
        "turns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "match_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("matches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("turn_no", sa.Integer(), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("match_id", "turn_no", name="uq_turns_match_turn_no"),
    )
    op.create_index("ix_turns_match_id_turn_no", "turns", ["match_id", "turn_no"], unique=False)

    # inventory_items
    op.create_table(
        "inventory_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("item_type", sa.String(length=32), nullable=False, server_default="cosmetic"),
        sa.Column("is_equipped", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "sku", name="uq_inventory_user_sku"),
    )
    op.create_index("ix_inventory_items_user_id", "inventory_items", ["user_id"], unique=False)
    op.create_index("ix_inventory_items_sku", "inventory_items", ["sku"], unique=False)

    # receipts
    op.create_table(
        "receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("product_sku", sa.String(length=64), nullable=False),
        sa.Column("transaction_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.Enum(name="receipt_status"), nullable=False, server_default="received"),
        sa.Column("raw_receipt", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("platform", "transaction_id", name="uq_receipts_platform_transaction"),
    )
    op.create_index("ix_receipts_user_id", "receipts", ["user_id"], unique=False)
    op.create_index("ix_receipts_product_sku", "receipts", ["product_sku"], unique=False)
    op.create_index("ix_receipts_status", "receipts", ["status"], unique=False)

    # feature_flags
    op.create_table(
        "feature_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("scope", sa.Enum(name="feature_flag_scope"), nullable=False, server_default="global"),
        sa.Column("scope_id", sa.String(length=128), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("key", "scope", "scope_id", name="uq_feature_flags_key_scope"),
    )
    op.create_index("ix_feature_flags_key", "feature_flags", ["key"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_feature_flags_key", table_name="feature_flags")
    op.drop_table("feature_flags")

    op.drop_index("ix_receipts_status", table_name="receipts")
    op.drop_index("ix_receipts_product_sku", table_name="receipts")
    op.drop_index("ix_receipts_user_id", table_name="receipts")
    op.drop_table("receipts")

    op.drop_index("ix_inventory_items_sku", table_name="inventory_items")
    op.drop_index("ix_inventory_items_user_id", table_name="inventory_items")
    op.drop_table("inventory_items")

    op.drop_index("ix_turns_match_id_turn_no", table_name="turns")
    op.drop_table("turns")

    op.drop_index("ix_match_players_user_id", table_name="match_players")
    op.drop_index("ix_match_players_match_id", table_name="match_players")
    op.drop_table("match_players")

    op.drop_index("ix_matches_status_created_at", table_name="matches")
    op.drop_table("matches")

    op.drop_index("ix_friends_friend_user_id", table_name="friends")
    op.drop_index("ix_friends_user_id", table_name="friends")
    op.drop_table("friends")

    op.drop_index("ix_profiles_user_id", table_name="profiles")
    op.drop_table("profiles")

    op.drop_index("ix_users_username", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")

    op.execute("DROP TYPE feature_flag_scope")
    op.execute("DROP TYPE receipt_status")
    op.execute("DROP TYPE match_status")
    op.execute("DROP TYPE user_status")
