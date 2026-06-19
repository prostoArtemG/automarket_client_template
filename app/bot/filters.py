from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import ShopAdmin


class AdminFilter(BaseFilter):
    """Passes for users in ADMIN_IDS (env) or in shop_admins table (DB).

    Env ADMIN_IDS are checked first (no DB round-trip for superadmins).
    """

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        if not event.from_user:
            return False
        uid = event.from_user.id
        if uid in settings.admin_ids:
            return True
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ShopAdmin.id).where(ShopAdmin.telegram_id == uid).limit(1)
            )
            return result.scalar_one_or_none() is not None
