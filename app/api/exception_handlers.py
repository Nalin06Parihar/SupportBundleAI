"""Safe HTTP exception handlers for application-specific errors."""

import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.models.api import ErrorDetail, ErrorResponse
from app.core.exceptions import (
    AIProviderError,
    BundleCreationError,
    CollectorError,
    ConfigurationError,
    PolicyViolationError,
    SanitizationError,
    SupportBundleError,
)

logger = logging.getLogger(__name__)

ExceptionHandler = Callable[[Request, Exception], Awaitable[JSONResponse]]


def _create_error_handler(
    code: str,
    message: str,
    status_code: int,
) -> ExceptionHandler:
    async def handle_error(_request: Request, _exception: Exception) -> JSONResponse:
        logger.error("Handled application error: %s", code)
        response = ErrorResponse(
            error=ErrorDetail(code=code, message=message),
        )
        return JSONResponse(
            status_code=status_code,
            content=response.model_dump(),
        )

    return handle_error


def register_exception_handlers(app: FastAPI) -> None:
    """Register safe responses for the custom application exception hierarchy."""
    handlers: dict[type[SupportBundleError], tuple[str, str, int]] = {
        ConfigurationError: (
            "configuration_error",
            "Application configuration could not be loaded.",
            500,
        ),
        PolicyViolationError: (
            "policy_violation",
            "The requested operation is not permitted.",
            403,
        ),
        CollectorError: (
            "collector_error",
            "Diagnostic collection could not be completed.",
            500,
        ),
        SanitizationError: (
            "sanitization_error",
            "Diagnostic evidence could not be processed.",
            500,
        ),
        BundleCreationError: (
            "bundle_creation_error",
            "The diagnostic bundle could not be created.",
            500,
        ),
        AIProviderError: (
            "ai_provider_error",
            "The analysis provider could not complete the request.",
            502,
        ),
        SupportBundleError: (
            "support_bundle_error",
            "The application could not complete the request.",
            500,
        ),
    }

    for exception_type, (code, message, status_code) in handlers.items():
        app.add_exception_handler(
            exception_type,
            _create_error_handler(code, message, status_code),
        )
