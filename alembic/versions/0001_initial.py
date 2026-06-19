"""Initial schema for technomarket_client_template.

Revision ID: 0001
Revises:
Create Date: 2026-06-19
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── shop_settings (single-row config table, id=1) ──────────────────────
    op.create_table(
        "shop_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(8), nullable=False, server_default="uk"),
        sa.Column("currency", sa.String(8), nullable=False, server_default="UAH"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Europe/Kyiv"),
        sa.Column("theme_name", sa.String(64), nullable=False, server_default="light_red"),
        sa.Column("shop_title", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("address", sa.String(255), nullable=True),
        sa.Column("telegram_url", sa.String(512), nullable=True),
        sa.Column("instagram_url", sa.String(512), nullable=True),
        sa.Column("logo_url", sa.String(1024), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── products ──────────────────────────────────────────────────────────────
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_name", sa.String(128), nullable=True),
        sa.Column("category", sa.String(128), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("image_url", sa.String(1024), nullable=True),
        sa.Column("brand", sa.String(128), nullable=True),
        sa.Column("old_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("specs", sa.Text(), nullable=True),
        sa.Column("is_available", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("badge", sa.String(64), nullable=True),
        sa.Column("seo_title", sa.String(255), nullable=True),
        sa.Column("seo_description", sa.Text(), nullable=True),
        sa.Column("seo_keywords", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── product_specs ─────────────────────────────────────────────────────────
    op.create_table(
        "product_specs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("value", sa.String(512), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_product_specs_product_id", "product_specs", ["product_id"])

    # ── category_specs ────────────────────────────────────────────────────────
    op.create_table(
        "category_specs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(128), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("is_filterable", sa.Boolean(), nullable=False, server_default="true"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("category", "name", name="uq_category_specs_cat_name"),
    )

    # ── orders ────────────────────────────────────────────────────────────────
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_name", sa.String(255), nullable=False),
        sa.Column("customer_phone", sa.String(64), nullable=False),
        sa.Column("customer_city", sa.String(255), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("items_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("total", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="new",
            index=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_orders_status", "orders", ["status"])

    # ── site_events ───────────────────────────────────────────────────────────
    op.create_table(
        "site_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("site_events")
    op.drop_index("ix_orders_status", table_name="orders")
    op.drop_table("orders")
    op.drop_table("category_specs")
    op.drop_index("ix_product_specs_product_id", table_name="product_specs")
    op.drop_table("product_specs")
    op.drop_table("products")
    op.drop_table("shop_settings")
