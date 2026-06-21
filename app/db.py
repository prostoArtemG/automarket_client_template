from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    pass


database_url = settings.database_url
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(database_url, echo=False, future=True)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session


_DEFAULT_SHOP_TITLE = "Мій магазин"
_DEFAULT_SUBTITLE = "Побутова техніка під замовлення та в наявності"


async def init_db() -> None:
    pass  # Schema is managed by Alembic (alembic upgrade head in Procfile)


async def init_shop_settings() -> None:
    """Seed or update ShopSettings(id=1) from ENV variables.

    Rules:
    - If no row exists → create with ENV values (or hardcoded defaults).
    - If row exists but still has the default title/subtitle AND ENV provides a
      custom value → update to ENV value.
    - Never overwrite values that the client already changed via bot.
    """
    from app.models import ShopSettings  # local import avoids circular deps

    env_title = settings.shop_title.strip()
    env_subtitle = settings.shop_subtitle.strip()

    async with AsyncSessionLocal() as session:
        row = await session.get(ShopSettings, 1)

        if row is None:
            row = ShopSettings(
                id=1,
                shop_title=env_title or _DEFAULT_SHOP_TITLE,
                subtitle=env_subtitle or _DEFAULT_SUBTITLE,
            )
            session.add(row)
            await session.commit()
            return

        changed = False
        # Update title only if current value is blank or still the default
        if env_title and (not row.shop_title or row.shop_title == _DEFAULT_SHOP_TITLE):
            row.shop_title = env_title
            changed = True
        # Update subtitle only if current value is blank or still the default
        if env_subtitle and (not row.subtitle or row.subtitle == _DEFAULT_SUBTITLE):
            row.subtitle = env_subtitle
            changed = True
        if changed:
            await session.commit()


async def close_db() -> None:
    await engine.dispose()
