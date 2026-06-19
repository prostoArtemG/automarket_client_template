from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

# ── Button labels ─────────────────────────────────────────────────────────────
BTN_CMS_PRODUCTS = "📦 Товари"
BTN_CMS_SITE     = "🌐 Мій сайт"
BTN_CMS_ORDERS   = "📊 Замовлення"
BTN_CMS_STATS    = "📈 Статистика"
BTN_CMS_SETTINGS = "⚙️ Налаштування"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CMS_PRODUCTS)],
            [KeyboardButton(text=BTN_CMS_SITE), KeyboardButton(text=BTN_CMS_ORDERS)],
            [KeyboardButton(text=BTN_CMS_STATS), KeyboardButton(text=BTN_CMS_SETTINGS)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
