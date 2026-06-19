"""Extend shop_settings + add shop_admins table.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-19
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── shop_settings: new columns ────────────────────────────────────────────
    op.add_column("shop_settings", sa.Column("phone2",           sa.String(64),   nullable=True))
    op.add_column("shop_settings", sa.Column("viber_url",        sa.String(512),  nullable=True))
    op.add_column("shop_settings", sa.Column("subtitle",         sa.String(512),  nullable=True))
    op.add_column("shop_settings", sa.Column("show_lang_switch", sa.Boolean(),    nullable=False, server_default=sa.true()))
    op.add_column("shop_settings", sa.Column("promo_text",       sa.Text(),       nullable=True))
    op.add_column("shop_settings", sa.Column("show_promo_bar",   sa.Boolean(),    nullable=False, server_default=sa.true()))
    op.add_column("shop_settings", sa.Column("show_banner",      sa.Boolean(),    nullable=False, server_default=sa.true()))

    # ── shop_admins: dynamic bot admins managed via bot ───────────────────────
    op.create_table(
        "shop_admins",
        sa.Column("id",          sa.Integer(),                  nullable=False),
        sa.Column("telegram_id", sa.BigInteger(),               nullable=False),
        sa.Column("username",    sa.String(128),                nullable=True),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_id", name="uq_shop_admins_telegram_id"),
    )
    op.create_index("ix_shop_admins_telegram_id", "shop_admins", ["telegram_id"])


def downgrade() -> None:
    op.drop_index("ix_shop_admins_telegram_id", table_name="shop_admins")
    op.drop_table("shop_admins")

    op.drop_column("shop_settings", "show_banner")
    op.drop_column("shop_settings", "show_promo_bar")
    op.drop_column("shop_settings", "promo_text")
    op.drop_column("shop_settings", "show_lang_switch")
    op.drop_column("shop_settings", "subtitle")
    op.drop_column("shop_settings", "viber_url")
    op.drop_column("shop_settings", "phone2")
