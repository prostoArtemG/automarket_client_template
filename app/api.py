"""HTTP API for the shop."""
import json as _json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.db import AsyncSessionLocal
from app.models import Order, Product, ProductSpec, SiteEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Catalog (products JSON — for external integrations)
# ---------------------------------------------------------------------------

@router.get("/catalog")
async def catalog() -> dict:
    async with AsyncSessionLocal() as session:
        products = list(await session.scalars(
            select(Product)
            .where(Product.is_available.is_(True))
            .order_by(Product.id.desc())
        ))
    return {
        "count": len(products),
        "items": [
            {
                "id": p.id,
                "name": p.name,
                "brand": p.brand,
                "category": p.category,
                "group_name": p.group_name,
                "price": float(p.price),
                "price_usd": float(p.price_usd) if p.price_usd is not None else None,
                "old_price": float(p.old_price) if p.old_price else None,
                "image_url": p.image_url,
                "badge": p.badge,
                "is_available": p.is_available,
                "video_url": p.video_url,
                "video_caption": p.video_caption,
                "video_source_type": p.video_source_type,
            }
            for p in products
        ],
    }


# ---------------------------------------------------------------------------
# Order (called by the storefront JS)
# ---------------------------------------------------------------------------

class SiteOrderRequest(BaseModel):
    name: str
    phone: str
    city: str = ""
    comment: str = ""
    product_id: Optional[int] = None   # single-product order (from product page)
    items: list[dict] = []             # explicit items list
    cart: list[dict] = []              # cart sent by index.html JS


@router.post("/order")
async def create_order(data: SiteOrderRequest, request: Request) -> dict:
    # Resolve effective items from all possible sources
    effective_items: list[dict] = data.items or data.cart or []

    # If no items but product_id provided — look up the product
    if not effective_items and data.product_id:
        async with AsyncSessionLocal() as session:
            product = await session.get(Product, data.product_id)
            if product:
                effective_items = [{
                    "id": product.id,
                    "name": product.name,
                    "price": float(product.price),
                    "qty": 1,
                }]

    _total = sum(
        float(item.get("price", 0)) * int(item.get("qty", 1))
        for item in effective_items
    )

    async with AsyncSessionLocal() as session:
        order_obj = Order(
            customer_name=(data.name or "")[:255],
            customer_phone=(data.phone or "")[:64],
            customer_city=(data.city[:255] if data.city else None),
            comment=(data.comment or None),
            items_json=_json.dumps(effective_items, ensure_ascii=False),
            total=_total,
            status="new",
        )
        session.add(order_obj)
        session.add(SiteEvent(event_type="order"))
        await session.commit()
        await session.refresh(order_obj)
        order_id = order_obj.id

    # Notify admin(s) via Telegram
    bot = getattr(request.app.state, "bot", None)
    from app.config import settings
    if bot and settings.admin_ids:
        lines = []
        for item in effective_items:
            name = item.get("name", "?")
            qty = item.get("qty", 1)
            price = item.get("price", 0)
            lines.append(f"• {name} × {qty} — {price} грн")
        items_text = "\n".join(lines) if lines else "—"
        msg = (
            f"🛒 <b>Нове замовлення #{order_id}!</b>\n\n"
            f"👤 {data.name}\n"
            f"📞 {data.phone}\n"
            f"🏙 {data.city or '—'}\n\n"
            f"📦 Товари:\n{items_text}\n\n"
            f"💰 Разом: <b>{_total:,.0f} грн</b>\n"
            f"💬 {data.comment or '—'}"
        )
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(admin_id, msg, parse_mode="HTML")
            except Exception as exc:
                logger.warning("Failed to notify admin %s: %s", admin_id, exc)

    return {"ok": True, "order_id": order_id}


# ---------------------------------------------------------------------------
# Diagnostics — raw DB state for a product  (admin/debug use)
# ---------------------------------------------------------------------------

@router.get("/diag/product/{product_id}")
async def diag_product(product_id: int):
    """Return raw DB state for a product: Product fields + ProductSpec rows."""
    async with AsyncSessionLocal() as session:
        product = await session.get(Product, product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found")

        spec_rows = list((await session.scalars(
            select(ProductSpec)
            .where(ProductSpec.product_id == product_id)
            .order_by(ProductSpec.id)
        )).all())

    return {
        "product": {
            "id": product.id,
            "name": product.name,
            "category": product.category,
            "description": product.description,
            "specs": product.specs,
            "price_usd": float(product.price_usd) if product.price_usd is not None else None,
            "video_url": product.video_url,
            "video_caption": product.video_caption,
            "video_source_type": product.video_source_type,
            "telegram_channel_post_id": product.telegram_channel_post_id,
        },
        "product_spec_count": len(spec_rows),
        "product_specs": [
            {"id": r.id, "name": r.name, "value": r.value}
            for r in spec_rows
        ],
    }
