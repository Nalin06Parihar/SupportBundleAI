"""FastAPI application declaration for SupportBundleAI."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from app.api.exception_handlers import register_exception_handlers
from app.api.health import router as health_router
from app.api.workflows import router as workflows_router
from app.core.config import get_settings
from app.core.exceptions import ConfigurationError
from app.core.logging_config import configure_logging
from app.database.engine import create_database_engine, initialize_database_engine
from app.database.session import create_session_factory

settings = get_settings()
logger = logging.getLogger(__name__)


class ApplicationMetadata(BaseModel):
    """Non-sensitive metadata returned from the root endpoint."""

    name: str
    version: str
    documentation: str


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Configure local application resources during startup and shutdown."""
    settings = get_settings()
    configure_logging()

    try:
        Path(settings.output_directory).mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError):
        raise ConfigurationError("The output directory could not be created.") from None

    if not Path(settings.controlled_environment_directory).is_dir():
        raise ConfigurationError(
            "The controlled-environment directory is unavailable."
        )

    engine = create_database_engine(settings.database_url)
    try:
        initialize_database_engine(engine)
    except (SQLAlchemyError, OSError, ValueError, RuntimeError):
        engine.dispose()
        raise ConfigurationError("The application database is unavailable.") from None

    _app.state.db_engine = engine
    _app.state.session_factory = create_session_factory(engine)
    logger.info(
        "Starting %s version %s in %s environment",
        settings.app_name,
        settings.app_version,
        settings.environment,
    )
    try:
        yield
    finally:
        logger.info("Shutting down %s", settings.app_name)
        engine.dispose()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Agentic diagnostic evidence collection and escalation assistant.",
    lifespan=lifespan,
)
register_exception_handlers(app)
app.include_router(health_router, prefix="/api", tags=["Health"])
app.include_router(workflows_router, prefix="/api", tags=["Workflows"])


@app.get(
    "/",
    response_model=ApplicationMetadata,
    summary="Get application metadata",
)
def get_application_metadata() -> ApplicationMetadata:
    """Return application identity and the Swagger documentation path."""
    current_settings = get_settings()
    return ApplicationMetadata(
        name=current_settings.app_name,
        version=current_settings.app_version,
        documentation="/docs",
    )
