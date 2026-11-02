"""
Application configuration for AgentFi.

Uses pydantic-settings to load environment variables from a .env file or system environment.
"""

from typing import Optional
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

    # Configuration for pydantic-settings
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


# Instantiate a singleton settings object for application-wide use
settings = Settings()

