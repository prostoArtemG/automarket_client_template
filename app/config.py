from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(..., alias="BOT_TOKEN")
    admin_ids: List[int] = Field(default_factory=list, alias="ADMIN_IDS")
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


settings = Settings()
