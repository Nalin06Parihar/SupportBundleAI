"""Standard API response contracts."""

from pydantic import Field

from app.models._common import StrictModel


class ErrorDetail(StrictModel):
    """Safe machine-readable and user-displayable error details."""

    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)


class ErrorResponse(StrictModel):
    """Standard application error response envelope."""

    error: ErrorDetail


class MessageResponse(StrictModel):
    """Simple informational API response."""

    message: str = Field(min_length=1, max_length=500)
