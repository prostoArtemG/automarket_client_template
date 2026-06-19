"""Middleware that clears FSM state when the user taps a main-menu reply button."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Set

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, TelegramObject

from app.bot.keyboards import (
    BTN_CMS_FILTERS,
    BTN_CMS_ORDERS,
    BTN_CMS_PRODUCTS,
    BTN_CMS_SETTINGS,
    BTN_CMS_SITE,
    BTN_CMS_STATS,
)

logger = logging.getLogger(__name__)

MENU_BUTTONS: Set[str] = {
    BTN_CMS_PRODUCTS,
    BTN_CMS_SITE,
    BTN_CMS_ORDERS,
    BTN_CMS_SETTINGS,
    BTN_CMS_STATS,
    BTN_CMS_FILTERS,
}


class MenuInterruptMiddleware(BaseMiddleware):
    """If the incoming message text matches a main-menu reply button, clear
    any active FSM state before handlers run. Also resets state on /start
    and /cancel commands."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.text:
            text = event.text.strip()
            state: FSMContext | None = data.get("state")
            if state is not None:
                if text in MENU_BUTTONS or text.startswith("/start") or text.startswith("/cancel"):
                    current = await state.get_state()
                    if current is not None:
                        await state.clear()
                        logger.debug("MenuInterrupt: cleared FSM state for %r", text)
        return await handler(event, data)
