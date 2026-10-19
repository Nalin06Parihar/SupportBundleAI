"""Health-check route definitions."""

from fastapi import APIRouter

from app.core.config import get_settings
from app.models.health import HealthResponse

router = APIRouter()


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Check API health",
    description=(
        "Confirms that the API is running and local configuration loaded. "
        "This does not test Groq connectivity or diagnostic readiness."
    ),
)
def get_health() -> HealthResponse:
    """Return non-sensitive application health information."""
    settings = get_settings()
    return HealthResponse(
        status="healthy",
        application=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
    )
