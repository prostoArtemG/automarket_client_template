"""Add auto market video and Telegram channel settings.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("shop_settings", sa.Column("telegram_channel_id", sa.String(length=128), nullable=True))
    op.add_column("shop_settings", sa.Column("telegram_channel_username", sa.String(length=128), nullable=True))
    op.add_column(
        "shop_settings",
        sa.Column("autopost_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "shop_settings",
        sa.Column("autopost_with_video_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    op.add_column("products", sa.Column("video_url", sa.String(length=1024), nullable=True))
    op.add_column("products", sa.Column("video_caption", sa.Text(), nullable=True))
    op.add_column("products", sa.Column("video_source_type", sa.String(length=32), nullable=True))
    op.add_column("products", sa.Column("telegram_channel_post_id", sa.BigInteger(), nullable=True))
    op.add_column("products", sa.Column("price_usd", sa.Numeric(10, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "telegram_channel_post_id")
    op.drop_column("products", "video_source_type")
    op.drop_column("products", "video_caption")
    op.drop_column("products", "video_url")
    op.drop_column("products", "price_usd")

    op.drop_column("shop_settings", "autopost_with_video_enabled")
    op.drop_column("shop_settings", "autopost_enabled")
    op.drop_column("shop_settings", "telegram_channel_username")
    op.drop_column("shop_settings", "telegram_channel_id")
