"""Add background_image_url and show_background_image to shop_settings.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-20
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shop_settings",
        sa.Column("background_image_url", sa.String(1024), nullable=True),
    )
    op.add_column(
        "shop_settings",
        sa.Column(
            "show_background_image",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("shop_settings", "show_background_image")
    op.drop_column("shop_settings", "background_image_url")
