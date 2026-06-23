from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ShopSettings(Base):
    """Single-row table (id=1) with shop-wide configuration.

    Passed as ``client`` to Jinja2 templates so the TechnoMarket Premium
    templates work without modification.
    """

    __tablename__ = "shop_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    language: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="uk", default="uk"
    )
    currency: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="UAH", default="UAH"
    )
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="Europe/Kyiv", default="Europe/Kyiv"
    )
    theme_name: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="light_red", default="light_red"
    )
    shop_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    telegram_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    instagram_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    logo_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    # ── v2 fields ─────────────────────────────────────────────────────────────
    phone2: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    viber_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    subtitle: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    show_lang_switch: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", default=True
    )
    promo_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    show_promo_bar: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", default=True
    )
    show_banner: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", default=True
    )
    # ── v3 fields ─────────────────────────────────────────────────────────────
    background_image_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    show_background_image: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", default=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ShopAdmin(Base):
    """Dynamic admin list managed via the CMS bot.

    Users in this table gain the same access as ADMIN_IDS from the env file.
    The env ADMIN_IDS always take precedence and cannot be removed here.
    """

    __tablename__ = "shop_admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    image_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    brand: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    old_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    specs: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", default=True
    )
    badge: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    seo_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    seo_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    seo_keywords: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    specs_structured: Mapped[list["ProductSpec"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
    )
    images: Mapped[list["ProductImage"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductImage.sort_order",
    )


class ProductSpec(Base):
    """Structured spec entries for a product (one row per key-value pair).

    Parsed from Product.specs each time specs are saved.
    Used to build available_filters for the storefront sidebar.
    """

    __tablename__ = "product_specs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)

    product: Mapped["Product"] = relationship(back_populates="specs_structured")


class ProductImage(Base):
    """Up to 5 photos per product. One row is marked is_main=True.

    Product.image_url is always kept in sync with the main image.
    """

    __tablename__ = "product_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    image_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    public_id: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_main: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    product: Mapped["Product"] = relationship(back_populates="images")


class CategorySpec(Base):
    """Metadata: which spec names are filterable for a given category.

    Populated automatically when specs are saved for a product.
    ``is_filterable`` can be set to False to hide a noisy spec from filters.
    """

    __tablename__ = "category_specs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_filterable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    __table_args__ = (
        UniqueConstraint("category", "name", name="uq_category_specs_cat_name"),
    )


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    customer_phone: Mapped[str] = mapped_column(String(64), nullable=False)
    customer_city: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    items_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    total: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="new", default="new", index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SiteEvent(Base):
    __tablename__ = "site_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    product_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NavGroup(Base):
    """Lookup table: emoji and display order for each product group name.

    Keyed by the raw string stored in ``products.group_name``.
    If a group name has no row here, ``category_meta.group_emoji()``
    provides a static fallback.
    """

    __tablename__ = "nav_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    emoji: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="", default=""
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="100", default=100
    )


class NavCategory(Base):
    """Lookup table: emoji, group association, and display order per category.

    Keyed by the raw string stored in ``products.category``.
    ``group_name`` loosely links the category to a NavGroup (plain string,
    no FK constraint so orphan-safe on delete).
    """

    __tablename__ = "nav_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    emoji: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="", default=""
    )
    group_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="100", default=100
    )
