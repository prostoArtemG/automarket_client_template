"""Telegram CMS bot for the shop owner.

Sections:
  📦 Товари     — paginated list, add / edit / delete products
  🌐 Мій сайт  — site URL link
  📊 Замовлення — order management (new / in progress / done)
  📈 Статистика — site event counts
  ⚙️ Налаштування — shop title, phone, address, social links, logo, theme
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import delete, func, or_, select

from app.bot.filters import AdminFilter
from app.bot.keyboards import (
    BTN_CMS_ADMINS,
    BTN_CMS_EMOJI,
    BTN_CMS_FILTERS,
    BTN_CMS_ORDERS,
    BTN_CMS_PRODUCTS,
    BTN_CMS_SETTINGS,
    BTN_CMS_SITE,
    BTN_CMS_STATS,
    main_menu,
)
from app.category_meta import category_emoji as _cat_emoji_fb
from app.category_meta import group_emoji as _grp_emoji_fb
from app.db import AsyncSessionLocal
from app.models import CategorySpec, NavCategory, NavGroup, Order, Product, ProductImage, ProductSpec, ShopAdmin, ShopSettings, SiteEvent

logger = logging.getLogger(__name__)

router = Router(name="cms")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())


# ── Shop helpers ───────────────────────────────────────────────────────────────

async def _get_shop() -> ShopSettings:
    """Return ShopSettings(id=1), creating it if missing."""
    async with AsyncSessionLocal() as session:
        shop = await session.get(ShopSettings, 1)
        if shop is None:
            shop = ShopSettings(id=1, shop_title="Мій магазин")
            session.add(shop)
            await session.commit()
            await session.refresh(shop)
        return shop


def _is_cloudinary_configured() -> bool:
    from app.config import settings as _s
    return bool(_s.cloudinary_cloud_name and _s.cloudinary_api_key and _s.cloudinary_api_secret)


def _configure_cloudinary() -> None:
    from app.config import settings as _s
    import cloudinary
    cloudinary.config(
        cloud_name=_s.cloudinary_cloud_name,
        api_key=_s.cloudinary_api_key,
        api_secret=_s.cloudinary_api_secret,
        secure=True,
    )


def upload_image_to_cloudinary(file_path: str, folder: str, kind: str = "product") -> str | None:
    """Upload a local file to Cloudinary with optimisation. Returns secure_url or None.

    Args:
        file_path: Absolute path to the local temporary file.
        folder:    Cloudinary folder, e.g. ``shopplatform/technovlada/products``.
        kind:      ``"product"`` (max 1600 px) or ``"logo"`` / ``"banner"`` (max 900 px).
    """
    if not _is_cloudinary_configured():
        return None
    try:
        import cloudinary.uploader
        _configure_cloudinary()
        max_width = 1600 if kind not in ("logo",) else 900
        result = cloudinary.uploader.upload(
            file_path,
            folder=folder,
            eager=[{
                "quality": "auto:good",
                "fetch_format": "auto",
                "width": max_width,
                "crop": "limit",
            }],
            eager_async=False,
        )
        # Return the optimised eager URL when available; fall back to original.
        if result.get("eager"):
            return result["eager"][0]["secure_url"]
        return result["secure_url"]
    except Exception as exc:
        logger.error("Cloudinary upload failed: %s", exc)
        return None


async def _download_and_upload(bot: Bot, file_id: str, folder: str, kind: str = "product") -> str | None:
    """Download a photo from Telegram and upload to Cloudinary. Returns secure_url or None."""
    if not _is_cloudinary_configured():
        return None
    try:
        tg_file = await bot.get_file(file_id)
        tmp = f"/tmp/{uuid4()}.jpg"
        try:
            await bot.download_file(tg_file.file_path, tmp)
            return await asyncio.to_thread(upload_image_to_cloudinary, tmp, folder=folder, kind=kind)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    except Exception as exc:
        logger.error("Telegram download failed: %s", exc)
        return None


# ── Themes ───────────────────────────────────────────────────────────────────

THEMES: dict[str, str] = {
    "default":      "🌱 За замовчуванням (зелена)",
    "light_red":    "🔴 Червоне світло (світла)",
    "navy_teal":    "🌊 Темно-синя + бірюза",
    "purple_lime":  "🟣 Фіолетова + лайм",
    "dark_parallax":"🌌 Темний паралакс",
}
VALID_THEMES: frozenset[str] = frozenset(THEMES)


def _themes_kb(current: str | None) -> InlineKeyboardMarkup:
    current = current or "default"
    rows = [
        [InlineKeyboardButton(
            text=("✅ " if key == current else "") + label,
            callback_data=f"cms:theme:{key}",
        )]
        for key, label in THEMES.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Settings helpers ──────────────────────────────────────────────────────────

SETTINGS_PROMPTS: dict[str, str] = {
    "shop_title":    "🏪 <b>Назва магазину</b>\n\nВведіть назву, яка буде відображатись в шапці сайту:",
    "phone":         "📞 <b>Телефон</b>\n\nВведіть контактний номер телефону:",
    "phone2":        "📞 <b>Телефон 2</b>\n\nВведіть другий контактний номер (необов'язково):",
    "viber_url":     "📲 <b>Viber</b>\n\nВведіть посилання на Viber\n(наприклад: <code>https://viber.me/+380XXXXXXXXX</code>):",
    "subtitle":      "💬 <b>Підзаголовок</b>\n\nВведіть підзаголовок магазину\n(відображається в шапці та на банері):",
    "address":       "📍 <b>Адреса</b>\n\nВведіть адресу магазину:",
    "telegram_url":  "✈️ <b>Telegram</b>\n\nВведіть посилання на Telegram\n(наприклад: <code>https://t.me/myshop</code>):",
    "instagram_url": "📸 <b>Instagram</b>\n\nВведіть посилання на Instagram\n(наприклад: <code>https://instagram.com/myshop</code>):",
    "telegram_channel_id": "📢 <b>Telegram канал</b>\n\nВведіть @username каналу або числовий chat_id\n(наприклад: <code>@my_auto_channel</code> або <code>-1001234567890</code>):",
    "telegram_channel_username": "🔗 <b>Публічний username каналу</b>\n\nВведіть username без посилання\n(наприклад: <code>my_auto_channel</code>) або «-» щоб очистити:",
    "logo":          "🖼 <b>Логотип</b>\n\nНадішліть фото логотипу або URL посилання на зображення:",
    "promo_text":    "📢 <b>Бігучий рядок</b>\n\nВведіть текст бігучого рядка у верхній частині сайту:",
    "background_image": "🌄 <b>Задній фон сайту</b>\n\nНадішліть фото або URL зображення для фону сайту\n(рекомендований розмір: 1920×600px+):\n\n"
        "Натисніть 🗑 <b>Очистити</b>, щоб прибрати фон.",
}
VALID_SETTINGS_FIELDS: frozenset[str] = frozenset(SETTINGS_PROMPTS)
URL_SETTINGS_FIELDS: frozenset[str] = frozenset({"telegram_url", "instagram_url"})
FIELD_ATTR: dict[str, str] = {
    "shop_title":    "shop_title",
    "phone":         "phone",
    "phone2":        "phone2",
    "viber_url":     "viber_url",
    "subtitle":      "subtitle",
    "address":       "address",
    "telegram_url":  "telegram_url",
    "instagram_url": "instagram_url",
    "telegram_channel_id": "telegram_channel_id",
    "telegram_channel_username": "telegram_channel_username",
    "logo":          "logo_url",
    "promo_text":    "promo_text",
    "background_image": "background_image_url",
}

# ── Toggle fields (boolean ShopSettings columns) ──────────────────────────────
TOGGLE_FIELDS: frozenset[str] = frozenset({
    "show_promo_bar",
    "show_lang_switch",
    "show_banner",
    "show_background_image",
    "autopost_enabled",
    "autopost_with_video_enabled",
})
TOGGLE_LABELS: dict[str, str] = {
    "show_promo_bar":         "Промо-бар",
    "show_lang_switch":       "Перемикач мови",
    "show_banner":            "Банер",
    "show_background_image":  "Задній фон",
    "autopost_enabled":       "Автопост у канал",
    "autopost_with_video_enabled": "Додавати відео-посилання",
}


def _settings_text(shop: ShopSettings | None) -> str:
    def _v(val: str | None) -> str:
        return val if val else "<i>не вказано</i>"

    theme = (shop.theme_name if shop else None) or "default"
    _on = lambda val: "✅" if val else "❌"
    return (
        f"⚙️ <b>Налаштування магазину</b>\n\n"
        f"🏪 Назва на сайті: <b>{_v(shop.shop_title if shop else None)}</b>\n"
        f"📞 Телефон: {_v(shop.phone if shop else None)}\n"
        f"📞 Телефон 2: {_v(shop.phone2 if shop else None)}\n"
        f"📲 Viber: {_v(shop.viber_url if shop else None)}\n"
        f"💬 Підзаголовок: {_v(shop.subtitle if shop else None)}\n"
        f"📍 Адреса: {_v(shop.address if shop else None)}\n"
        f"✈️ Telegram: {_v(shop.telegram_url if shop else None)}\n"
        f"📸 Instagram: {_v(shop.instagram_url if shop else None)}\n"
        f"📢 Telegram канал: {_v(shop.telegram_channel_id if shop else None)}\n"
        f"🔗 Username каналу: {_v(shop.telegram_channel_username if shop else None)}\n"
        f"🖼 Логотип: {'✅ є' if (shop and shop.logo_url) else '<i>немає</i>'}\n"
        f"📢 Бігучий рядок: {_v(shop.promo_text if shop else None)}\n"
        f"🌄 Задній фон: {'✅ є' if (shop and shop.background_image_url) else '<i>немає</i>'}\n"
        f"🔛 Промо-бар: {_on(shop.show_promo_bar if shop is not None else True)}\n"
        f"🌐 Перемикач мови: {_on(shop.show_lang_switch if shop is not None else True)}\n"
        f"🖼 Банер: {_on(shop.show_banner if shop is not None else True)}\n"
        f"👁 Фон: {_on(shop.show_background_image if shop is not None else True)}\n"
        f"📣 Автопост: {_on(shop.autopost_enabled if shop is not None else False)}\n"
        f"🎬 Відео у пості: {_on(shop.autopost_with_video_enabled if shop is not None else False)}\n"
        f"🎨 Тема: {THEMES.get(theme, theme)}\n\n"
        f"Натисніть кнопку, щоб змінити:"
    )


def _settings_overview_kb(shop: ShopSettings | None = None) -> InlineKeyboardMarkup:
    def _bi(val: bool) -> str:
        return "✅" if val else "❌"
    sp  = shop.show_promo_bar        if shop is not None else True
    sl  = shop.show_lang_switch      if shop is not None else True
    sb  = shop.show_banner           if shop is not None else True
    sbg = shop.show_background_image if shop is not None else True
    ap  = shop.autopost_enabled      if shop is not None else False
    apv = shop.autopost_with_video_enabled if shop is not None else False
    has_bg = bool(shop and shop.background_image_url)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏪 Назва магазину",               callback_data="cms:set:shop_title")],
            [InlineKeyboardButton(text="📞 Телефон",                      callback_data="cms:set:phone")],
            [InlineKeyboardButton(text="📞 Телефон 2",                    callback_data="cms:set:phone2")],
            [InlineKeyboardButton(text="📲 Viber",                        callback_data="cms:set:viber_url")],
            [InlineKeyboardButton(text="💬 Підзаголовок",                 callback_data="cms:set:subtitle")],
            [InlineKeyboardButton(text="📍 Адреса",                       callback_data="cms:set:address")],
            [InlineKeyboardButton(text="✈️ Telegram",                     callback_data="cms:set:telegram_url")],
            [InlineKeyboardButton(text="📸 Instagram",                    callback_data="cms:set:instagram_url")],
            [InlineKeyboardButton(text="📢 Telegram канал",               callback_data="cms:set:telegram_channel_id")],
            [InlineKeyboardButton(text="🔗 Username каналу",              callback_data="cms:set:telegram_channel_username")],
            [InlineKeyboardButton(text="🖼 Логотип",                      callback_data="cms:set:logo")],
            [InlineKeyboardButton(text="🎨 Тема сайту",                   callback_data="cms:set:theme")],
            [InlineKeyboardButton(text="📢 Бігучий рядок",                callback_data="cms:set:promo_text")],
            [InlineKeyboardButton(
                text=f"🌄 {'✅ ' if has_bg else ''}Задній фон сайту",
                callback_data="cms:set:background_image",
            )],
            [InlineKeyboardButton(text=f"{_bi(sp)} Промо-бар",           callback_data="cms:toggle:show_promo_bar")],
            [InlineKeyboardButton(text=f"{_bi(sl)} Перемикач мови",      callback_data="cms:toggle:show_lang_switch")],
            [InlineKeyboardButton(text=f"{_bi(sb)} Банер",               callback_data="cms:toggle:show_banner")],
            [InlineKeyboardButton(
                text=f"{_bi(sbg)} {'Приховати фон' if sbg else 'Показувати фон'}",
                callback_data="cms:toggle:show_background_image",
            )],
            [InlineKeyboardButton(text=f"{_bi(ap)} Автопост у канал",    callback_data="cms:toggle:autopost_enabled")],
            [InlineKeyboardButton(text=f"{_bi(apv)} Відео-посилання у пості", callback_data="cms:toggle:autopost_with_video_enabled")],
        ]
    )


def _cancel_input_kb(field: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="🗑 Очистити",  callback_data=f"cms:clr:{field}"),
            InlineKeyboardButton(text="❌ Скасувати", callback_data="cms:set:cancel"),
        ]]
    )


async def _save_settings_field(field: str, value: str | None) -> ShopSettings:
    attr = FIELD_ATTR.get(field, field)
    async with AsyncSessionLocal() as session:
        shop = await session.get(ShopSettings, 1)
        if shop is None:
            shop = ShopSettings(id=1)
            session.add(shop)
        setattr(shop, attr, value)
        await session.commit()
        await session.refresh(shop)
        return shop


# ── Orders helpers ─────────────────────────────────────────────────────────────

ORDER_STATUS_LABELS: dict[str, str] = {
    "new":         "🆕 Нові",
    "in_progress": "🔄 В роботі",
    "done":        "✅ Виконані",
}
_ORDER_NEXT_STATUS: dict[str, str] = {"new": "in_progress", "in_progress": "done"}
_ORDER_BTN_LABEL:   dict[str, str] = {"new": "✅ В роботу",  "in_progress": "✅ Виконано"}


def _order_card(order: Order) -> str:
    import json as _json
    dt = order.created_at.strftime("%d.%m %H:%M") if order.created_at else "?"
    try:
        items = _json.loads(order.items_json or "[]")
        parts = [f"{i.get('name', '?')} × {i.get('qty', 1)}" for i in items[:3]]
        items_str = ", ".join(parts)
        if len(items) > 3:
            items_str += f" (+{len(items) - 3})"
    except Exception:
        items_str = "—"
    city_part = f" · 🏙 {order.customer_city}" if order.customer_city else ""
    comment_part = f"\n💬 {order.comment}" if order.comment else ""
    return (
        f"<b>#{order.id}</b> · {dt}\n"
        f"👤 {order.customer_name} · 📞 {order.customer_phone}{city_part}\n"
        f"📦 {items_str} · 💰 {int(order.total):,} грн{comment_part}"
    )


def _order_list_text(orders: list, status: str) -> str:
    label = ORDER_STATUS_LABELS.get(status, status)
    if not orders:
        return f"📋 <b>{label}</b>\n\nЗамовлень немає."
    parts = [f"📋 <b>{label}</b> ({len(orders)})"]
    for order in orders:
        parts.append("")
        parts.append(_order_card(order))
    if len(orders) >= 10:
        parts.append("\n<i>Показано перші 10</i>")
    return "\n".join(parts)


def _order_list_kb(orders: list, status: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    ns = _ORDER_NEXT_STATUS.get(status)
    lbl = _ORDER_BTN_LABEL.get(status)
    if ns and lbl:
        for order in orders:
            rows.append([InlineKeyboardButton(
                text=f"{lbl} #{order.id}",
                callback_data=f"cms:ord:status:{order.id}:{ns}",
            )])
    rows.append([InlineKeyboardButton(text="← Зведення", callback_data="cms:ord:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _order_counts() -> tuple[int, int, int]:
    async with AsyncSessionLocal() as session:
        new_cnt = await session.scalar(
            select(func.count(Order.id)).where(Order.status == "new")
        ) or 0
        ip_cnt = await session.scalar(
            select(func.count(Order.id)).where(Order.status == "in_progress")
        ) or 0
        done_cnt = await session.scalar(
            select(func.count(Order.id)).where(Order.status == "done")
        ) or 0
    return new_cnt, ip_cnt, done_cnt


def _order_summary_text(new: int, ip: int, done: int) -> str:
    return (
        f"📊 <b>Замовлення</b>\n\n"
        f"🆕 Нові: <b>{new}</b>\n"
        f"🔄 В роботі: <b>{ip}</b>\n"
        f"✅ Виконані: <b>{done}</b>"
    )


def _order_summary_kb(new: int, ip: int, done: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"🆕 Нові ({new})",       callback_data="cms:ord:list:new"),
        InlineKeyboardButton(text=f"🔄 В роботі ({ip})",   callback_data="cms:ord:list:in_progress"),
        InlineKeyboardButton(text=f"✅ Виконані ({done})",  callback_data="cms:ord:list:done"),
    ]])


# ── Stats helpers ─────────────────────────────────────────────────────────────

async def _site_stats() -> dict[str, dict[str, int]]:
    """Return event counts per period: today / 7 days / 30 days."""
    now = datetime.now(timezone.utc)
    result: dict[str, dict[str, int]] = {}
    async with AsyncSessionLocal() as session:
        for key, days in [("today", 1), ("week", 7), ("month", 30)]:
            since = now - timedelta(days=days)
            rows = (
                await session.execute(
                    select(SiteEvent.event_type, func.count(SiteEvent.id).label("cnt"))
                    .where(SiteEvent.created_at >= since)
                    .group_by(SiteEvent.event_type)
                )
            ).all()
            result[key] = {et: cnt for et, cnt in rows}
    return result


def _stats_text(stats: dict[str, dict[str, int]], product_count: int) -> str:
    def _c(period: str, et: str) -> int:
        return stats.get(period, {}).get(et, 0)

    lines = [f"📈 <b>Статистика сайту</b>\n", f"\n📦 Товари: <b>{product_count}</b>\n"]
    for label, key in [("Сьогодні", "today"), ("7 днів", "week"), ("30 днів", "month")]:
        lines.append(f"<b>{label}:</b>")
        lines.append(f"  👁 Відвідувань сайту: {_c(key, 'site_view')}")
        lines.append(f"  🔍 Переглядів товарів: {_c(key, 'product_view')}")
        lines.append(f"  🛒 Додано в корзину: {_c(key, 'add_to_cart')}")
        lines.append(f"  📦 Замовлень: {_c(key, 'order')}")
        lines.append("")
    return "\n".join(lines).rstrip()


# ── Product list helpers ──────────────────────────────────────────────────────

PROD_PAGE_SIZE = 10

_PROD_EDIT_PROMPTS: dict[str, str] = {
    "name":            "✏️ Введіть нову назву/модель товару:",
    "description":     "📝 Введіть короткий опис товару (або «-» щоб очистити):",
    "brand":           "🏢 Введіть новий бренд (або «-» щоб очистити):",
    "category":        "📂 Введіть нову категорію (або «-» щоб очистити):",
    "group_name":      "📁 Введіть нову групу (або «-» щоб очистити):",
    "price":           "💰 Введіть нову ціну (наприклад: 150):",
    "price_usd":       "💵 Введіть ціну в доларах (наприклад: 4500) або «-» щоб очистити:",
    "old_price":       "🏷 Введіть стару ціну (або «-» щоб очистити):",
    "specs":           "📋 Введіть нові характеристики (або «-» щоб очистити):",
    "seo_title":       "📝 SEO Title — назва у браузері та пошукових системах.\nВведіть або «-» щоб очистити:",
    "seo_description": "📄 SEO Description — опис для пошукових систем.\nВведіть або «-» щоб очистити:",
    "seo_keywords":    "🔑 SEO Keywords — ключові слова через кому.\nВведіть або «-» щоб очистити:",
    "video_url":       "🎬 Введіть посилання на відео-огляд (YouTube / Instagram / TikTok / mp4) або «-» щоб очистити:",
    "video_caption":   "📝 Введіть короткий підпис до відео або «-» щоб очистити:",
}
_PROD_EDIT_VALID: frozenset[str] = frozenset(_PROD_EDIT_PROMPTS) | {"image"}

BADGE_OPTIONS: list[str | None] = [
    None, "🔥 Акція", "🏆 Топ продаж", "🆕 Новинка", "💰 Супер ціна", "⚡ Хіт",
]
_BADGE_LABELS: list[str] = [
    "❌ Без плашки", "🔥 Акція", "🏆 Топ продаж", "🆕 Новинка", "💰 Супер ціна", "⚡ Хіт",
]


def _pfmt(price: object) -> str:
    try:
        return f"{float(price):,.0f}".replace(",", "\u00a0")
    except Exception:
        return str(price)


async def _prod_page_data(page: int) -> tuple[list, int, int]:
    async with AsyncSessionLocal() as session:
        total: int = await session.scalar(select(func.count(Product.id))) or 0
        total_pages = max(1, (total + PROD_PAGE_SIZE - 1) // PROD_PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))
        prods = list(await session.scalars(
            select(Product)
            .order_by(Product.id.desc())
            .offset(page * PROD_PAGE_SIZE)
            .limit(PROD_PAGE_SIZE)
        ))
    return prods, page, total_pages


def _prod_row_btn(p: "Product") -> str:
    brand = f"{p.brand} " if p.brand else ""
    flag = "✅" if p.is_available else "❌"
    return f"#{p.id} · {brand}{p.name} · {_pfmt(p.price)} грн · {flag}"


def _prod_list_text_header(page: int, total_pages: int, count: int, shop_title: str) -> str:
    if count == 0:
        return (
            f"📦 <b>{shop_title}</b> — Товари\n\n"
            "<i>Товарів поки немає. Додайте перший!</i>"
        )
    return f"📦 <b>{shop_title}</b> — Товари\nСторінка {page + 1} / {total_pages} · Показано {count}"


def _prod_list_kb(prods: list, page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for p in prods:
        rows.append([InlineKeyboardButton(
            text=_prod_row_btn(p),
            callback_data=f"cms:pv:{p.id}:{page}",
        )])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"cms:pl:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="cms:noop"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"cms:pl:{page + 1}"))
    rows.append(nav)
    rows.append([
        InlineKeyboardButton(text="🔍 Пошук",  callback_data="cms:psearch"),
        InlineKeyboardButton(text="➕ Додати", callback_data="cms:prod:add"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _prod_card_text(p: "Product") -> str:
    def _v(val: object) -> str:
        s = str(val) if val is not None else ""
        return s if s else "<i>—</i>"

    lines = [f"<b>#{p.id} · {p.name}</b>", ""]
    lines.append(f"📁 Група:      {_v(p.group_name)}")
    lines.append(f"📂 Категорія:  {_v(p.category)}")
    lines.append(f"🏢 Бренд:      {_v(p.brand)}")
    lines.append(f"💰 Ціна:       <b>{_pfmt(p.price)} грн</b>")
    if getattr(p, "price_usd", None):
        lines.append(f"💵 Ціна USD:   <b>${_pfmt(p.price_usd)}</b>")
    if p.old_price:
        lines.append(f"🏷 Стара ціна: {_pfmt(p.old_price)} грн")
    if p.specs:
        lines.append(f"\n📋 <b>Характеристики:</b>\n{p.specs}")
    if getattr(p, "video_url", None):
        lines.append(f"\n🎬 Відео: {p.video_url}")
    if getattr(p, "video_caption", None):
        lines.append(f"📝 Підпис відео: {p.video_caption}")
    lines.append("")
    lines.append(f"👁 Статус: {'✅ В наявності' if p.is_available else '❌ Прихований'}")
    lines.append(f"🖼 Фото:   {'✅ є' if p.image_url else '<i>немає</i>'}")
    desc_val = p.description
    if desc_val:
        preview = desc_val[:200] + ("…" if len(desc_val) > 200 else "")
        lines.append(f"📝 Опис:   {preview}")
    else:
        lines.append("📝 Опис:   <i>—</i>")
    badge_val = getattr(p, "badge", None)
    lines.append(f"⭐ Плашка: {badge_val if badge_val else '<i>—</i>'}")
    seo_val = getattr(p, "seo_title", None)
    lines.append(f"🔍 SEO:    {'✅' if seo_val else '<i>—</i>'}")
    return "\n".join(lines)


def _prod_card_kb(p: "Product", page: int = 0, site_url: str = "") -> InlineKeyboardMarkup:
    toggle_text = "👁 Приховати" if p.is_available else "👁 Показати"
    pid = p.id
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="✏️ Назва/модель",   callback_data=f"cms:pe:{pid}:name:{page}"),
            InlineKeyboardButton(text="🏢 Бренд",          callback_data=f"cms:pe:{pid}:brand:{page}"),
        ],
        [
            InlineKeyboardButton(text="📂 Категорія",      callback_data=f"cms:pe:{pid}:category:{page}"),
            InlineKeyboardButton(text="📁 Група",          callback_data=f"cms:pe:{pid}:group_name:{page}"),
        ],
        [
            InlineKeyboardButton(text="💰 Ціна",           callback_data=f"cms:pe:{pid}:price:{page}"),
            InlineKeyboardButton(text="💵 Ціна USD",       callback_data=f"cms:pe:{pid}:price_usd:{page}"),
        ],
        [
            InlineKeyboardButton(text="🏷 Стара ціна",     callback_data=f"cms:pe:{pid}:old_price:{page}"),
        ],
        [
            InlineKeyboardButton(text="📝 Опис",           callback_data=f"cms:pe:{pid}:description:{page}"),
            InlineKeyboardButton(text="📋 Характеристики", callback_data=f"cms:pe:{pid}:specs:{page}"),
        ],
        [
            InlineKeyboardButton(text="🔄 Перепарсити хар-ки", callback_data=f"cms:reparse:{pid}:{page}"),
        ],
        [
            InlineKeyboardButton(text="🖼 Фото товару",     callback_data=f"cms:pgallery:{pid}:{page}"),
            InlineKeyboardButton(text="⭐ Плашка",         callback_data=f"cms:bview:{pid}:{page}"),
        ],
        [
            InlineKeyboardButton(text="🔍 SEO товару",     callback_data=f"cms:seo:{pid}:{page}"),
        ],
        [
            InlineKeyboardButton(text="🎬 Відео-огляд",    callback_data=f"cms:pe:{pid}:video_url:{page}"),
            InlineKeyboardButton(text="📝 Підпис відео",   callback_data=f"cms:pe:{pid}:video_caption:{page}"),
        ],
        [
            InlineKeyboardButton(text=toggle_text,         callback_data=f"cms:ptog:{pid}:{page}"),
            InlineKeyboardButton(text="🗑 Видалити товар", callback_data=f"cms:pdc:{pid}:{page}"),
        ],
    ]
    if site_url:
        rows.append([InlineKeyboardButton(text="🌐 Відкрити на сайті", url=site_url)])
    rows.append([InlineKeyboardButton(text="← Список", callback_data=f"cms:pl:{page}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _badge_picker_kb(prod_id: int, page: int, current: str | None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for idx, (val, label) in enumerate(zip(BADGE_OPTIONS, _BADGE_LABELS)):
        mark = "✅ " if val == current else ""
        rows.append([InlineKeyboardButton(
            text=mark + label,
            callback_data=f"cms:bset:{prod_id}:{page}:{idx}",
        )])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data=f"cms:pv:{prod_id}:{page}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _seo_kb(prod_id: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 SEO Title",       callback_data=f"cms:pe:{prod_id}:seo_title:{page}")],
        [InlineKeyboardButton(text="📄 SEO Description", callback_data=f"cms:pe:{prod_id}:seo_description:{page}")],
        [InlineKeyboardButton(text="🔑 Keywords",        callback_data=f"cms:pe:{prod_id}:seo_keywords:{page}")],
        [InlineKeyboardButton(text="← Назад",            callback_data=f"cms:pv:{prod_id}:{page}")],
    ])


def _groups_kb(groups: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=g, callback_data=f"cms:group:pick:{i}")]
        for i, g in enumerate(groups)
    ]
    rows.append([
        InlineKeyboardButton(text="✏️ Нова група", callback_data="cms:group:new"),
        InlineKeyboardButton(text="⏭ Пропустити", callback_data="cms:group:skip"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _categories_kb(cats: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=c, callback_data=f"cms:cat:pick:{i}")]
        for i, c in enumerate(cats)
    ]
    rows.append([InlineKeyboardButton(text="✏️ Нова категорія", callback_data="cms:cat:new")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _brands_kb(brands: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=b, callback_data=f"cms:brand:pick:{i}")]
        for i, b in enumerate(brands)
    ]
    rows.append([
        InlineKeyboardButton(text="✏️ Новий бренд", callback_data="cms:brand:new"),
        InlineKeyboardButton(text="⏭ Пропустити", callback_data="cms:brand:skip"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _skip_kb(field: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⏭ Пропустити", callback_data=f"cms:skip:{field}")]]
    )


def _specs_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Готово",     callback_data="cms:done:specs"),
            InlineKeyboardButton(text="⏭ Пропустити", callback_data="cms:skip:specs"),
        ]]
    )


def _clean_description(text: str | None) -> str | None:
    """Normalize pasted supplier description text.

    - Replaces ">" (used as item separator on supplier sites) with newline
    - Collapses multiple blank lines
    - Strips leading/trailing whitespace per line
    """
    if not text:
        return text
    # Replace ">" separator with newline
    text = text.replace(">", "\n")
    # Normalize each line (collapse multiple spaces)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    # Remove blank lines
    lines = [ln for ln in lines if ln]
    return "\n".join(lines) if lines else None


def _parse_specs_text(text: str | None) -> list[tuple[str, str]]:
    """Parse supplier-copied specs text into ordered (name, value) pairs.

    Supported formats (auto-detected, can be mixed):

      Single-line:
        "Назва: Значення"
        "Назва=Значення"
        "Назва - Значення"     (space-dash-space)
        "Назва – Значення"     (em dash)
        "Назва > Значення"
        "К1 > В1 > К2 > В2"   (alternating pairs)

      Two-line (name with trailing colon, value on next line):
        "Номінальний об'єм:"
        "50 л"

    Lines that are headers ("Характеристики", "📋 Характеристики", etc.)
    or cannot be parsed as a name/value pair are silently skipped.
    """
    import re

    result: list[tuple[str, str]] = []
    if not text:
        return result

    # Pre-process: strip blank lines, strip leading bullets
    raw_lines = [ln.strip() for ln in text.splitlines()]
    lines: list[str] = []
    for ln in raw_lines:
        ln = re.sub(r'^[•·*]\s+', '', ln)
        ln = re.sub(r'^[-–]\s+', '', ln)
        ln = ln.strip()
        if ln:
            lines.append(ln)

    def _is_header(s: str) -> bool:
        """True if line is a section header to skip (e.g. 'Характеристики')."""
        normalized = re.sub(r'[^\w]', '', s.lower())  # letters/digits only
        return normalized in {"характеристики", "specifications", "specs", "опистовару", "opistovar"}

    def _looks_like_name_line(s: str) -> bool:
        """True if line looks like a spec name/value line."""
        return ("=" in s or ":" in s or ">" in s or " - " in s or " – " in s)

    i = 0
    while i < len(lines):
        line = lines[i]

        # Skip section headers
        if _is_header(line) or _is_header(line.rstrip(":")):
            i += 1
            continue

        if ">" in line:
            # Alternating key-value: "К1 > В1 > К2 > В2"
            parts = [p.strip() for p in line.split(">") if p.strip()]
            for j in range(0, len(parts) - 1, 2):
                n, v = parts[j].strip(), parts[j + 1].strip()
                if n and v:
                    result.append((n, v))
            i += 1

        elif "=" in line:
            n, _, v = line.partition("=")
            n, v = n.strip(), v.strip()
            if n and v:
                result.append((n, v))
            i += 1

        elif ":" in line:
            n, _, v = line.partition(":")
            n, v = n.strip(), v.strip()
            if n and v:
                # Inline value: "Назва: Значення"
                result.append((n, v))
                i += 1
            elif n and not v:
                # Trailing colon only: look ahead for value on the next line
                if i + 1 < len(lines) and not _looks_like_name_line(lines[i + 1]):
                    result.append((n, lines[i + 1].strip()))
                    i += 2  # consume both the name line and the value line
                else:
                    i += 1  # name with no value — skip
            else:
                i += 1

        elif " – " in line:   # em dash
            n, _, v = line.partition(" – ")
            n, v = n.strip(), v.strip()
            if n and v:
                result.append((n, v))
            i += 1

        elif " - " in line:   # regular dash with spaces
            n, _, v = line.partition(" - ")
            n, v = n.strip(), v.strip()
            if n and v:
                result.append((n, v))
            i += 1

        else:
            # No recognisable separator.
            # If the next line also has no separator and is not a header,
            # treat the pair as (name, value) — handles plain alternating format:
            #   "Номінальний об'єм"  ← name (no colon)
            #   "50 л"               ← value
            next_idx = i + 1
            next_is_plain_value = (
                next_idx < len(lines)
                and not _looks_like_name_line(lines[next_idx])
                and not _is_header(lines[next_idx])
                and not _is_header(lines[next_idx].rstrip(":"))
            )
            if next_is_plain_value:
                result.append((line, lines[next_idx].strip()))
                i += 2
            else:
                i += 1  # stray line with no pair — skip

    return result


def _specs_text_from_list(pairs: list[tuple[str, str]]) -> str | None:
    """Serialise parsed spec pairs back to "Name: Value" text for Product.specs."""
    if not pairs:
        return None
    return "\n".join(f"{n}: {v}" for n, v in pairs)


def _detect_video_source(url: str | None) -> str | None:
    if not url:
        return None
    lower = url.lower()
    if "youtube.com" in lower or "youtu.be" in lower:
        return "youtube"
    if "instagram.com" in lower:
        return "instagram"
    if "tiktok.com" in lower:
        return "tiktok"
    if lower.endswith(".mp4") or ".mp4?" in lower:
        return "mp4"
    return "external"


def _channel_target(shop: ShopSettings | None) -> str | None:
    if shop is None:
        return None
    return shop.telegram_channel_id or (
        f"@{shop.telegram_channel_username.lstrip('@')}"
        if shop.telegram_channel_username
        else None
    )


def _channel_post_link(shop: ShopSettings | None, post_id: int | None) -> str | None:
    if shop is None or post_id is None or not shop.telegram_channel_username:
        return None
    return f"https://t.me/{shop.telegram_channel_username.lstrip('@')}/{post_id}"


async def _autopost_product_to_channel(bot: Bot, product_id: int) -> int | None:
    async with AsyncSessionLocal() as session:
        shop = await session.get(ShopSettings, 1)
        product = await session.get(Product, product_id)
        if shop is None or product is None or not shop.autopost_enabled:
            return None
        target = _channel_target(shop)
        if not target:
            return None

        price_text = f"{_pfmt(product.price)} грн"
        price_usd_text = f"\nЦіна USD: <b>${_pfmt(product.price_usd)}</b>" if product.price_usd else ""
        old_price_text = f"\nСтара ціна: {_pfmt(product.old_price)} грн" if product.old_price else ""
        specs_list = _parse_specs_text(product.specs)
        specs_preview = "\n".join(
            f"• {html.escape(name)}: {html.escape(value)}"
            for name, value in specs_list[:6]
        )
        if specs_preview:
            specs_preview = f"\n\nХарактеристики:\n{specs_preview}"
        video_link = ""
        if product.video_url and shop.autopost_with_video_enabled:
            video_link = f"\n\nВідео огляд: {product.video_url}"

        product_url = _site_url_for_product(product.id)
        message = (
            f"🚘 <b>{html.escape(product.brand + ' ' if product.brand else '')}{html.escape(product.name)}</b>\n"
            f"Ціна: <b>{price_text}</b>{price_usd_text}{old_price_text}"
            f"{specs_preview}"
            f"{video_link}"
            f"\n\nДетальніше: {product_url or 'сайт буде доступний після деплою'}"
        )

        sent = None
        if product.image_url:
            sent = await bot.send_photo(
                target,
                photo=product.image_url,
                caption=message[:1024],
                parse_mode="HTML",
            )
        else:
            sent = await bot.send_message(target, message, parse_mode="HTML")

        product.telegram_channel_post_id = sent.message_id if sent else None
        await session.commit()
        return product.telegram_channel_post_id


def _specs_list_text(items: list) -> str:
    lines = "\n".join(f"• {it}" for it in items)
    return f"Поточні характеристики:\n{lines}\n\nДодайте ще або натисніть кнопку:"


def _site_url_for_product(product_id: int) -> str:
    from app.config import settings as app_settings
    base = (app_settings.site_url or "").rstrip("/")
    return f"{base}/product/{product_id}" if base else ""


# ── FSM states ─────────────────────────────────────────────────────────────────

class CmsProductPhotos(StatesGroup):
    waiting = State()  # waiting for a photo to add to existing product


class CmsAddProduct(StatesGroup):
    group          = State()
    group_input    = State()
    category       = State()
    category_input = State()
    brand          = State()
    brand_input    = State()
    name           = State()
    description    = State()
    specs          = State()
    price          = State()
    price_usd      = State()
    old_price      = State()
    photos         = State()  # multi-photo collection (up to 5)


class CmsSettings(StatesGroup):
    shop_title       = State()
    phone            = State()
    phone2           = State()
    viber_url        = State()
    subtitle         = State()
    address          = State()
    telegram_url     = State()
    instagram_url    = State()
    telegram_channel_id = State()
    telegram_channel_username = State()
    logo             = State()
    promo_text       = State()
    background_image = State()


class CmsEditProduct(StatesGroup):
    edit_field = State()
    edit_image = State()


class CmsProductSearch(StatesGroup):
    query = State()


class CmsAdmins(StatesGroup):
    add_id = State()  # waiting for numeric Telegram ID input


class CmsFilters(StatesGroup):
    category_select = State()  # browsing categories
    spec_list       = State()  # viewing/toggling specs for a selected category


class CmsEmojiNav(StatesGroup):
    overview    = State()  # main Групи/Категорії menu
    group_list  = State()  # browsing product groups
    group_input = State()  # waiting for new emoji for a group
    cat_list    = State()  # browsing product categories
    cat_input   = State()  # waiting for new emoji for a category


# ── /cancel ────────────────────────────────────────────────────────────────────

@router.message(StateFilter(CmsAddProduct, CmsSettings, CmsEditProduct, CmsProductSearch, CmsProductPhotos, CmsFilters, CmsEmojiNav), Command("cancel"))
async def cms_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Скасовано.", reply_markup=main_menu())


# ── 📦 Товари (paginated) ──────────────────────────────────────────────────────

@router.message(F.text == BTN_CMS_PRODUCTS)
async def cms_products(message: Message, state: FSMContext) -> None:
    await state.clear()
    shop = await _get_shop()
    prods, page, total_pages = await _prod_page_data(0)
    await message.answer(
        _prod_list_text_header(page, total_pages, len(prods), shop.shop_title or "Магазин"),
        parse_mode="HTML",
        reply_markup=_prod_list_kb(prods, page, total_pages),
    )


@router.callback_query(F.data.startswith("cms:pl:"))
async def cms_prod_page(cb: CallbackQuery, state: FSMContext) -> None:
    try:
        page = int(cb.data[len("cms:pl:"):])
    except ValueError:
        await cb.answer()
        return
    shop = await _get_shop()
    prods, page, total_pages = await _prod_page_data(page)
    await cb.message.edit_text(
        _prod_list_text_header(page, total_pages, len(prods), shop.shop_title or "Магазин"),
        parse_mode="HTML",
        reply_markup=_prod_list_kb(prods, page, total_pages),
    )
    await cb.answer()


@router.callback_query(F.data == "cms:noop")
async def cms_noop(cb: CallbackQuery) -> None:
    await cb.answer()


@router.callback_query(F.data.startswith("cms:pv:"))
async def cms_prod_view(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    try:
        prod_id = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 0
    except (ValueError, IndexError):
        await cb.answer()
        return
    async with AsyncSessionLocal() as session:
        product = await session.get(Product, prod_id)
    if product is None:
        await cb.answer("Товар не знайдено", show_alert=True)
        return
    site_url = _site_url_for_product(product.id)
    await cb.message.edit_text(
        _prod_card_text(product),
        parse_mode="HTML",
        reply_markup=_prod_card_kb(product, page, site_url),
    )
    await cb.answer()


# ── Product: edit ──────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("cms:pe:"))
async def cms_prod_edit_start(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    try:
        prod_id = int(parts[2])
        field = parts[3]
        page = int(parts[4]) if len(parts) > 4 else 0
    except (ValueError, IndexError):
        await cb.answer("Помилка", show_alert=True)
        return
    if field not in _PROD_EDIT_VALID:
        await cb.answer("Невідоме поле", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        product = await session.get(Product, prod_id)
    if product is None:
        await cb.answer("Товар не знайдено", show_alert=True)
        return
    await state.update_data(edit_prod_id=prod_id, edit_prod_page=page)
    if field == "image":
        await state.set_state(CmsEditProduct.edit_image)
        await cb.message.answer(
            "🖼 Надішліть нове фото або URL зображення. «-» щоб очистити.\n"
            "<i>/cancel для скасування</i>",
            parse_mode="HTML",
        )
    else:
        await state.update_data(edit_prod_field=field)
        await state.set_state(CmsEditProduct.edit_field)
        prompt = _PROD_EDIT_PROMPTS.get(field, f"Введіть нове значення для «{field}»:")
        await cb.message.answer(
            prompt + "\n<i>/cancel для скасування</i>",
            parse_mode="HTML",
        )
    await cb.answer()


@router.message(StateFilter(CmsEditProduct.edit_field))
async def cms_prod_edit_field_input(message: Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    data = await state.get_data()
    prod_id: int = data["edit_prod_id"]
    field: str = data["edit_prod_field"]
    page: int = data.get("edit_prod_page", 0)

    async with AsyncSessionLocal() as session:
        product = await session.get(Product, prod_id)
        if product is None:
            await state.clear()
            await message.answer("Товар не знайдено.")
            return
        clear = (val == "-")
        if field == "name":
            if not val or clear:
                await message.answer("Назва не може бути порожньою або «-». Введіть ще раз:")
                return
            product.name = val
        elif field in ("price", "price_usd", "old_price"):
            if clear:
                if field == "old_price":
                    product.old_price = None
                elif field == "price_usd":
                    product.price_usd = None
                else:
                    await message.answer("Ціна не може бути порожньою.")
                    return
            else:
                raw = val.replace(",", ".")
                try:
                    v = Decimal(raw)
                    if v < 0:
                        raise ValueError
                except (InvalidOperation, ValueError):
                    await message.answer("Некоректна ціна. Введіть число:")
                    return
                if field == "price":
                    product.price = v
                elif field == "price_usd":
                    product.price_usd = v
                else:
                    product.old_price = v
        elif field == "specs":
            # Rebuild ProductSpec rows
            await session.execute(
                delete(ProductSpec).where(ProductSpec.product_id == prod_id)
            )
            if clear:
                product.specs = None
            else:
                specs_list = _parse_specs_text(val)
                # Normalise stored text to "Name: Value" format
                product.specs = _specs_text_from_list(specs_list) or val
                for spec_name, spec_value in specs_list:
                    session.add(ProductSpec(product_id=prod_id, name=spec_name, value=spec_value))
                # Upsert CategorySpec entries
                if product.category:
                    for spec_name, _ in specs_list:
                        existing = await session.scalar(
                            select(CategorySpec).where(
                                CategorySpec.category == product.category,
                                CategorySpec.name == spec_name,
                            )
                        )
                        if existing is None:
                            session.add(CategorySpec(category=product.category, name=spec_name))
        elif field == "video_url":
            product.video_url = None if clear else val
            product.video_source_type = None if clear else _detect_video_source(val)
        elif field == "video_caption":
            product.video_caption = None if clear else val
        else:
            if field == "description" and not clear:
                setattr(product, field, _clean_description(val))
            else:
                setattr(product, field, None if clear else val)
        await session.commit()
        await session.refresh(product)
        fresh = product

    await state.clear()
    site_url = _site_url_for_product(fresh.id)
    await message.answer(
        "✅ Збережено\n\n" + _prod_card_text(fresh),
        parse_mode="HTML",
        reply_markup=_prod_card_kb(fresh, page, site_url),
    )


@router.message(StateFilter(CmsEditProduct.edit_image), F.photo)
async def cms_prod_edit_photo(message: Message, state: FSMContext) -> None:
    from app.config import settings as app_settings
    if not _is_cloudinary_configured():
        await message.answer(
            "📷 Cloudinary не налаштований.\n"
            "Надішліть URL зображення або введіть «-» щоб очистити:",
        )
        return
    data = await state.get_data()
    prod_id: int = data["edit_prod_id"]
    page: int = data.get("edit_prod_page", 0)
    photo = message.photo[-1]
    folder = f"{app_settings.cloudinary_folder}/products"
    url = await _download_and_upload(message.bot, photo.file_id, folder=folder, kind="product")
    if not url:
        await message.answer(
            "⚠️ Не вдалось завантажити фото. Спробуйте URL або введіть «-» щоб очистити:",
        )
        return
    async with AsyncSessionLocal() as session:
        product = await session.get(Product, prod_id)
        if product is None:
            await state.clear()
            return
        product.image_url = url
        await session.commit()
        await session.refresh(product)
        fresh = product
    await state.clear()
    site_url = _site_url_for_product(fresh.id)
    await message.answer(
        "✅ Фото оновлено\n\n" + _prod_card_text(fresh),
        parse_mode="HTML",
        reply_markup=_prod_card_kb(fresh, page, site_url),
    )


@router.message(StateFilter(CmsEditProduct.edit_image))
async def cms_prod_edit_image_url(message: Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    data = await state.get_data()
    prod_id: int = data["edit_prod_id"]
    page: int = data.get("edit_prod_page", 0)
    async with AsyncSessionLocal() as session:
        product = await session.get(Product, prod_id)
        if product is None:
            await state.clear()
            return
        product.image_url = None if val == "-" else val or None
        await session.commit()
        await session.refresh(product)
        fresh = product
    await state.clear()
    site_url = _site_url_for_product(fresh.id)
    await message.answer(
        "✅ Фото оновлено\n\n" + _prod_card_text(fresh),
        parse_mode="HTML",
        reply_markup=_prod_card_kb(fresh, page, site_url),
    )


# ── Product: toggle availability ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("cms:ptog:"))
async def cms_prod_toggle(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    try:
        prod_id = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 0
    except (ValueError, IndexError):
        await cb.answer()
        return
    async with AsyncSessionLocal() as session:
        product = await session.get(Product, prod_id)
        if product is None:
            await cb.answer("Товар не знайдено", show_alert=True)
            return
        product.is_available = not product.is_available
        await session.commit()
        await session.refresh(product)
        fresh = product
    site_url = _site_url_for_product(fresh.id)
    await cb.message.edit_text(
        _prod_card_text(fresh),
        parse_mode="HTML",
        reply_markup=_prod_card_kb(fresh, page, site_url),
    )
    await cb.answer("✅ Показано" if fresh.is_available else "❌ Приховано")


# ── Product: delete ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("cms:pdc:"))
async def cms_prod_del_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    try:
        prod_id = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 0
    except (ValueError, IndexError):
        await cb.answer()
        return
    async with AsyncSessionLocal() as session:
        product = await session.get(Product, prod_id)
    if product is None:
        await cb.answer("Товар не знайдено", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Так, видалити", callback_data=f"cms:pdo:{prod_id}:{page}"),
        InlineKeyboardButton(text="❌ Скасувати",     callback_data=f"cms:pv:{prod_id}:{page}"),
    ]])
    await cb.message.edit_text(
        f"🗑 Видалити товар <b>#{product.id} · {product.name}</b>?\n\n"
        f"<i>Товар буде видалено назавжди.</i>",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await cb.answer()


@router.callback_query(F.data.startswith("cms:pdo:"))
async def cms_prod_del_do(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    try:
        prod_id = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 0
    except (ValueError, IndexError):
        await cb.answer()
        return
    async with AsyncSessionLocal() as session:
        product = await session.get(Product, prod_id)
        if product is None:
            await cb.answer("Товар не знайдено", show_alert=True)
            return
        prod_name = product.name
        await session.delete(product)
        await session.commit()
    shop = await _get_shop()
    prods, page, total_pages = await _prod_page_data(page)
    await cb.message.edit_text(
        f"🗑 Товар <b>{prod_name}</b> видалено.\n\n"
        + _prod_list_text_header(page, total_pages, len(prods), shop.shop_title or "Магазин"),
        parse_mode="HTML",
        reply_markup=_prod_list_kb(prods, page, total_pages),
    )
    await cb.answer("✅ Видалено")


# ── Product: badge picker ──────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("cms:bview:"))
async def cms_prod_badge_show(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    try:
        prod_id = int(parts[2])
        page = int(parts[3])
    except (ValueError, IndexError):
        await cb.answer()
        return
    async with AsyncSessionLocal() as session:
        product = await session.get(Product, prod_id)
    if product is None:
        await cb.answer("Товар не знайдено", show_alert=True)
        return
    await cb.message.edit_text(
        f"⭐ <b>Плашка товару #{product.id}</b>\n\n"
        f"Поточна: <b>{product.badge or '—'}</b>\n\nВиберіть плашку:",
        parse_mode="HTML",
        reply_markup=_badge_picker_kb(prod_id, page, product.badge),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("cms:bset:"))
async def cms_prod_badge_set(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    try:
        prod_id = int(parts[2])
        page = int(parts[3])
        idx = int(parts[4])
    except (ValueError, IndexError):
        await cb.answer("Помилка", show_alert=True)
        return
    if not (0 <= idx < len(BADGE_OPTIONS)):
        await cb.answer("Невідома плашка", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        product = await session.get(Product, prod_id)
        if product is None:
            await cb.answer("Товар не знайдено", show_alert=True)
            return
        product.badge = BADGE_OPTIONS[idx]
        await session.commit()
        await session.refresh(product)
        fresh = product
    site_url = _site_url_for_product(fresh.id)
    await cb.message.edit_text(
        _prod_card_text(fresh),
        parse_mode="HTML",
        reply_markup=_prod_card_kb(fresh, page, site_url),
    )
    lbl = BADGE_OPTIONS[idx] or "видалено"
    await cb.answer(f"✅ Плашка: {lbl}")


# ── Product: SEO ────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("cms:seo:"))
async def cms_prod_seo_show(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    try:
        prod_id = int(parts[2])
        page = int(parts[3])
    except (ValueError, IndexError):
        await cb.answer()
        return
    async with AsyncSessionLocal() as session:
        product = await session.get(Product, prod_id)
    if product is None:
        await cb.answer("Товар не знайдено", show_alert=True)
        return

    def _v(val: object) -> str:
        return str(val) if val else "<i>—</i>"

    fallback = f"{product.brand} {product.name}" if product.brand else product.name
    shop = await _get_shop()
    await cb.message.edit_text(
        f"🔍 <b>SEO товару #{product.id}</b>\n\n"
        f"📝 Title:       {_v(product.seo_title)}\n"
        f"📄 Description: {_v(product.seo_description)}\n"
        f"🔑 Keywords:    {_v(product.seo_keywords)}\n\n"
        f"<i>Якщо SEO не заповнено — fallback: «{fallback} — {shop.shop_title or 'Магазин'}»</i>",
        parse_mode="HTML",
        reply_markup=_seo_kb(prod_id, page),
    )
    await cb.answer()


# ── Product: reparse specs (rebuild ProductSpec from Product.specs text) ───────

@router.callback_query(F.data.startswith("cms:reparse:"))
async def cms_prod_reparse(cb: CallbackQuery, state: FSMContext) -> None:
    """Re-parse Product.specs with the current parser and rebuild ProductSpec rows.

    Useful for products created before the multi-format parser was introduced
    (e.g. specs stored as "K1 > V1 > K2 > V2" which the old parser ignored).
    """
    await cb.answer()
    parts = cb.data.split(":")
    try:
        prod_id = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 0
    except (IndexError, ValueError):
        return

    async with AsyncSessionLocal() as session:
        product = await session.get(Product, prod_id)
        if product is None:
            await cb.message.answer("Товар не знайдено.")
            return

        # ── Snapshot BEFORE ─────────────────────────────────────────────────
        old_specs_text = product.specs or ""
        old_prod_spec_rows = list((await session.scalars(
            select(ProductSpec).where(ProductSpec.product_id == prod_id).order_by(ProductSpec.id)
        )).all())
        old_count = len(old_prod_spec_rows)
        old_list_preview = "\n".join(
            f"  • {r.name}: {r.value}" for r in old_prod_spec_rows
        ) or "  <i>(немає)</i>"

        # ── Clean description ────────────────────────────────────────────────
        if product.description:
            product.description = _clean_description(product.description)

        # ── Re-parse specs ───────────────────────────────────────────────────
        specs_list = _parse_specs_text(old_specs_text)
        new_specs_text = _specs_text_from_list(specs_list)

        # Delete ALL old ProductSpec rows for this product
        await session.execute(
            delete(ProductSpec).where(ProductSpec.product_id == prod_id)
        )

        # Insert fresh rows
        for spec_name, spec_value in specs_list:
            session.add(ProductSpec(
                product_id=prod_id,
                name=spec_name,
                value=spec_value,
            ))

        # Upsert CategorySpec (discover new filterable specs)
        if product.category and specs_list:
            for spec_name, _ in specs_list:
                existing = await session.scalar(
                    select(CategorySpec).where(
                        CategorySpec.category == product.category,
                        CategorySpec.name == spec_name,
                    )
                )
                if existing is None:
                    session.add(CategorySpec(
                        category=product.category,
                        name=spec_name,
                    ))

        # Update Product.specs to normalised "K: V" text
        if new_specs_text:
            product.specs = new_specs_text

        await session.commit()
        await session.refresh(product)

    # ── Build diagnostic report ──────────────────────────────────────────────
    new_list_preview = "\n".join(
        f"  • {n}: {v}" for n, v in specs_list
    ) or "  <i>(нічого не розпізнано)</i>"

    report = (
        f"🔄 <b>Перепарсинг характеристик товару #{prod_id}</b>\n\n"
        f"<b>БУЛО ({old_count} рядків у ProductSpec):</b>\n{old_list_preview}\n\n"
        f"<b>Product.specs (сирий текст):</b>\n<code>{old_specs_text[:300] or '—'}</code>\n\n"
        f"<b>СТАЛО ({len(specs_list)} пар):</b>\n{new_list_preview}\n\n"
        f"<b>Product.specs (нормалізовано):</b>\n<code>{new_specs_text or '—'}</code>"
    )

    site_url = _site_url_for_product(prod_id)
    await cb.message.answer(
        report,
        parse_mode="HTML",
        reply_markup=_prod_card_kb(product, page, site_url),
    )


# ── Product: search ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "cms:psearch")
async def cms_prod_search_start(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CmsProductSearch.query)
    await cb.message.answer(
        "🔍 <b>Пошук товарів</b>\n\n"
        "Введіть ID (або #ID), назву, бренд або категорію:\n"
        "<i>/cancel для скасування</i>",
        parse_mode="HTML",
    )
    await cb.answer()


@router.message(StateFilter(CmsProductSearch.query))
async def cms_prod_search_input(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip()
    if not query:
        await message.answer("Введіть пошуковий запит:")
        return
    raw = query.lstrip("#")
    async with AsyncSessionLocal() as session:
        if raw.isdigit():
            results = list(await session.scalars(
                select(Product).where(Product.id == int(raw)).limit(20)
            ))
        else:
            q = f"%{query}%"
            results = list(await session.scalars(
                select(Product)
                .where(or_(
                    Product.name.ilike(q),
                    Product.brand.ilike(q),
                    Product.category.ilike(q),
                    Product.group_name.ilike(q),
                ))
                .order_by(Product.id.desc())
                .limit(20)
            ))
    await state.clear()
    if not results:
        await message.answer(
            f"🔍 За запитом «{query}» нічого не знайдено.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="← Список товарів", callback_data="cms:pl:0"),
            ]]),
        )
        return
    rows = [[InlineKeyboardButton(
        text=_prod_row_btn(p),
        callback_data=f"cms:pv:{p.id}:0",
    )] for p in results]
    rows.append([InlineKeyboardButton(text="← Список товарів", callback_data="cms:pl:0")])
    await message.answer(
        f"🔍 Знайдено за «{query}»: {len(results)} товар(ів)",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


# ── 🌐 Мій сайт ────────────────────────────────────────────────────────────────

@router.message(F.text == BTN_CMS_SITE)
async def cms_site(message: Message, state: FSMContext) -> None:
    from app.config import settings as app_settings
    site_url = (app_settings.site_url or "").rstrip("/") or "<не задано>"
    await message.answer(
        f"🌐 <b>Ваш сайт:</b>\n<code>{site_url}</code>\n\n"
        f"<i>Налаштуйте SITE_URL у змінних середовища Railway.</i>",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


# ── 📈 Статистика ──────────────────────────────────────────────────────────────

@router.message(F.text == BTN_CMS_STATS)
async def cms_stats(message: Message, state: FSMContext) -> None:
    stats = await _site_stats()
    async with AsyncSessionLocal() as session:
        product_count: int = await session.scalar(select(func.count(Product.id))) or 0
    await message.answer(
        _stats_text(stats, product_count),
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


# ── 📊 Замовлення ──────────────────────────────────────────────────────────────

@router.message(F.text == BTN_CMS_ORDERS)
async def cms_orders(message: Message, state: FSMContext) -> None:
    new, ip, done = await _order_counts()
    await message.answer(
        _order_summary_text(new, ip, done),
        parse_mode="HTML",
        reply_markup=_order_summary_kb(new, ip, done),
    )


@router.callback_query(F.data.startswith("cms:ord:list:"))
async def cms_orders_list(cb: CallbackQuery, state: FSMContext) -> None:
    status = cb.data[len("cms:ord:list:"):]
    if status not in ORDER_STATUS_LABELS:
        await cb.answer("Невідомий статус", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        orders = list(await session.scalars(
            select(Order)
            .where(Order.status == status)
            .order_by(Order.id.desc())
            .limit(10)
        ))
    await cb.message.edit_text(
        _order_list_text(orders, status),
        parse_mode="HTML",
        reply_markup=_order_list_kb(orders, status),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("cms:ord:status:"))
async def cms_ord_set_status(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    if len(parts) != 5:
        await cb.answer("Помилка", show_alert=True)
        return
    try:
        order_id = int(parts[3])
    except ValueError:
        await cb.answer("Помилка", show_alert=True)
        return
    new_status = parts[4]
    if new_status not in ("in_progress", "done"):
        await cb.answer("Невідомий статус", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        order = await session.get(Order, order_id)
        if order is None:
            await cb.answer("Замовлення не знайдено", show_alert=True)
            return
        old_status = order.status
        order.status = new_status
        await session.commit()
    async with AsyncSessionLocal() as session:
        orders = list(await session.scalars(
            select(Order)
            .where(Order.status == new_status)
            .order_by(Order.id.desc())
            .limit(10)
        ))
    await cb.message.edit_text(
        _order_list_text(orders, new_status),
        parse_mode="HTML",
        reply_markup=_order_list_kb(orders, new_status),
    )
    await cb.answer("✅ Статус змінено")


@router.callback_query(F.data == "cms:ord:back")
async def cms_ord_back(cb: CallbackQuery, state: FSMContext) -> None:
    new, ip, done = await _order_counts()
    await cb.message.edit_text(
        _order_summary_text(new, ip, done),
        parse_mode="HTML",
        reply_markup=_order_summary_kb(new, ip, done),
    )
    await cb.answer()


# ── ⚙️ Налаштування ───────────────────────────────────────────────────────────

@router.message(F.text == BTN_CMS_SETTINGS)
async def cms_settings(message: Message, state: FSMContext) -> None:
    await state.clear()
    shop = await _get_shop()
    await message.answer(
        _settings_text(shop),
        parse_mode="HTML",
        reply_markup=_settings_overview_kb(shop),
    )


@router.callback_query(F.data.startswith("cms:set:"))
async def cms_settings_start_edit(cb: CallbackQuery, state: FSMContext) -> None:
    field = cb.data[len("cms:set:"):]

    if field == "cancel":
        await state.clear()
        shop = await _get_shop()
        await cb.message.answer(
            _settings_text(shop),
            parse_mode="HTML",
            reply_markup=_settings_overview_kb(shop),
        )
        await cb.answer()
        return

    if field == "theme":
        shop = await _get_shop()
        theme = (shop.theme_name if shop else None) or "default"
        await cb.message.edit_text(
            f"🎨 <b>Тема сайту</b>\n\nПоточна: {THEMES.get(theme, theme)}\nОберіть:",
            parse_mode="HTML",
            reply_markup=_themes_kb(theme),
        )
        await cb.answer()
        return

    if field not in VALID_SETTINGS_FIELDS:
        await cb.answer("Невідома дія", show_alert=True)
        return

    await state.set_state(getattr(CmsSettings, field))
    await cb.message.answer(
        SETTINGS_PROMPTS[field],
        parse_mode="HTML",
        reply_markup=_cancel_input_kb(field),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("cms:theme:"))
async def cms_set_theme(cb: CallbackQuery, state: FSMContext) -> None:
    theme_key = cb.data.split(":", 2)[2]
    if theme_key not in VALID_THEMES:
        await cb.answer("Невідома тема", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        shop = await session.get(ShopSettings, 1)
        if shop is None:
            shop = ShopSettings(id=1, theme_name=theme_key)
            session.add(shop)
        else:
            shop.theme_name = theme_key
        await session.commit()
        await session.refresh(shop)
    await cb.message.edit_text(
        _settings_text(shop),
        parse_mode="HTML",
        reply_markup=_settings_overview_kb(shop),
    )
    theme_label = THEMES.get(theme_key, theme_key)
    await cb.answer(f"✅ Тему сайту змінено: {theme_label}", show_alert=False)


@router.callback_query(F.data.startswith("cms:toggle:"))
async def cms_toggle_setting(cb: CallbackQuery) -> None:
    """Toggle a boolean ShopSettings column (show_promo_bar, show_lang_switch, show_banner)."""
    field = cb.data[len("cms:toggle:"):]
    if field not in TOGGLE_FIELDS:
        await cb.answer("Невідома дія", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        shop = await session.get(ShopSettings, 1)
        if shop is None:
            shop = ShopSettings(id=1)
            session.add(shop)
        default_current = field not in {"autopost_enabled", "autopost_with_video_enabled"}
        new_val = not getattr(shop, field, default_current)
        setattr(shop, field, new_val)
        await session.commit()
        await session.refresh(shop)
    label = TOGGLE_LABELS.get(field, field)
    icon = "✅" if new_val else "❌"
    state_str = "увімкнено" if new_val else "вимкнено"
    await cb.message.edit_text(
        _settings_text(shop),
        parse_mode="HTML",
        reply_markup=_settings_overview_kb(shop),
    )
    await cb.answer(f"{icon} {label}: {state_str}")


@router.callback_query(F.data.startswith("cms:clr:"))
async def cms_settings_clear_field(cb: CallbackQuery, state: FSMContext) -> None:
    field = cb.data[len("cms:clr:"):]
    if field not in VALID_SETTINGS_FIELDS:
        await cb.answer("Невідома дія", show_alert=True)
        return
    await state.clear()
    shop = await _save_settings_field(field, None)
    await cb.message.answer(
        _settings_text(shop),
        parse_mode="HTML",
        reply_markup=_settings_overview_kb(shop),
    )
    await cb.answer("🗑 Очищено")


@router.message(StateFilter(
    CmsSettings.shop_title, CmsSettings.phone, CmsSettings.phone2,
    CmsSettings.viber_url, CmsSettings.subtitle, CmsSettings.address,
    CmsSettings.telegram_url, CmsSettings.instagram_url,
    CmsSettings.telegram_channel_id, CmsSettings.telegram_channel_username,
    CmsSettings.promo_text,
))
async def cms_settings_text_input(message: Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    if not val:
        await message.answer("Значення не може бути порожнім. Спробуйте ще раз або скасуйте:")
        return
    current = await state.get_state()
    field = current.split(":")[-1] if current else ""
    if field in {"telegram_channel_id", "telegram_channel_username"} and val == "-":
        shop = await _save_settings_field(field, None)
        await state.clear()
        await message.answer(
            _settings_text(shop),
            parse_mode="HTML",
            reply_markup=_settings_overview_kb(shop),
        )
        return
    if field in URL_SETTINGS_FIELDS:
        if not (val.startswith("https://") or val.startswith("http://")):
            await message.answer(
                "❌ Некоректний URL. Має починатись з <code>https://</code>",
                parse_mode="HTML",
                reply_markup=_cancel_input_kb(field),
            )
            return
    shop = await _save_settings_field(field, val)
    await state.clear()
    await message.answer(
        _settings_text(shop),
        parse_mode="HTML",
        reply_markup=_settings_overview_kb(shop),
    )


@router.message(StateFilter(CmsSettings.logo), F.photo)
async def cms_logo_photo(message: Message, state: FSMContext) -> None:
    from app.config import settings as app_settings
    if not _is_cloudinary_configured():
        await message.answer(
            "📷 Cloudinary не налаштований.\nНадішліть URL посилання на логотип або натисніть «Скасувати»:",
            reply_markup=_cancel_input_kb("logo"),
        )
        return
    photo = message.photo[-1]
    folder = f"{app_settings.cloudinary_folder}/logos"
    url = await _download_and_upload(message.bot, photo.file_id, folder=folder, kind="logo")
    if not url:
        await message.answer("⚠️ Не вдалось завантажити фото. Спробуйте URL або скасуйте:", reply_markup=_cancel_input_kb("logo"))
        return
    shop = await _save_settings_field("logo", url)
    await state.clear()
    await message.answer(_settings_text(shop), parse_mode="HTML", reply_markup=_settings_overview_kb(shop))


@router.message(StateFilter(CmsSettings.logo))
async def cms_logo_url_input(message: Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    if not val:
        await message.answer("Введіть URL або надішліть фото:")
        return
    if not (val.startswith("https://") or val.startswith("http://")):
        await message.answer(
            "❌ URL має починатись з <code>https://</code> або <code>http://</code>",
            parse_mode="HTML",
            reply_markup=_cancel_input_kb("logo"),
        )
        return
    shop = await _save_settings_field("logo", val)
    await state.clear()
    await message.answer(_settings_text(shop), parse_mode="HTML", reply_markup=_settings_overview_kb(shop))


# ── FSM: background image ─────────────────────────────────────────────────────

@router.message(StateFilter(CmsSettings.background_image), F.photo)
async def cms_bg_photo(message: Message, state: FSMContext) -> None:
    from app.config import settings as app_settings
    if not _is_cloudinary_configured():
        await message.answer(
            "📷 Cloudinary не налаштований.\nНадішліть URL посилання на зображення або натисніть «Скасувати»:",
            reply_markup=_cancel_input_kb("background_image"),
        )
        return
    photo = message.photo[-1]
    folder = f"{app_settings.cloudinary_folder}/backgrounds"
    url = await _download_and_upload(message.bot, photo.file_id, folder=folder, kind="background")
    if not url:
        await message.answer("⚠️ Не вдалось завантажити фото. Спробуйте URL або скасуйте:", reply_markup=_cancel_input_kb("background_image"))
        return
    shop = await _save_settings_field("background_image", url)
    await state.clear()
    await message.answer(_settings_text(shop), parse_mode="HTML", reply_markup=_settings_overview_kb(shop))


@router.message(StateFilter(CmsSettings.background_image))
async def cms_bg_url_input(message: Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    if not val:
        await message.answer("Введіть URL або надішліть фото:")
        return
    if not (val.startswith("https://") or val.startswith("http://")):
        await message.answer(
            "❌ URL має починатись з <code>https://</code> або <code>http://</code>",
            parse_mode="HTML",
            reply_markup=_cancel_input_kb("background_image"),
        )
        return
    shop = await _save_settings_field("background_image", val)
    await state.clear()
    await message.answer(_settings_text(shop), parse_mode="HTML", reply_markup=_settings_overview_kb(shop))


# ── FSM: add product ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "cms:prod:add")
async def cms_start_add(cb: CallbackQuery, state: FSMContext) -> None:
    async with AsyncSessionLocal() as session:
        groups = list(await session.scalars(
            select(Product.group_name)
            .where(Product.group_name.isnot(None))
            .distinct()
            .order_by(Product.group_name)
        ))
    await state.update_data(possible_groups=groups)
    await state.set_state(CmsAddProduct.group)
    await cb.message.answer(
        "📦 <b>Новий товар</b>\n\n"
        "Крок 1 — Виберіть або введіть групу товарів:\n"
        "<i>(відправ /cancel для скасування)</i>",
        parse_mode="HTML",
        reply_markup=_groups_kb(groups),
    )
    await cb.answer()


async def _go_to_category(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    selected_group = data.get("group_name")
    async with AsyncSessionLocal() as session:
        q = select(Product.category).where(Product.category.isnot(None))
        if selected_group:
            q = q.where(Product.group_name == selected_group)
        cats = list(await session.scalars(q.distinct().order_by(Product.category)))
    await state.update_data(possible_categories=cats)
    await state.set_state(CmsAddProduct.category)
    await msg.answer("Крок 2 — Виберіть або введіть категорію:", reply_markup=_categories_kb(cats))


async def _go_to_brand(msg: Message, state: FSMContext) -> None:
    async with AsyncSessionLocal() as session:
        brands = list(await session.scalars(
            select(Product.brand)
            .where(Product.brand.isnot(None))
            .distinct().order_by(Product.brand)
        ))
    await state.update_data(possible_brands=brands)
    await state.set_state(CmsAddProduct.brand)
    await msg.answer("Крок 3 — Виберіть або введіть бренд:", reply_markup=_brands_kb(brands))


@router.callback_query(F.data.startswith("cms:group:pick:"), StateFilter(CmsAddProduct.group))
async def cms_group_pick(cb: CallbackQuery, state: FSMContext) -> None:
    try:
        idx = int(cb.data.rsplit(":", 1)[-1])
    except (ValueError, IndexError):
        await cb.answer()
        return
    data = await state.get_data()
    groups = data.get("possible_groups", [])
    group = groups[idx] if 0 <= idx < len(groups) else None
    await state.update_data(group_name=group)
    await _go_to_category(cb.message, state)
    await cb.answer()


@router.callback_query(F.data == "cms:group:new", StateFilter(CmsAddProduct.group))
async def cms_group_new(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CmsAddProduct.group_input)
    await cb.message.answer("Введіть нову групу товарів:")
    await cb.answer()


@router.callback_query(F.data == "cms:group:skip", StateFilter(CmsAddProduct.group))
async def cms_group_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(group_name=None)
    await _go_to_category(cb.message, state)
    await cb.answer()


@router.message(StateFilter(CmsAddProduct.group))
async def cms_group_typed(message: Message, state: FSMContext) -> None:
    group = (message.text or "").strip()
    await state.update_data(group_name=group or None)
    await _go_to_category(message, state)


@router.message(StateFilter(CmsAddProduct.group_input))
async def cms_group_input(message: Message, state: FSMContext) -> None:
    group = (message.text or "").strip()
    await state.update_data(group_name=group or None)
    await _go_to_category(message, state)


@router.callback_query(F.data.startswith("cms:cat:pick:"), StateFilter(CmsAddProduct.category))
async def cms_cat_pick(cb: CallbackQuery, state: FSMContext) -> None:
    try:
        idx = int(cb.data.rsplit(":", 1)[-1])
    except (ValueError, IndexError):
        await cb.answer()
        return
    data = await state.get_data()
    cats = data.get("possible_categories", [])
    cat = cats[idx] if 0 <= idx < len(cats) else None
    await state.update_data(category=cat)
    await _go_to_brand(cb.message, state)
    await cb.answer()


@router.callback_query(F.data == "cms:cat:new", StateFilter(CmsAddProduct.category))
async def cms_cat_new(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CmsAddProduct.category_input)
    await cb.message.answer("Введіть нову категорію:")
    await cb.answer()


@router.message(StateFilter(CmsAddProduct.category))
async def cms_cat_typed(message: Message, state: FSMContext) -> None:
    cat = (message.text or "").strip()
    await state.update_data(category=cat or None)
    await _go_to_brand(message, state)


@router.message(StateFilter(CmsAddProduct.category_input))
async def cms_cat_input(message: Message, state: FSMContext) -> None:
    cat = (message.text or "").strip()
    await state.update_data(category=cat or None)
    await _go_to_brand(message, state)


@router.callback_query(F.data.startswith("cms:brand:pick:"), StateFilter(CmsAddProduct.brand))
async def cms_brand_pick(cb: CallbackQuery, state: FSMContext) -> None:
    try:
        idx = int(cb.data.rsplit(":", 1)[-1])
    except (ValueError, IndexError):
        await cb.answer()
        return
    data = await state.get_data()
    brands = data.get("possible_brands", [])
    brand = brands[idx] if 0 <= idx < len(brands) else None
    await state.update_data(brand=brand)
    await state.set_state(CmsAddProduct.name)
    await cb.message.answer("Крок 4 — Введіть модель / назву товару:")
    await cb.answer()


@router.callback_query(F.data == "cms:brand:new", StateFilter(CmsAddProduct.brand))
async def cms_brand_new(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CmsAddProduct.brand_input)
    await cb.message.answer("Введіть новий бренд:")
    await cb.answer()


@router.callback_query(F.data == "cms:brand:skip", StateFilter(CmsAddProduct.brand))
async def cms_brand_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(brand=None)
    await state.set_state(CmsAddProduct.name)
    await cb.message.answer("Крок 4 — Введіть модель / назву товару:")
    await cb.answer()


@router.message(StateFilter(CmsAddProduct.brand))
async def cms_brand_typed(message: Message, state: FSMContext) -> None:
    brand = (message.text or "").strip()
    await state.update_data(brand=brand or None)
    await state.set_state(CmsAddProduct.name)
    await message.answer("Крок 4 — Введіть модель / назву товару:")


@router.message(StateFilter(CmsAddProduct.brand_input))
async def cms_brand_input(message: Message, state: FSMContext) -> None:
    brand = (message.text or "").strip()
    await state.update_data(brand=brand or None)
    await state.set_state(CmsAddProduct.name)
    await message.answer("Крок 4 — Введіть модель / назву товару:")


@router.message(StateFilter(CmsAddProduct.name))
async def cms_add_name(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Назва не може бути порожньою. Введіть назву:")
        return
    await state.update_data(name=text, specs_items=[])
    await state.set_state(CmsAddProduct.description)
    await message.answer(
        "Крок 5 — Короткий опис товару (відображається на сторінці товару):",
        reply_markup=_skip_kb("description"),
    )


@router.callback_query(F.data == "cms:skip:description", StateFilter(CmsAddProduct.description))
async def cms_skip_description(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(description=None)
    await state.set_state(CmsAddProduct.specs)
    await cb.message.answer(
        "Крок 6 — Характеристики товару:\n\nВставте всі характеристики одним повідомленням\n"
        "або вводьте по одній (наприклад: Площа: 35 м²).\n"
        "Підтримуються формати:\n"
        "  Назва: Значення\n"
        "  Назва > Значення\n"
        "  К1 > В1 > К2 > В2 > ...\n"
        "Коли закінчите — натисніть ✅ Готово.",
        reply_markup=_specs_kb(),
    )
    await cb.answer()


@router.message(StateFilter(CmsAddProduct.description))
async def cms_add_description(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    cleaned = _clean_description(raw) if raw and raw != "-" else None
    await state.update_data(description=cleaned)
    await state.set_state(CmsAddProduct.specs)
    await message.answer(
        "Крок 6 — Характеристики товару:\n\nВставте всі характеристики одним повідомленням\n"
        "або вводьте по одній (наприклад: Площа: 35 м²).\n"
        "Підтримуються формати:\n"
        "  Назва: Значення\n"
        "  Назва > Значення\n"
        "  К1 > В1 > К2 > В2 > ...\n"
        "Коли закінчите — натисніть ✅ Готово.",
        reply_markup=_specs_kb(),
    )


@router.callback_query(F.data == "cms:skip:specs", StateFilter(CmsAddProduct.specs))
async def cms_skip_specs(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(specs=None, specs_items=[])
    await state.set_state(CmsAddProduct.price)
    await cb.message.answer("Крок 7 — Ціна (наприклад: 150):")
    await cb.answer()


@router.callback_query(F.data == "cms:done:specs", StateFilter(CmsAddProduct.specs))
async def cms_done_specs(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    items: list = data.get("specs_items", [])
    specs_text = "\n".join(items) if items else None
    await state.update_data(specs=specs_text, specs_items=[])
    await state.set_state(CmsAddProduct.price)
    await cb.message.answer("Крок 7 — Ціна (наприклад: 150):")
    await cb.answer()


@router.message(StateFilter(CmsAddProduct.specs))
async def cms_add_specs(message: Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    if not val:
        await message.answer("Введіть характеристику або натисніть кнопку:", reply_markup=_specs_kb())
        return
    data = await state.get_data()
    items: list = list(data.get("specs_items", []))

    # Parse immediately — supports "K: V", "K > V", "K1 > V1 > K2 > V2", etc.
    parsed = _parse_specs_text(val)
    if parsed:
        for name, value in parsed:
            items.append(f"{name}: {value}")
        await state.update_data(specs_items=items)
        parsed_preview = "\n".join(f"  • {n}: {v}" for n, v in parsed)
        await message.answer(
            f"✅ Розпізнано {len(parsed)} характеристик:\n{parsed_preview}\n\n"
            + _specs_list_text(items),
            reply_markup=_specs_kb(),
        )
    else:
        # Could not parse as key-value — store raw and warn
        items.append(val)
        await state.update_data(specs_items=items)
        await message.answer(
            "⚠️ Не вдалось розпізнати формат. Збережено як є.\n"
            "Підтримувані формати: Назва: Значення  /  Назва > Значення\n\n"
            + _specs_list_text(items),
            reply_markup=_specs_kb(),
        )


@router.message(StateFilter(CmsAddProduct.price))
async def cms_add_price(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        price = Decimal(raw)
        if price < 0:
            raise ValueError("negative price")
    except (InvalidOperation, ValueError):
        await message.answer("Некоректна ціна. Введіть число (наприклад: 150):")
        return
    await state.update_data(price=str(price))
    await state.set_state(CmsAddProduct.price_usd)
    await message.answer("Крок 8 — Ціна в доларах (необов'язково):", reply_markup=_skip_kb("price_usd"))


@router.callback_query(F.data == "cms:skip:price_usd", StateFilter(CmsAddProduct.price_usd))
async def cms_skip_price_usd(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(price_usd=None)
    await state.set_state(CmsAddProduct.old_price)
    await cb.message.answer("Крок 9 — Стара ціна (для відображення знижки):", reply_markup=_skip_kb("old_price"))
    await cb.answer()


@router.message(StateFilter(CmsAddProduct.price_usd))
async def cms_add_price_usd(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        price_usd = Decimal(raw)
        if price_usd < 0:
            raise ValueError("negative")
    except (InvalidOperation, ValueError):
        await message.answer("Некоректна ціна. Введіть число або натисніть «Пропустити»:")
        return
    await state.update_data(price_usd=str(price_usd))
    await state.set_state(CmsAddProduct.old_price)
    await message.answer("Крок 9 — Стара ціна (для відображення знижки):", reply_markup=_skip_kb("old_price"))


@router.callback_query(F.data == "cms:skip:old_price", StateFilter(CmsAddProduct.old_price))
async def cms_skip_old_price(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(old_price=None)
    await _enter_photos_step(cb.message, state)
    await cb.answer()


@router.message(StateFilter(CmsAddProduct.old_price))
async def cms_add_old_price(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        old_price = Decimal(raw)
        if old_price < 0:
            raise ValueError("negative")
    except (InvalidOperation, ValueError):
        await message.answer("Некоректна ціна. Введіть число або натисніть «Пропустити»:")
        return
    await state.update_data(old_price=str(old_price))
    await _enter_photos_step(message, state)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 9 — Multi-photo collection (up to 5 photos, add product flow)
# ─────────────────────────────────────────────────────────────────────────────

MAX_PRODUCT_PHOTOS = 5

# Per-user lock to serialize concurrent state updates during photo collection.
# Needed because Telegram sends album (media group) photos as separate concurrent
# updates — without serialization, multiple handlers race on `collected_photos`.
_photo_add_locks: dict[int, asyncio.Lock] = {}


def _get_photo_add_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _photo_add_locks:
        _photo_add_locks[user_id] = asyncio.Lock()
    return _photo_add_locks[user_id]


def _photos_progress_kb(count: int) -> InlineKeyboardMarkup:
    """Keyboard shown while collecting photos during add-product flow."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Готово",     callback_data="cms:photos:done"),
        InlineKeyboardButton(text="⏭ Пропустити", callback_data="cms:photos:skip"),
    ]])


