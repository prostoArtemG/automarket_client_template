import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.cms import router as cms_router
from app.bot.filters import AdminFilter
from app.bot.keyboards import main_menu
from app.bot.middlewares import MenuInterruptMiddleware
from app.config import settings

logger = logging.getLogger(__name__)

# ── Public router — no AdminFilter, /start accessible to everyone ─────────────
public_router = Router(name="public")


@public_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    is_admin = await AdminFilter()(message)
    if not is_admin:
        uid = message.from_user.id if message.from_user else "?"
        await message.answer(
            f"⛔ У вас немає доступу.\n"
            f"Ваш Telegram ID: <code>{uid}</code>",
            parse_mode="HTML",
        )
        return
    await message.answer(
        "👋 Вітаю! Виберіть розділ у меню:",
        reply_markup=main_menu(),
    )


# ── Admin-only root router — global /cancel ───────────────────────────────────
router = Router(name="root")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())


@router.message(Command("cancel"))
async def global_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Скасовано.", reply_markup=main_menu())


def create_bot() -> Bot:
    return Bot(token=settings.bot_token)


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.message.middleware(MenuInterruptMiddleware())
    dp.include_router(public_router)  # first: handles /start without filter
    dp.include_router(cms_router)
    dp.include_router(router)
    return dp
