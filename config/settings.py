"""
Application configuration for AgentFi.

Uses pydantic-settings to load environment variables from a .env file or system environment.
"""

from datetime import date
from typing import Any, Optional, Union
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Settings schema for AgentFi.
    
    Reads variables from environment or .env file.
    Default model is gpt-4o-mini as required.
    """
    OPENAI_API_KEY: Optional[str] = ""
    OPENAI_MODEL: str = "gpt-4o-mini"

    # Data directories (Member 3: Data & Financial Intelligence)
    RAW_DATA_DIR: str = "data/raw"
    PROCESSED_DATA_DIR: str = "data/processed"

    # Tool source toggle: use Member 3's real finance engine tools or mock tools
    USE_REAL_FINANCE_TOOLS: bool = True

    # Fixed demo reference date (defaults to 2026-11-05; if empty, falls back to system date)
    DEMO_AS_OF_DATE: Optional[str] = "2026-11-05"

    # Configuration for pydantic-settings
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @field_validator("DEMO_AS_OF_DATE", mode="before")
    @classmethod
    def _validate_demo_date(cls, v: Any) -> Optional[str]:
        if v is None:
            return date.today().isoformat()
        if isinstance(v, date):
            return v.isoformat()
        s = str(v).strip()
        if not s:
            return date.today().isoformat()
        return s

    def get_as_of_date(self) -> date:
        """
        Returns DEMO_AS_OF_DATE as a date object.
        Falls back to system date if DEMO_AS_OF_DATE is None, empty, or whitespace.
        """
        val = self.DEMO_AS_OF_DATE
        if not val or not str(val).strip():
            return date.today()
        if isinstance(val, date):
            return val
        try:
            return date.fromisoformat(str(val).strip())
        except ValueError:
            return date.today()

    @property
    def as_of_date(self) -> date:
        """Property shortcut for get_as_of_date()."""
        return self.get_as_of_date()


# Instantiate a singleton settings object for application-wide use
settings = Settings()


def get_as_of_date() -> date:
    """Helper function to obtain current demo as-of date from settings."""
    return settings.get_as_of_date()