async def _enter_photos_step(message: Message, state: FSMContext) -> None:
    await state.update_data(collected_photos=[])
    await state.set_state(CmsAddProduct.photos)
    await message.answer(
        "Крок 9 — Надішліть фото товару (до 5 штук).\n"
        "Можна надсилати по одному.\n"
        "Або натисніть «Пропустити»:",
        reply_markup=_photos_progress_kb(0),
    )


@router.callback_query(F.data == "cms:photos:skip", StateFilter(CmsAddProduct.photos))
async def cms_photos_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(collected_photos=[])
    await _do_save_product(cb.message, state)
    await cb.answer()


@router.callback_query(F.data == "cms:photos:done", StateFilter(CmsAddProduct.photos))
async def cms_photos_done(cb: CallbackQuery, state: FSMContext) -> None:
    await _do_save_product(cb.message, state)
    await cb.answer()


@router.message(StateFilter(CmsAddProduct.photos), F.photo)
async def cms_add_photo(message: Message, state: FSMContext) -> None:
    from app.config import settings as app_settings

    # ── Fast pre-check (no lock needed — just reading) ────────────────────────
    if not _is_cloudinary_configured():
        data = await state.get_data()
        collected: list[str] = data.get("collected_photos") or []
        await message.answer(
            "📷 Cloudinary не налаштований. Надішліть URL фото або натисніть «Готово»:",
            reply_markup=_photos_progress_kb(len(collected)),
        )
        return

    # ── Step 1: upload BEFORE acquiring lock (slow I/O, can run concurrently) ─
    photo = message.photo[-1]
    folder = f"{app_settings.cloudinary_folder}/products"
    try:
        url = await _download_and_upload(message.bot, photo.file_id, folder=folder, kind="product")
    except Exception:
        logger.exception("Failed to upload product photo")
        data = await state.get_data()
        collected = data.get("collected_photos") or []
        await message.answer(
            "⚠️ Не вдалося завантажити фото. Спробуйте ще раз або натисніть «Готово».",
            reply_markup=_photos_progress_kb(len(collected)),
        )
        return
    if not url:
        data = await state.get_data()
        collected = data.get("collected_photos") or []
        await message.answer(
            "⚠️ Не вдалось завантажити фото. Спробуйте ще раз або натисніть «Готово»:",
            reply_markup=_photos_progress_kb(len(collected)),
        )
        return

    # ── Step 2: critical section — read-modify-write collected_photos ─────────
    # Serialised with per-user lock to prevent race conditions when Telegram
    # sends an album (media group) as multiple near-simultaneous updates.
    lock = _get_photo_add_lock(message.from_user.id)
    async with lock:
        data = await state.get_data()
        collected = data.get("collected_photos") or []
        if len(collected) >= MAX_PRODUCT_PHOTOS:
            # Album sent more photos than the limit — silently discard extras.
            return
        collected.append(url)
        count = len(collected)
        await state.update_data(collected_photos=collected)

    # ── Step 3: progress message (outside lock) ───────────────────────────────
    if count >= MAX_PRODUCT_PHOTOS:
        await message.answer(
            f"✅ Фото додано {count}/{MAX_PRODUCT_PHOTOS} — досягнуто максимум!\n"
            "Натисніть «Готово» для збереження:",
            reply_markup=_photos_progress_kb(count),
        )
    else:
        await message.answer(
            f"✅ Фото додано {count}/{MAX_PRODUCT_PHOTOS}. Надішліть ще фото або натисніть «Готово»:",
            reply_markup=_photos_progress_kb(count),
        )


