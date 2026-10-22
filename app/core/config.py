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

    groq_api_key: str | None = Field(default=None, repr=False)
    groq_model: str | None = None
    groq_request_timeout_seconds: float = Field(default=30, gt=0, le=300)
    groq_max_retries: int = Field(default=2, ge=0, le=5)
    groq_planning_temperature: float = Field(default=0, ge=0, le=2)
    groq_max_output_tokens: int | None = Field(default=None, gt=0, le=32768)
    groq_analysis_max_output_tokens: int = Field(default=4096, gt=0, le=32768)

    max_tool_calls: int = Field(default=6, ge=1, le=10)
    tool_timeout_seconds: int = Field(default=15, ge=1, le=60)
    max_log_file_size_mb: float = Field(default=5, gt=0)
    analysis_payload_max_bytes: int = Field(default=65536, ge=1024, le=1_000_000)

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
