"""FastAPI site routes for the TechnoMarket Premium storefront."""
import asyncio
import logging
import os
from typing import Optional

from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.db import AsyncSessionLocal
from app.models import CategorySpec, Product, ProductSpec, ShopSettings, SiteEvent
from app.site.i18n import DEFAULT_LANG, SUPPORTED_LANGS, get_t

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _resolve_lang(lang: Optional[str], cookie: Optional[str]) -> str:
    chosen = lang or cookie or DEFAULT_LANG
    if chosen not in SUPPORTED_LANGS:
        chosen = DEFAULT_LANG
    return chosen


async def _get_shop_data() -> dict:
    """Return ShopSettings(id=1) as a dict suitable for Jinja2 templates."""
    async with AsyncSessionLocal() as session:
        shop = await session.get(ShopSettings, 1)
    if shop is None:
        return {
            "shop_title": "TechnoMarket",
            "theme_name": "light_red",
            "phone": None,
            "address": None,
            "telegram_url": None,
            "instagram_url": None,
            "logo_url": None,
        }
    return {
        "shop_title": shop.shop_title or "TechnoMarket",
        "theme_name": shop.theme_name or "light_red",
        "phone": shop.phone,
        "address": shop.address,
        "telegram_url": shop.telegram_url,
        "instagram_url": shop.instagram_url,
        "logo_url": shop.logo_url,
    }


async def _record_event(event_type: str, product_id: Optional[int] = None) -> None:
    """Fire-and-forget: persist a site analytics event."""
    try:
        async with AsyncSessionLocal() as session:
            session.add(SiteEvent(event_type=event_type, product_id=product_id))
            await session.commit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# GET /  — Shop catalog
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def shop_index(
    request: Request,
    lang: Optional[str] = None,
    lang_cookie: Optional[str] = Cookie(default=None, alias="lang"),
) -> HTMLResponse:
    chosen = _resolve_lang(lang, lang_cookie)

    async with AsyncSessionLocal() as session:
        products_rows = list((
            await session.scalars(
                select(Product)
                .order_by(Product.is_available.desc(), Product.id.desc())
            )
        ).all())

        spec_rows: list = []
        try:
            spec_rows = list((
                await session.scalars(select(ProductSpec))
            ).all())
        except Exception:
            await session.rollback()

    # Build specs_map: {product_id: {name: value}}
    _specs_by_product: dict[int, dict[str, str]] = {}
    for _sr in spec_rows:
        _specs_by_product.setdefault(_sr.product_id, {})[_sr.name] = _sr.value

    # Build available_filters from spec rows
    _filters_acc: dict[str, set] = {}
    for _sr in spec_rows:
        _filters_acc.setdefault(_sr.name, set()).add(_sr.value)
    available_filters: dict[str, list[str]] = {
        k: sorted(v) for k, v in _filters_acc.items()
    }

    products = [
        {
            "id": p.id,
            "group_name": p.group_name,
            "category": p.category,
            "name": p.name,
            "description": p.description,
            "brand": p.brand,
            "price": float(p.price) if p.price is not None else 0.0,
            "old_price": float(p.old_price) if p.old_price is not None else None,
            "specs": p.specs,
            "specs_map": _specs_by_product.get(p.id, {}),
            "image_url": p.image_url,
            "is_available": p.is_available,
            "badge": p.badge,
        }
        for p in products_rows
    ]

    client = await _get_shop_data()
    asyncio.create_task(_record_event("site_view"))

    response = templates.TemplateResponse(
        "technomarket_premium/index.html",
        {
            "request": request,
            "lang": chosen,
            "client": client,
            "products": products,
            "available_filters": available_filters,
            "event_url": "/api/event",
        },
    )
    response.set_cookie("lang", chosen, max_age=60 * 60 * 24 * 365, samesite="lax")
    return response


# ---------------------------------------------------------------------------
# GET /product/{id}  — Product page
# ---------------------------------------------------------------------------

@router.get("/product/{product_id}", response_class=HTMLResponse)
async def shop_product(
    request: Request,
    product_id: int,
    lang: Optional[str] = None,
    lang_cookie: Optional[str] = Cookie(default=None, alias="lang"),
) -> HTMLResponse:
    chosen = _resolve_lang(lang, lang_cookie)

    async with AsyncSessionLocal() as session:
        product_row = await session.get(Product, product_id)
        if product_row is None:
            raise HTTPException(status_code=404, detail="Product not found")

        spec_rows: list = []
        try:
            spec_rows = list((
                await session.scalars(
                    select(ProductSpec).where(ProductSpec.product_id == product_id)
                )
            ).all())
        except Exception:
            await session.rollback()

    specs_map = {sr.name: sr.value for sr in spec_rows}

    product_data = {
        "id": product_row.id,
        "group_name": product_row.group_name,
        "category": product_row.category,
        "name": product_row.name,
        "description": product_row.description,
        "brand": product_row.brand,
        "price": float(product_row.price) if product_row.price is not None else 0.0,
        "old_price": float(product_row.old_price) if product_row.old_price is not None else None,
        "specs": product_row.specs,
        "specs_map": specs_map,
        "image_url": product_row.image_url,
        "is_available": product_row.is_available,
        "badge": product_row.badge,
        "seo_title": product_row.seo_title,
        "seo_description": product_row.seo_description,
        "seo_keywords": product_row.seo_keywords,
    }

    client = await _get_shop_data()
    asyncio.create_task(_record_event("product_view", product_id))

    response = templates.TemplateResponse(
        "technomarket_premium/product.html",
        {
            "request": request,
            "lang": chosen,
            "client": client,
            "product": product_data,
            "catalog_url": "/",
        },
    )
    response.set_cookie("lang", chosen, max_age=60 * 60 * 24 * 365, samesite="lax")
    return response


# ---------------------------------------------------------------------------
# POST /api/event  — Site analytics event (add-to-cart, etc.)
# ---------------------------------------------------------------------------

@router.post("/api/event")
async def record_event(request: Request) -> dict:
    try:
        body = await request.json()
        event_type = str(body.get("event_type", ""))[:32]
        product_id = body.get("product_id")
        if product_id is not None:
            product_id = int(product_id)
    except Exception:
        return {"ok": False}
    if event_type:
        asyncio.create_task(_record_event(event_type, product_id))
    return {"ok": True}


# ---------------------------------------------------------------------------
# GET /404  — 404 page (also served by FastAPI exception handler)
# ---------------------------------------------------------------------------

@router.get("/404", response_class=HTMLResponse)
async def not_found_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("404.html", {"request": request}, status_code=404)
