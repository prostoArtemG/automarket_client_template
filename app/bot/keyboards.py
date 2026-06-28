from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

# ── Button labels ─────────────────────────────────────────────────────────────
BTN_CMS_PRODUCTS = "📦 Товари"
BTN_CMS_SITE     = "🌐 Мій сайт"
BTN_CMS_ORDERS   = "📊 Замовлення"
BTN_CMS_STATS    = "📈 Статистика"
BTN_CMS_FILTERS  = "🧩 Фільтри"
BTN_CMS_SETTINGS = "⚙️ Налаштування"
BTN_CMS_ADMINS   = "👥 Управління"
BTN_CMS_EMOJI    = "🗂 Групи / Категорії"
BTN_CMS_HELP     = "📘 Інструкція"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CMS_PRODUCTS)],
            [KeyboardButton(text=BTN_CMS_SITE), KeyboardButton(text=BTN_CMS_ORDERS)],
            [KeyboardButton(text=BTN_CMS_STATS), KeyboardButton(text=BTN_CMS_FILTERS)],
            [KeyboardButton(text=BTN_CMS_EMOJI)],
            [KeyboardButton(text=BTN_CMS_HELP)],
            [KeyboardButton(text=BTN_CMS_SETTINGS), KeyboardButton(text=BTN_CMS_ADMINS)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