@router.message(StateFilter(CmsAddProduct.photos))
async def cms_add_photo_url(message: Message, state: FSMContext) -> None:
    """Accept a URL as photo, or treat empty/dash as 'skip all photos'."""
    if message.photo:
        await cms_add_photo(message, state)
        return

    val = (message.text or "").strip()
    if not val or val == "-":
        # Empty or dash typed → skip photos, save product now
        await _do_save_product(message, state)
        return
    if val in ("✅ Готово", "Готово", "готово"):
        await _do_save_product(message, state)
        return
    if val in ("⏭ Пропустити", "⏭️ Пропустити", "Пропустити", "пропустити"):
        await _do_save_product(message, state)
        return

    # URL provided — add with same lock-based serialisation
    lock = _get_photo_add_lock(message.from_user.id)
    async with lock:
        data = await state.get_data()
        collected: list[str] = data.get("collected_photos") or []
        if len(collected) >= MAX_PRODUCT_PHOTOS:
            await message.answer(
                f"⚠️ Вже додано максимум {MAX_PRODUCT_PHOTOS} фото. Натисніть «Готово»:",
                reply_markup=_photos_progress_kb(MAX_PRODUCT_PHOTOS),
            )
            return
        collected.append(val)
        count = len(collected)
        await state.update_data(collected_photos=collected)

    await message.answer(
        f"✅ Фото {count}/{MAX_PRODUCT_PHOTOS} додано. Надішліть ще або натисніть «Готово»:",
        reply_markup=_photos_progress_kb(count),
    )


