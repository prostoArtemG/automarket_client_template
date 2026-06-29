"""Add promo speed setting for auto market storefront.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shop_settings",
        sa.Column("promo_speed", sa.String(length=16), nullable=False, server_default=sa.text("'medium'")),
    )


def downgrade() -> None:
    op.drop_column("shop_settings", "promo_speed")
