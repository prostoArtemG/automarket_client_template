import json
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(..., alias="BOT_TOKEN")
    # Read as a plain string to avoid pydantic-settings trying to coerce a
    # comma-separated value into List[int] before we can parse it ourselves.
    admin_ids_raw: str = Field("", alias="ADMIN_IDS")
    database_url: str = Field(..., alias="DATABASE_URL")

    app_host: str = Field("0.0.0.0", alias="APP_HOST")
    app_port: int = Field(8000, alias="APP_PORT")

    # Public URL of this app — used for "Open on site" links in the bot.
    site_url: str = Field("", alias="SITE_URL")

    # Cloudinary (optional — enables photo upload via bot)
    cloudinary_cloud_name: str = Field("", alias="CLOUDINARY_CLOUD_NAME")
    cloudinary_api_key: str = Field("", alias="CLOUDINARY_API_KEY")
    cloudinary_api_secret: str = Field("", alias="CLOUDINARY_API_SECRET")
    # Base folder for all uploads; subfolders /products and /logos are appended automatically.
    cloudinary_folder: str = Field("shopplatform/default", alias="CLOUDINARY_FOLDER")

    # Initial shop identity (used only to seed ShopSettings on first run or to
    # update default values; will not overwrite customised bot settings).
    shop_title: str = Field("", alias="SHOP_TITLE")
    shop_subtitle: str = Field("", alias="SHOP_SUBTITLE")

    @property
    def admin_ids(self) -> list[int]:
        """Parse ADMIN_IDS supporting all formats:
        500134490
        500134490,123456789
        [500134490,123456789]
        """
        raw = self.admin_ids_raw.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                return [int(x) for x in json.loads(raw)]
            except (json.JSONDecodeError, ValueError):
                pass
        return [int(x.strip()) for x in raw.split(",") if x.strip()]


settings = Settings()