async def _do_save_product(message: Message, state: FSMContext) -> None:
    data = await state.get_data()

    # Normalise description and specs before saving
    clean_desc = _clean_description(data.get("description"))
    specs_list = _parse_specs_text(data.get("specs"))
    clean_specs = _specs_text_from_list(specs_list)  # "К: В\nК: В\n..." or None

    logger.info("SAVE PRODUCT specs raw=%r", data.get("specs"))
    logger.info("SAVE PRODUCT specs_list=%r", specs_list)

    old_price_val = Decimal(data["old_price"]) if data.get("old_price") else None
    price_usd_val = Decimal(data["price_usd"]) if data.get("price_usd") else None
    category = data.get("category")
    photos: list[str] = data.get("collected_photos") or []

    # ── Step 1: save the Product + ProductSpec rows ───────────────────────────
    try:
        async with AsyncSessionLocal() as session:
            main_url = photos[0] if photos else None
            product = Product(
                name=data["name"],
                group_name=data.get("group_name"),
                category=category,
                brand=data.get("brand"),
                description=clean_desc,
                specs=clean_specs,
                price=Decimal(data["price"]),
                price_usd=price_usd_val,
                old_price=old_price_val,
                image_url=main_url,
                is_available=True,
            )
            session.add(product)
            await session.flush()  # generates product.id

            # Save structured specs (skip empty names/values)
            seen_spec_names: set[str] = set()
            for spec_name, spec_value in specs_list:
                if not spec_name or not spec_value:
                    continue
                session.add(ProductSpec(product_id=product.id, name=spec_name, value=spec_value))
                seen_spec_names.add(spec_name)

            # Upsert CategorySpec entries — guard against duplicates within session
            if category:
                seen_cat_specs: set[str] = set()
                for spec_name in seen_spec_names:
                    if spec_name in seen_cat_specs:
                        continue
                    existing = await session.scalar(
                        select(CategorySpec).where(
                            CategorySpec.category == category,
                            CategorySpec.name == spec_name,
                        )
                    )
                    if existing is None:
                        session.add(CategorySpec(category=category, name=spec_name))
                    seen_cat_specs.add(spec_name)

            await session.commit()
            product_id = product.id
    except Exception:
        logger.exception("Failed to save product")
        await message.answer(
            "⚠️ Не вдалося зберегти товар. Перевірте характеристики або спробуйте без них.",
            reply_markup=main_menu(),
        )
        await state.clear()
        return

    # ── Step 2: save ProductImage rows (separate transaction, non-fatal) ──────
    if photos:
        try:
            async with AsyncSessionLocal() as session:
                for idx, url in enumerate(photos):
                    session.add(ProductImage(
                        product_id=product_id,
                        image_url=url,
                        sort_order=idx,
                        is_main=(idx == 0),
                    ))
                await session.commit()
        except Exception as exc:
            logger.warning("Could not save ProductImage rows (table may not exist yet): %s", exc)

    try:
        await _autopost_product_to_channel(message.bot, product_id)
    except Exception as exc:
        logger.warning("Could not autopost product %s to Telegram channel: %s", product_id, exc)

    await state.clear()
    group_label = f" [{data['group_name']}]" if data.get("group_name") else ""
    cat_label = f" · {data['category']}" if data.get("category") else ""
    brand_label = f" [{data['brand']}]" if data.get("brand") else ""
    old_price_label = f" (знижка з {data['old_price']} грн)" if data.get("old_price") else ""
    usd_label = f"\nЦіна USD: ${data['price_usd']}" if data.get("price_usd") else ""
    photo_label = f"\nФото: {len(photos)} шт." if photos else ""
    await message.answer(
        f"✅ Товар <b>{data['name']}</b>{brand_label} додано!{group_label}{cat_label}\n"
        f"Ціна: {data['price']} грн{usd_label}{old_price_label}{photo_label}",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# PHOTO GALLERY — manage photos of an existing product
# ─────────────────────────────────────────────────────────────────────────────

def _gallery_kb(images: list, prod_id: int, page: int) -> InlineKeyboardMarkup:
    """Build inline keyboard for the photo gallery manager."""
    rows: list[list[InlineKeyboardButton]] = []
    for pos, img in enumerate(images):
        star = "⭐ " if img.is_main else ""
        rows.append([InlineKeyboardButton(
            text=f"{star}Фото {pos + 1}",
            callback_data=f"cms:ph:view:{prod_id}:{img.id}:{page}",
        )])
    rows.append([
        InlineKeyboardButton(text="➕ Додати фото", callback_data=f"cms:ph:add:{prod_id}:{page}"),
    ])
    rows.append([
        InlineKeyboardButton(text="← Назад до товару", callback_data=f"cms:pv:{prod_id}:{page}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _photo_actions_kb(img_id: int, prod_id: int, page: int, is_main: bool, is_first: bool, is_last: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if not is_main:
        rows.append([InlineKeyboardButton(text="⭐ Зробити головним", callback_data=f"cms:ph:main:{prod_id}:{img_id}:{page}")])
    move_row = []
    if not is_first:
        move_row.append(InlineKeyboardButton(text="🔼 Вверх", callback_data=f"cms:ph:up:{prod_id}:{img_id}:{page}"))
    if not is_last:
        move_row.append(InlineKeyboardButton(text="🔽 Вниз",  callback_data=f"cms:ph:down:{prod_id}:{img_id}:{page}"))
    if move_row:
        rows.append(move_row)
    rows.append([InlineKeyboardButton(text="🗑 Видалити", callback_data=f"cms:ph:del:{prod_id}:{img_id}:{page}")])
    rows.append([InlineKeyboardButton(text="← Галерея",  callback_data=f"cms:pgallery:{prod_id}:{page}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _sync_main_image(session, prod_id: int) -> None:
    """Keep Product.image_url in sync with the is_main ProductImage (or first by sort_order)."""
    imgs = list((await session.scalars(
        select(ProductImage).where(ProductImage.product_id == prod_id).order_by(ProductImage.sort_order)
    )).all())
    product = await session.get(Product, prod_id)
    if not product:
        return
    if not imgs:
        product.image_url = None
        return
    main = next((i for i in imgs if i.is_main), imgs[0])
    product.image_url = main.image_url


@router.callback_query(F.data.startswith("cms:pgallery:"))
async def cms_prod_gallery(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    try:
        prod_id = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 0
    except (ValueError, IndexError):
        await cb.answer("Помилка", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        product = await session.get(Product, prod_id)
        if product is None:
            await cb.answer("Товар не знайдено", show_alert=True)
            return
        images = list((await session.scalars(
            select(ProductImage).where(ProductImage.product_id == prod_id).order_by(ProductImage.sort_order)
        )).all())

    count = len(images)
    if count == 0:
        text = f"<b>🖼 Фото товару #{prod_id}</b>\n\nФото відсутні."
    else:
        lines = [f"<b>🖼 Фото товару #{prod_id}</b> ({count}/5)\n"]
        for pos, img in enumerate(images):
            label = "⭐ головне" if img.is_main else f"сортування {pos + 1}"
            lines.append(f"• Фото {pos + 1} — {label}")
        text = "\n".join(lines)

    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=_gallery_kb(images, prod_id, page))
    await cb.answer()


@router.callback_query(F.data.startswith("cms:ph:view:"))
async def cms_ph_view(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    try:
        prod_id = int(parts[3])
        img_id  = int(parts[4])
        page    = int(parts[5]) if len(parts) > 5 else 0
    except (ValueError, IndexError):
        await cb.answer("Помилка", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        img = await session.get(ProductImage, img_id)
        if img is None or img.product_id != prod_id:
            await cb.answer("Фото не знайдено", show_alert=True)
            return
        images = list((await session.scalars(
            select(ProductImage).where(ProductImage.product_id == prod_id).order_by(ProductImage.sort_order)
        )).all())

    ids = [i.id for i in images]
    pos = ids.index(img_id)
    kb = _photo_actions_kb(img_id, prod_id, page, img.is_main, pos == 0, pos == len(ids) - 1)
    await cb.message.edit_text(
        f"<b>Фото {pos + 1}/{len(ids)}</b>\n{'⭐ Головне' if img.is_main else ''}\n<a href='{img.image_url}'>Переглянути</a>",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await cb.answer()


@router.callback_query(F.data.startswith("cms:ph:main:"))
async def cms_ph_set_main(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    try:
        prod_id = int(parts[3])
        img_id  = int(parts[4])
        page    = int(parts[5]) if len(parts) > 5 else 0
    except (ValueError, IndexError):
        await cb.answer("Помилка", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        imgs = list((await session.scalars(
            select(ProductImage)
            .where(ProductImage.product_id == prod_id)
            .order_by(ProductImage.sort_order)
        )).all())
        # Put the new main photo first, keep relative order of others
        target = next((i for i in imgs if i.id == img_id), None)
        if target:
            ordered = [target] + [i for i in imgs if i.id != img_id]
            for idx, img in enumerate(ordered):
                img.sort_order = idx
                img.is_main = (img.id == img_id)
        await _sync_main_image(session, prod_id)
        await session.commit()

    await cb.answer("⭐ Головне фото оновлено")
    # Refresh gallery
    cb.data = f"cms:pgallery:{prod_id}:{page}"
    await cms_prod_gallery(cb, state)


@router.callback_query(F.data.startswith("cms:ph:up:") | F.data.startswith("cms:ph:down:"))
async def cms_ph_move(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    try:
        direction = parts[2]   # "up" or "down"
        prod_id   = int(parts[3])
        img_id    = int(parts[4])
        page      = int(parts[5]) if len(parts) > 5 else 0
    except (ValueError, IndexError):
        await cb.answer("Помилка", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        imgs = list((await session.scalars(
            select(ProductImage).where(ProductImage.product_id == prod_id).order_by(ProductImage.sort_order)
        )).all())
        ids = [i.id for i in imgs]
        if img_id not in ids:
            await cb.answer("Фото не знайдено", show_alert=True)
            return
        pos = ids.index(img_id)
        if direction == "up" and pos > 0:
            imgs[pos].sort_order, imgs[pos - 1].sort_order = imgs[pos - 1].sort_order, imgs[pos].sort_order
        elif direction == "down" and pos < len(imgs) - 1:
            imgs[pos].sort_order, imgs[pos + 1].sort_order = imgs[pos + 1].sort_order, imgs[pos].sort_order
        # Re-sync main if needed
        await _sync_main_image(session, prod_id)
        await session.commit()

    await cb.answer()
    cb.data = f"cms:pgallery:{prod_id}:{page}"
    await cms_prod_gallery(cb, state)


@router.callback_query(F.data.startswith("cms:ph:del:"))
async def cms_ph_delete(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    try:
        prod_id = int(parts[3])
        img_id  = int(parts[4])
        page    = int(parts[5]) if len(parts) > 5 else 0
    except (ValueError, IndexError):
        await cb.answer("Помилка", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        img = await session.get(ProductImage, img_id)
        if img is None or img.product_id != prod_id:
            await cb.answer("Фото не знайдено", show_alert=True)
            return
        was_main = img.is_main
        await session.delete(img)
        await session.flush()
        # Re-number sort_order
        remaining = list((await session.scalars(
            select(ProductImage).where(ProductImage.product_id == prod_id).order_by(ProductImage.sort_order)
        )).all())
        for idx, r in enumerate(remaining):
            r.sort_order = idx
        # If deleted was main, promote first
        if was_main and remaining:
            remaining[0].is_main = True
        await _sync_main_image(session, prod_id)
        await session.commit()

    await cb.answer("🗑 Фото видалено")
    cb.data = f"cms:pgallery:{prod_id}:{page}"
    await cms_prod_gallery(cb, state)


@router.callback_query(F.data.startswith("cms:ph:add:"))
async def cms_ph_add_start(cb: CallbackQuery, state: FSMContext) -> None:
    parts = cb.data.split(":")
    try:
        prod_id = int(parts[3])
        page    = int(parts[4]) if len(parts) > 4 else 0
    except (ValueError, IndexError):
        await cb.answer("Помилка", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        count = (await session.scalar(
            select(func.count()).where(ProductImage.product_id == prod_id)
        )) or 0

    if count >= MAX_PRODUCT_PHOTOS:
        await cb.answer(f"Максимум {MAX_PRODUCT_PHOTOS} фото. Видаліть зайве.", show_alert=True)
        return

    await state.update_data(ph_add_prod_id=prod_id, ph_add_page=page)
    await state.set_state(CmsProductPhotos.waiting)
    await cb.message.answer(
        f"Надішліть нове фото ({count + 1}/{MAX_PRODUCT_PHOTOS}):\n<i>/cancel для скасування</i>",
        parse_mode="HTML",
    )
    await cb.answer()


@router.message(StateFilter(CmsProductPhotos.waiting), F.photo)
async def cms_ph_add_receive(message: Message, state: FSMContext) -> None:
    from app.config import settings as app_settings
    data = await state.get_data()
    prod_id: int = data["ph_add_prod_id"]
    page: int    = data.get("ph_add_page", 0)

    async with AsyncSessionLocal() as session:
        count = (await session.scalar(
            select(func.count()).where(ProductImage.product_id == prod_id)
        )) or 0

    if count >= MAX_PRODUCT_PHOTOS:
        await state.clear()
        await message.answer(f"Максимум {MAX_PRODUCT_PHOTOS} фото вже досягнуто.")
        return

    if not _is_cloudinary_configured():
        await message.answer("📷 Cloudinary не налаштований. Надішліть URL фото:")
        return

    photo = message.photo[-1]
    folder = f"{app_settings.cloudinary_folder}/products/{prod_id}"
    url = await _download_and_upload(message.bot, photo.file_id, folder=folder, kind="product")
    if not url:
        await message.answer("⚠️ Не вдалось завантажити. Спробуйте ще раз або /cancel:")
        return

    async with AsyncSessionLocal() as session:
        max_order = await session.scalar(
            select(func.max(ProductImage.sort_order)).where(ProductImage.product_id == prod_id)
        )
        new_sort_order = (max_order + 1) if max_order is not None else 0
        is_first = max_order is None
        session.add(ProductImage(
            product_id=prod_id,
            image_url=url,
            sort_order=new_sort_order,
            is_main=is_first,
        ))
        if is_first:
            product = await session.get(Product, prod_id)
            if product:
                product.image_url = url
        await session.commit()

    await state.clear()
    new_count = count + 1
    await message.answer(
        f"✅ Фото {new_count}/{MAX_PRODUCT_PHOTOS} додано!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🖼 До галереї", callback_data=f"cms:pgallery:{prod_id}:{page}"),
        ]]),
    )


# ══════════════════════════════════════════════════════════════════════════════
# ── 🧩 Фільтри — управління фільтрами каталогу по категоріях ─────────────────
# ══════════════════════════════════════════════════════════════════════════════

async def _filt_show_categories(target: Message | CallbackQuery, state: FSMContext) -> None:
    """Show inline keyboard with all product categories for filter management."""
    async with AsyncSessionLocal() as session:
        rows = list((await session.scalars(
            select(Product.category).distinct().where(Product.category.isnot(None))
        )).all())
    categories = sorted([r for r in rows if r])
    await state.update_data(filt_categories=categories)
    await state.set_state(CmsFilters.category_select)

    if not categories:
        text = "🧩 Фільтри\n\nКатегорій не знайдено. Спочатку додайте товари із заповненою категорією."
        kb = None
    else:
        text = "🧩 Фільтри — оберіть категорію:"
        buttons = [
            [InlineKeyboardButton(text=cat, callback_data=f"cms:filt:cat:{i}")]
            for i, cat in enumerate(categories)
        ]
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    msg = target if isinstance(target, Message) else target.message
    await msg.answer(text, reply_markup=kb)


async def _filt_show_specs(
    cb_or_msg: CallbackQuery | Message,
    state: FSMContext,
    category: str,
    edit: bool = False,
) -> None:
    """Show spec list for a category with ✅/❌ toggles."""
    async with AsyncSessionLocal() as session:
        spec_name_rows = list((await session.scalars(
            select(ProductSpec.name).distinct()
            .join(Product, ProductSpec.product_id == Product.id)
            .where(Product.category == category)
            .order_by(ProductSpec.name)
        )).all())
        spec_names = [s for s in spec_name_rows if s]

        cs_rows = list((await session.scalars(
            select(CategorySpec).where(CategorySpec.category == category)
        )).all())
    cs_map: dict[str, bool] = {cs.name: cs.is_filterable for cs in cs_rows}

    # Default: filterable=True for specs without a CategorySpec row yet
    specs = [(name, cs_map.get(name, True)) for name in spec_names]
    await state.update_data(filt_specs=[[s[0], s[1]] for s in specs], filt_current_cat=category)
    await state.set_state(CmsFilters.spec_list)

    if not specs:
        text = f"🧩 Фільтри «{category}»\n\nУ товарів цієї категорії ще немає характеристик (spec)."
    else:
        text = f"🧩 Фільтри «{category}»\nНатисніть характеристику, щоб увімкнути/вимкнути:"

    buttons: list[list[InlineKeyboardButton]] = []
    for i, (name, filterable) in enumerate(specs):
        icon = "✅" if filterable else "❌"
        buttons.append([InlineKeyboardButton(text=f"{icon} {name}", callback_data=f"cms:filt:toggle:{i}")])

    buttons.append([InlineKeyboardButton(text="🔄 Синхронізувати з товарами", callback_data="cms:filt:sync")])
    buttons.append([InlineKeyboardButton(text="◀ До категорій", callback_data="cms:filt:back")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    msg = cb_or_msg if isinstance(cb_or_msg, Message) else cb_or_msg.message
    if edit and not isinstance(cb_or_msg, Message):
        await cb_or_msg.message.edit_text(text, reply_markup=kb)
    else:
        await msg.answer(text, reply_markup=kb)


@router.message(F.text == BTN_CMS_FILTERS)
async def cms_filters_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _filt_show_categories(message, state)


@router.callback_query(F.data.startswith("cms:filt:cat:"))
async def cms_filt_cat_select(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    try:
        idx = int(cb.data.split(":")[-1])
    except ValueError:
        return
    data = await state.get_data()
    categories: list[str] = data.get("filt_categories") or []
    if not categories:
        # FSM state was lost (bot restart) — re-fetch from DB
        async with AsyncSessionLocal() as session:
            rows = list((await session.scalars(
                select(Product.category).distinct().where(Product.category.isnot(None))
            )).all())
        categories = sorted([r for r in rows if r])
        await state.update_data(filt_categories=categories)
    await state.set_state(CmsFilters.category_select)
    if idx >= len(categories):
        await cb.message.answer("⚠️ Категорія не знайдена. Натисніть «🧩 Фільтри» знову.")
        return
    category = categories[idx]
    await _filt_show_specs(cb, state, category)


@router.callback_query(F.data.startswith("cms:filt:toggle:"))
async def cms_filt_toggle(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    logger.info("FILTER TOGGLE callback=%s data=%s", cb.data, data)
    try:
        idx = int(cb.data.split(":")[-1])
    except ValueError:
        await cb.answer()
        return
    specs: list[list] = data.get("filt_specs") or []
    category: str = data.get("filt_current_cat") or ""

    if not category or idx >= len(specs):
        # FSM state was lost (bot restart) — show alert popup, do not proceed
        await cb.answer(
            "Оновіть меню фільтрів: натисніть «Фільтри» ще раз",
            show_alert=True,
        )
        return

    await cb.answer()

    name, current = specs[idx]
    new_val = not current

    async with AsyncSessionLocal() as session:
        cs = (await session.scalars(
            select(CategorySpec).where(
                CategorySpec.category == category,
                CategorySpec.name == name,
            )
        )).first()
        if cs is not None:
            cs.is_filterable = new_val
        else:
            session.add(CategorySpec(category=category, name=name, is_filterable=new_val))
        await session.commit()

    specs[idx] = [name, new_val]
    await state.update_data(filt_specs=specs)
    await _filt_show_specs(cb, state, category, edit=True)


@router.callback_query(F.data == "cms:filt:sync")
async def cms_filt_sync(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    category: str = data.get("filt_current_cat") or ""
    if not category:
        await cb.answer("⚠️ Сесія застаріла. Натисніть «🧩 Фільтри» знову.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        # Discover all spec names for this category from products
        all_names_rows = list((await session.scalars(
            select(ProductSpec.name).distinct()
            .join(Product, ProductSpec.product_id == Product.id)
            .where(Product.category == category)
        )).all())
        all_names = {s for s in all_names_rows if s}

        # Find which ones already have a CategorySpec row
        existing_names = {
            cs.name
            for cs in (await session.scalars(
                select(CategorySpec).where(CategorySpec.category == category)
            )).all()
        }

        new_names = all_names - existing_names
        for name in sorted(new_names):
            session.add(CategorySpec(category=category, name=name, is_filterable=True))
        await session.commit()

    await cb.answer(
        f"✅ Синхронізовано: +{len(new_names)} нових" if new_names else "✅ Все актуально",
        show_alert=bool(new_names),
    )
    await _filt_show_specs(cb, state, category, edit=True)


@router.callback_query(F.data == "cms:filt:back")
async def cms_filt_back(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await _filt_show_categories(cb, state)


def _admins_text(env_ids: frozenset[int], db_admins: list[ShopAdmin]) -> str:
    lines = ["👥 <b>Управління адміністраторами</b>\n"]
    lines.append("👑 <b>Суперадміни (env):</b>")
    for uid in sorted(env_ids):
        lines.append(f"  • <code>{uid}</code>")
    lines.append("")
    if db_admins:
        lines.append("👤 <b>Додані адміни:</b>")
        for adm in db_admins:
            who = f"@{adm.username}" if adm.username else f"<code>{adm.telegram_id}</code>"
            lines.append(f"  • {who} — <code>{adm.telegram_id}</code>")
    else:
        lines.append("👤 <b>Додані адміни:</b> <i>немає</i>")
    lines.append("\nНатисніть кнопку для керування:")
    return "\n".join(lines)


def _admins_kb(db_admins: list[ShopAdmin]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for adm in db_admins:
        who = f"@{adm.username}" if adm.username else str(adm.telegram_id)
        rows.append([
            InlineKeyboardButton(
                text=f"🗑 {who}",
                callback_data=f"cms:admin:del:{adm.telegram_id}",
            )
        ])
    rows.append([
        InlineKeyboardButton(text="➕ Додати адміна", callback_data="cms:admin:add"),
        InlineKeyboardButton(text="🔄 Оновити",       callback_data="cms:admin:list"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text == BTN_CMS_ADMINS)
async def cms_admins(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ShopAdmin).order_by(ShopAdmin.added_at))
        db_admins = list(result.scalars().all())
    from app.config import settings as app_settings
    await message.answer(
        _admins_text(app_settings.admin_ids, db_admins),
        parse_mode="HTML",
        reply_markup=_admins_kb(db_admins),
    )


@router.callback_query(F.data == "cms:admin:list")
async def cms_admins_refresh(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ShopAdmin).order_by(ShopAdmin.added_at))
        db_admins = list(result.scalars().all())
    from app.config import settings as app_settings
    await cb.message.edit_text(
        _admins_text(app_settings.admin_ids, db_admins),
        parse_mode="HTML",
        reply_markup=_admins_kb(db_admins),
    )
    await cb.answer()


@router.callback_query(F.data == "cms:admin:add")
async def cms_admins_add_start(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CmsAdmins.add_id)
    await cb.message.answer(
        "➕ <b>Додавання адміна</b>\n\n"
        "Введіть Telegram ID нового адміністратора\n"
        "(числовий ID, наприклад: <code>123456789</code>):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Скасувати", callback_data="cms:admin:cancel"),
        ]]),
    )
    await cb.answer()


@router.callback_query(F.data == "cms:admin:cancel")
async def cms_admins_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ShopAdmin).order_by(ShopAdmin.added_at))
        db_admins = list(result.scalars().all())
    from app.config import settings as app_settings
    await cb.message.answer(
        _admins_text(app_settings.admin_ids, db_admins),
        parse_mode="HTML",
        reply_markup=_admins_kb(db_admins),
    )
    await cb.answer("❌ Скасовано")


@router.message(StateFilter(CmsAdmins.add_id))
async def cms_admins_add_input(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.lstrip("-").isdigit():
        await message.answer(
            "❌ Некоректний формат. Введіть числовий Telegram ID:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="❌ Скасувати", callback_data="cms:admin:cancel"),
            ]]),
        )
        return
    new_tid = int(raw)
    from app.config import settings as app_settings
    if new_tid in app_settings.admin_ids:
        await message.answer(
            "ℹ️ Цей користувач вже є суперадміном.\n"
            "Його не потрібно додавати — доступ уже є.",
        )
        return
    async with AsyncSessionLocal() as session:
        existing = await session.execute(
            select(ShopAdmin).where(ShopAdmin.telegram_id == new_tid).limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            await message.answer("⚠️ Цей адмін вже є у списку.")
            return
        new_admin = ShopAdmin(telegram_id=new_tid)
        session.add(new_admin)
        await session.commit()
        result = await session.execute(select(ShopAdmin).order_by(ShopAdmin.added_at))
        db_admins = list(result.scalars().all())
    await state.clear()
    await message.answer(
        f"✅ Адміна <code>{new_tid}</code> додано!\n\n"
        + _admins_text(app_settings.admin_ids, db_admins),
        parse_mode="HTML",
        reply_markup=_admins_kb(db_admins),
    )


@router.callback_query(F.data.startswith("cms:admin:del:"))
async def cms_admins_del(cb: CallbackQuery, state: FSMContext) -> None:
    raw = cb.data[len("cms:admin:del:"):]
    if not raw.lstrip("-").isdigit():
        await cb.answer("Некоректний ID", show_alert=True)
        return
    del_tid = int(raw)
    from app.config import settings as app_settings
    if del_tid in app_settings.admin_ids:
        await cb.answer("🔒 Суперадмінів не можна видалити через бот.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(ShopAdmin).where(ShopAdmin.telegram_id == del_tid)
        )
        await session.commit()
        result = await session.execute(select(ShopAdmin).order_by(ShopAdmin.added_at))
        db_admins = list(result.scalars().all())
    await cb.message.edit_text(
        _admins_text(app_settings.admin_ids, db_admins),
        parse_mode="HTML",
        reply_markup=_admins_kb(db_admins),
    )
    await cb.answer(f"🗑 Адміна {del_tid} видалено")


# ══════════════════════════════════════════════════════════════════════════════
# ── 🗂 Групи / Категорії — управління emoji ───────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

# ── helpers ──────────────────────────────────────────────────────────────────

async def _nav_current_group_emoji(name: str) -> str:
    """Return emoji for a group: DB row if set, else static fallback."""
    async with AsyncSessionLocal() as session:
        row = (await session.scalars(
            select(NavGroup).where(NavGroup.name == name)
        )).first()
    if row and row.emoji:
        return row.emoji
    return _grp_emoji_fb(name)


async def _nav_current_cat_emoji(name: str) -> str:
    """Return emoji for a category: DB row if set, else static fallback."""
    async with AsyncSessionLocal() as session:
        row = (await session.scalars(
            select(NavCategory).where(NavCategory.name == name)
        )).first()
    if row and row.emoji:
        return row.emoji
    return _cat_emoji_fb(name)


async def _nav_find_group_for_cat(category: str) -> str | None:
    """Find the group_name of the first product with this category."""
    async with AsyncSessionLocal() as session:
        row = (await session.scalars(
            select(Product.group_name)
            .where(Product.category == category, Product.group_name.isnot(None))
            .limit(1)
        )).first()
    return row or None


# ── overview ─────────────────────────────────────────────────────────────────

def _nav_overview_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗂 Групи товарів",  callback_data="cms:nav:groups")],
        [InlineKeyboardButton(text="📋 Категорії",      callback_data="cms:nav:cats")],
        [InlineKeyboardButton(text="↩️ Закрити",        callback_data="cms:nav:close")],
    ])


@router.message(F.text == BTN_CMS_EMOJI)
async def cms_emoji_nav(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(CmsEmojiNav.overview)
    await message.answer(
        "🗂 <b>Групи / Категорії</b>\n\n"
        "Оберіть розділ для редагування emoji:",
        parse_mode="HTML",
        reply_markup=_nav_overview_kb(),
    )


@router.callback_query(F.data == "cms:nav:close", StateFilter(CmsEmojiNav))
async def cms_nav_close(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    try:
        await cb.message.delete()
    except Exception:
        pass


@router.callback_query(F.data == "cms:nav:menu", StateFilter(CmsEmojiNav))
async def cms_nav_back_to_menu(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(CmsEmojiNav.overview)
    await cb.message.edit_text(
        "🗂 <b>Групи / Категорії</b>\n\n"
        "Оберіть розділ для редагування emoji:",
        parse_mode="HTML",
        reply_markup=_nav_overview_kb(),
    )


# ── groups list ───────────────────────────────────────────────────────────────

async def _nav_show_groups(target: Message | CallbackQuery, state: FSMContext) -> None:
    """Display product groups with current emoji for selection."""
    async with AsyncSessionLocal() as session:
        group_names: list[str] = sorted(filter(None, (
            await session.scalars(
                select(Product.group_name).distinct().where(Product.group_name.isnot(None))
            )
        ).all()))
        nav_rows = {
            r.name: r.emoji
            for r in (await session.scalars(select(NavGroup))).all()
            if r.emoji
        }

    await state.update_data(nav_groups=group_names)
    await state.set_state(CmsEmojiNav.group_list)

    if not group_names:
        text = (
            "🗂 <b>Групи товарів</b>\n\n"
            "<i>Груп не знайдено. Спочатку додайте товари з заповненою групою.</i>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Назад", callback_data="cms:nav:menu")],
        ])
    else:
        text = "🗂 <b>Групи товарів</b>\nОберіть групу для зміни emoji:"
        buttons = []
        for i, name in enumerate(group_names):
            emo = nav_rows.get(name) or _grp_emoji_fb(name)
            buttons.append([InlineKeyboardButton(
                text=f"{emo}  {name}",
                callback_data=f"cms:nav:g:{i}",
            )])
        buttons.append([InlineKeyboardButton(text="↩️ Назад", callback_data="cms:nav:menu")])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "cms:nav:groups", StateFilter(CmsEmojiNav))
async def cms_nav_groups(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await _nav_show_groups(cb, state)


@router.callback_query(F.data.startswith("cms:nav:g:"), StateFilter(CmsEmojiNav.group_list))
async def cms_nav_group_select(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    try:
        idx = int(cb.data.split(":")[-1])
    except ValueError:
        return
    data = await state.get_data()
    groups: list[str] = data.get("nav_groups", [])
    if idx >= len(groups):
        return
    name = groups[idx]
    cur_emo = await _nav_current_group_emoji(name)
    await state.update_data(nav_editing_name=name)
    await state.set_state(CmsEmojiNav.group_input)
    await cb.message.edit_text(
        f"🗂 Група: <b>{name}</b>\n\n"
        f"Поточний emoji: {cur_emo}\n\n"
        "Надішліть новий emoji (або будь-який текст до 16 символів):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Назад до груп", callback_data="cms:nav:groups")],
        ]),
    )


@router.message(StateFilter(CmsEmojiNav.group_input), F.text)
async def cms_nav_group_emoji_input(message: Message, state: FSMContext) -> None:
    emoji_text = (message.text or "").strip()[:16]
    if not emoji_text:
        await message.answer("Порожній текст. Надішліть emoji або символ.")
        return
    data = await state.get_data()
    name: str = data.get("nav_editing_name", "")
    async with AsyncSessionLocal() as session:
        row = (await session.scalars(
            select(NavGroup).where(NavGroup.name == name)
        )).first()
        if row is None:
            session.add(NavGroup(name=name, emoji=emoji_text))
        else:
            row.emoji = emoji_text
        await session.commit()
    await message.answer(f"✅ Emoji оновлено: {emoji_text} {name}")
    await _nav_show_groups(message, state)


# ── categories list ───────────────────────────────────────────────────────────

async def _nav_show_cats(target: Message | CallbackQuery, state: FSMContext) -> None:
    """Display product categories with current emoji for selection."""
    async with AsyncSessionLocal() as session:
        cat_names: list[str] = sorted(filter(None, (
            await session.scalars(
                select(Product.category).distinct().where(Product.category.isnot(None))
            )
        ).all()))
        nav_rows = {
            r.name: r.emoji
            for r in (await session.scalars(select(NavCategory))).all()
            if r.emoji
        }

    await state.update_data(nav_cats=cat_names)
    await state.set_state(CmsEmojiNav.cat_list)

    if not cat_names:
        text = (
            "📋 <b>Категорії</b>\n\n"
            "<i>Категорій не знайдено. Спочатку додайте товари з категорією.</i>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Назад", callback_data="cms:nav:menu")],
        ])
    else:
        text = "📋 <b>Категорії</b>\nОберіть категорію для зміни emoji:"
        buttons = []
        for i, name in enumerate(cat_names):
            emo = nav_rows.get(name) or _cat_emoji_fb(name)
            buttons.append([InlineKeyboardButton(
                text=f"{emo}  {name}",
                callback_data=f"cms:nav:c:{i}",
            )])
        buttons.append([InlineKeyboardButton(text="↩️ Назад", callback_data="cms:nav:menu")])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "cms:nav:cats", StateFilter(CmsEmojiNav))
async def cms_nav_cats(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await _nav_show_cats(cb, state)


@router.callback_query(F.data.startswith("cms:nav:c:"), StateFilter(CmsEmojiNav.cat_list))
async def cms_nav_cat_select(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    try:
        idx = int(cb.data.split(":")[-1])
    except ValueError:
        return
    data = await state.get_data()
    cats: list[str] = data.get("nav_cats", [])
    if idx >= len(cats):
        return
    name = cats[idx]
    cur_emo = await _nav_current_cat_emoji(name)
    await state.update_data(nav_editing_name=name)
    await state.set_state(CmsEmojiNav.cat_input)
    await cb.message.edit_text(
        f"📋 Категорія: <b>{name}</b>\n\n"
        f"Поточний emoji: {cur_emo}\n\n"
        "Надішліть новий emoji (або будь-який текст до 16 символів):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Назад до категорій", callback_data="cms:nav:cats")],
        ]),
    )


@router.message(StateFilter(CmsEmojiNav.cat_input), F.text)
async def cms_nav_cat_emoji_input(message: Message, state: FSMContext) -> None:
    emoji_text = (message.text or "").strip()[:16]
    if not emoji_text:
        await message.answer("Порожній текст. Надішліть emoji або символ.")
        return
    data = await state.get_data()
    name: str = data.get("nav_editing_name", "")
    group_name = await _nav_find_group_for_cat(name)
    async with AsyncSessionLocal() as session:
        row = (await session.scalars(
            select(NavCategory).where(NavCategory.name == name)
        )).first()
        if row is None:
            session.add(NavCategory(name=name, emoji=emoji_text, group_name=group_name))
        else:
            row.emoji = emoji_text
            if group_name and not row.group_name:
                row.group_name = group_name
        await session.commit()
    await message.answer(f"✅ Emoji оновлено: {emoji_text} {name}")
    await _nav_show_cats(message, state)
