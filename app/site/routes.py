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
from app.models import CategorySpec, Product, ProductImage, ProductSpec, ShopSettings, SiteEvent
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
            "phone2": None,
            "viber_url": None,
            "address": None,
            "telegram_url": None,
            "instagram_url": None,
            "logo_url": None,
            "subtitle": None,
            "show_lang_switch": True,
            "promo_text": None,
            "show_promo_bar": True,
            "show_banner": True,
            "background_image_url": None,
            "show_background_image": True,
        }
    return {
        "shop_title": shop.shop_title or "TechnoMarket",
        "theme_name": shop.theme_name or "light_red",
        "phone": shop.phone,
        "phone2": shop.phone2,
        "viber_url": shop.viber_url,
        "address": shop.address,
        "telegram_url": shop.telegram_url,
        "instagram_url": shop.instagram_url,
        "logo_url": shop.logo_url,
        "subtitle": shop.subtitle,
        "show_lang_switch": shop.show_lang_switch,
        "promo_text": shop.promo_text,
        "show_promo_bar": shop.show_promo_bar,
        "show_banner": shop.show_banner,
        "background_image_url": shop.background_image_url,
        "show_background_image": shop.show_background_image,
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

        # Extract product data inside session to avoid DetachedInstanceError
        _specs_by_product: dict[int, dict[str, str]] = {}

        # Build product category/group lookup (needed for per-group filter split)
        _prod_cat_map: dict[int, tuple[str, str]] = {
            p.id: (p.category or "", p.group_name or "")
            for p in products_rows
        }

        # Fetch CategorySpec — tracks which specs are filterable per category.
        # A (category, spec_name) NOT in this table defaults to filterable=True.
        # A row with is_filterable=False hides that spec from the filter sidebar.
        _cat_spec_rows = list((await session.scalars(select(CategorySpec))).all())
        _disabled_specs: set[tuple[str, str]] = {
            (cs.category or "", cs.name or "")
            for cs in _cat_spec_rows
            if not cs.is_filterable
        }

        category_filters: dict[str, dict[str, list]] = {}
        try:
            spec_rows = list((
                await session.scalars(select(ProductSpec))
            ).all())
            _group_acc: dict[str, dict[str, set]] = {}
            for _sr in spec_rows:
                _specs_by_product.setdefault(_sr.product_id, {})[_sr.name] = _sr.value
                cat, group = _prod_cat_map.get(_sr.product_id, ("", ""))
                if (cat, _sr.name) not in _disabled_specs:
                    _key = group if group else cat
                    if _key:
                        _group_acc.setdefault(_key, {}).setdefault(_sr.name, set()).add(_sr.value)
            category_filters = {
                g: {k: sorted(v) for k, v in sp.items()}
                for g, sp in _group_acc.items()
            }
        except Exception:
            await session.rollback()

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
            "category_filters": category_filters,
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

        # Extract all scalar fields immediately — before any potential rollback
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
            "image_url": product_row.image_url,
            "is_available": product_row.is_available,
            "badge": product_row.badge,
            "seo_title": product_row.seo_title,
            "seo_description": product_row.seo_description,
            "seo_keywords": product_row.seo_keywords,
        }

        specs_map: dict[str, str] = {}
        try:
            spec_rows = list((
                await session.scalars(
                    select(ProductSpec).where(ProductSpec.product_id == product_id).order_by(ProductSpec.id)
                )
            ).all())
            specs_map = {sr.name: sr.value for sr in spec_rows}
        except Exception:
            await session.rollback()

        images: list[dict] = []
        try:
            image_rows = list((
                await session.scalars(
                    select(ProductImage)
                    .where(ProductImage.product_id == product_id)
                    .order_by(ProductImage.sort_order)
                )
            ).all())
            images = [{"url": img.image_url, "is_main": img.is_main} for img in image_rows]
        except Exception:
            await session.rollback()

        # Fall back to product.image_url if no ProductImage rows
        if not images and product_data["image_url"]:
            images = [{"url": product_data["image_url"], "is_main": True}]

        product_data["specs_map"] = specs_map
        product_data["images"] = images

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

@router.get("/api/diag/settings")
async def diag_settings() -> dict:
    """Diagnostics endpoint — shows current ShopSettings without sensitive data."""
    client = await _get_shop_data()
    return {
        "theme_name": client.get("theme_name"),
        "background_image_url": client.get("background_image_url"),
        "show_background_image": client.get("show_background_image"),
        "shop_title": client.get("shop_title"),
        "subtitle": client.get("subtitle"),
        "show_banner": client.get("show_banner"),
        "show_promo_bar": client.get("show_promo_bar"),
        "logo_url": client.get("logo_url"),
    }


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
