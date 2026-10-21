"""Application Configuration Module using Pydantic Settings."""

import json
from typing import Any
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """T-Bank application configuration settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    BOT_TOKEN: str = Field(default="dummy_token", description="Telegram Bot API Token")
    ADMIN_IDS: list[int] | str = Field(default_factory=list, description="List of administrator Telegram IDs")
    DATABASE_URL: str = Field(default="sqlite+aiosqlite:///tbank.db", description="Database connection URL")
    SAVINGS_APR: float = Field(default=0.18, description="Annual percentage rate for savings accounts")
    BROKER_FEE_PERCENT: float = Field(default=0.003, description="Broker fee percentage per trade (0.3%)")
    DEBUG: bool = Field(default=False, description="Debug mode flag")

    @field_validator("ADMIN_IDS", mode="before")
    @classmethod
    def parse_admin_ids(cls, v: Any) -> list[int]:
        """Flexible validator accepting comma-separated strings, JSON arrays, lists, or ints."""
        if v is None:
            return []
        if isinstance(v, int):
            return [v]
        if isinstance(v, str):
            clean_str = v.strip()
            if not clean_str:
                return []
            if clean_str.startswith("[") and clean_str.endswith("]"):
                try:
                    parsed = json.loads(clean_str)
                    if isinstance(parsed, list):
                        return [int(item) for item in parsed]
                except (json.JSONDecodeError, ValueError):
                    pass
            # Split by comma
            result = []
            for item in clean_str.split(","):
                item_str = item.strip()
                if item_str:
                    result.append(int(item_str))
            return result
        if isinstance(v, (list, tuple, set)):
            return [int(item) for item in v]
        return []

    # Lowercase property aliases for compatibility across different calling conventions
    @property
    def bot_token(self) -> str:
        return self.BOT_TOKEN

    @property
    def admin_ids(self) -> list[int]:
        return self.ADMIN_IDS

    @property
    def database_url(self) -> str:
        return self.DATABASE_URL

    @property
    def savings_apr(self) -> float:
        return self.SAVINGS_APR

    @property
    def broker_fee_percent(self) -> float:
        return self.BROKER_FEE_PERCENT

    @property
    def debug(self) -> bool:
        return self.DEBUG


# Global settings singleton instance
settings = Settings()


def get_settings() -> Settings:
    """Dependency helper returning application settings."""
    return settings
