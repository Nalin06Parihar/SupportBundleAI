"""Application settings loaded from environment variables and the .env file."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "SupportBundleAI"
    app_version: str = "0.1.0"
    environment: str = "development"

    groq_api_key: str
    groq_model: str

    max_tool_calls: int = Field(default=6, ge=1, le=10)
    tool_timeout_seconds: int = Field(default=15, ge=1, le=60)
    max_log_file_size_mb: float = Field(default=5, gt=0)

    output_directory: str = "output"
    controlled_environment_directory: str = "controlled_environment"
    database_url: str = Field(
        default="sqlite:///./supportbundle.db",
        repr=False,
    )


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings instance."""
    return Settings()
