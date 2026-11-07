"""
Application configuration for the AgentFi Backend service.

Uses pydantic-settings to load environment variables from system environment
or root .env file.
"""

from pathlib import Path
from typing import List, Union
from dotenv import load_dotenv
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve absolute path to project root .env so it loads regardless of terminal working directory
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOTENV_PATH = PROJECT_ROOT / ".env"

# Explicitly populate environment variables from .env to bypass IDE terminal injection issues
load_dotenv(dotenv_path=DOTENV_PATH, override=True)


class Settings(BaseSettings):
    """
    Backend settings schema with environment-specific validation.
    """
    # General App Config
    APP_NAME: str = "AgentFi API"
    ENVIRONMENT: str = "local"  # local | staging | production
    DEBUG: bool = True
    API_PREFIX: str = "/api"

    # Database Configuration (Supabase PostgreSQL via Session Pooler)
    # Required format: postgresql+psycopg://postgres.<ref>:<password>@<pooler-host>:5432/postgres
    DATABASE_URL: str = "postgresql+psycopg://postgres.placeholder:placeholder@placeholder.pooler.supabase.com:5432/postgres"
    USE_TRANSACTION_POOLER: bool = False
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5
    DB_POOL_RECYCLE: int = 1800  # 30 minutes to stay fresh with pooler

    # JWT Authentication Configuration
    JWT_SECRET_KEY: str = "CHANGE_ME_IN_PRODUCTION_SECRET_KEY_PLACEHOLDER_64CHARS_MINIMUM_SECURITY"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # CORS Origins (Comma-separated string or list of URLs)
    CORS_ORIGINS: Union[List[str], str] = ["http://localhost:3000"]

    # Placeholders for integration credentials (coexist cleanly with other components)
    OPENAI_API_KEY: str = ""
    EMAIL_HOST: str = "smtp.example.com"
    EMAIL_PORT: int = 587
    EMAIL_USERNAME: str = ""
    EMAIL_PASSWORD: str = ""
    EMAIL_FROM: str = "no-reply@agentfi.local"

    model_config = SettingsConfigDict(
        env_file=DOTENV_PATH,
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        """
        Enforce strict security validation in production:
        Refuse to start if JWT_SECRET_KEY or DATABASE_URL still hold placeholder values.
        """
        if self.ENVIRONMENT.lower() == "production":
            placeholder_indicators = ["change_me", "placeholder", "secret_key"]
            key_lower = self.JWT_SECRET_KEY.lower()
            if any(ind in key_lower for ind in placeholder_indicators) or len(self.JWT_SECRET_KEY) < 32:
                raise ValueError(
                    "FATAL SECURITY VIOLATION: Cannot start in production with an insecure or default "
                    "JWT_SECRET_KEY! Must be at least 32 characters and securely generated."
                )
            if "placeholder" in self.DATABASE_URL.lower():
                raise ValueError(
                    "FATAL CONFIGURATION ERROR: Cannot start in production with placeholder DATABASE_URL!"
                )
        return self


# Application singleton settings instance
settings = Settings()

