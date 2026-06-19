from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from app.config import settings


class AdminFilter(BaseFilter):
    """Passes only for users whose Telegram ID is in ADMIN_IDS."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return bool(event.from_user and event.from_user.id in settings.admin_ids)
