import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str = os.environ.get("DATABASE_URL", "")
    JWT_SECRET: str = os.environ.get("SESSION_SECRET", "9f3KpW2mQx8Lr1Zt6Vn0Aa7bC4dE5fG2")
    BOT_TOKEN: Optional[str] = os.environ.get("BOT_TOKEN", None)
    BOT_USERNAME: str = ""
    CUSTOMER_SERVICE_USERNAME: str = ""
    ADMIN_TELEGRAM_ID: Optional[int] = int(os.environ.get("ADMIN_TELEGRAM_ID", "0")) if os.environ.get("ADMIN_TELEGRAM_ID") else None
    TRON_ADDRESS: str = ""
    
    REDIS_URL: str = "redis://localhost:6379/0"
    
    BACKEND_URL: str = "http://127.0.0.1:8000"
    API_V1_STR: str = "/api/v1"

settings = Settings()

# Override DATABASE_URL to use asyncpg
if settings.DATABASE_URL and "postgresql://" in settings.DATABASE_URL and "asyncpg" not in settings.DATABASE_URL:
    settings.DATABASE_URL = settings.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
