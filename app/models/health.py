"""Response model for the application health endpoint."""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Non-sensitive status and application metadata."""

    status: Literal["healthy"]
    application: str
    version: str
    environment: str
