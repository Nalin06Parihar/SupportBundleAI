"""API and application-lifecycle tests."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.exception_handlers import register_exception_handlers
from app.core.config import get_settings
from app.core.exceptions import (
    AIProviderError,
    BundleCreationError,
    CollectorError,
    ConfigurationError,
    PolicyViolationError,
    SanitizationError,
    SupportBundleError,
)
from app.main import app


@pytest.fixture
def configured_client_environment(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Point lifecycle-managed paths at isolated temporary test directories."""
    controlled_environment = tmp_path / "controlled_environment"
    controlled_environment.mkdir()
    output_directory = tmp_path / "output"

    monkeypatch.setenv("APP_NAME", "SupportBundleAI Test")
    monkeypatch.setenv("APP_VERSION", "9.8.7")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("GROQ_API_KEY", "test-placeholder-key")
    monkeypatch.setenv("GROQ_MODEL", "test-placeholder-model")
    monkeypatch.setenv("OUTPUT_DIRECTORY", str(output_directory))
    monkeypatch.setenv(
        "CONTROLLED_ENVIRONMENT_DIRECTORY",
        str(controlled_environment),
    )
    monkeypatch.setenv(
        "DATABASE_URL",
        f"sqlite:///{(tmp_path / 'health-test.db').as_posix()}",
    )
    get_settings.cache_clear()
    yield {
        "controlled_environment": controlled_environment,
        "output_directory": output_directory,
    }
    get_settings.cache_clear()


def test_root_returns_application_metadata(configured_client_environment) -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "name": "SupportBundleAI Test",
        "version": "9.8.7",
        "documentation": "/docs",
    }


def test_health_returns_configured_non_sensitive_status(
    configured_client_environment,
) -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "status": "healthy",
        "application": "SupportBundleAI Test",
        "version": "9.8.7",
        "environment": "test",
    }

    response_text = response.text
    assert "GROQ_API_KEY" not in response_text
    assert "test-placeholder-key" not in response_text
    assert "sqlite:///" not in response_text
    assert str(configured_client_environment["output_directory"]) not in response_text
    assert (
        str(configured_client_environment["controlled_environment"])
        not in response_text
    )


def test_unknown_endpoint_returns_not_found(configured_client_environment) -> None:
    with TestClient(app) as client:
        response = client.get("/api/does-not-exist")

    assert response.status_code == 404


def test_health_endpoint_is_in_openapi_schema(
    configured_client_environment,
) -> None:
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()

    assert "/api/health" in schema["paths"]
    assert "get" in schema["paths"]["/api/health"]


def test_lifespan_creates_output_directory(configured_client_environment) -> None:
    output_directory = configured_client_environment["output_directory"]
    assert not output_directory.exists()

    with TestClient(app):
        assert output_directory.is_dir()


def test_lifespan_rejects_missing_controlled_environment(
    configured_client_environment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CONTROLLED_ENVIRONMENT_DIRECTORY",
        str(configured_client_environment["controlled_environment"] / "missing"),
    )
    get_settings.cache_clear()

    with pytest.raises(ConfigurationError) as error:
        with TestClient(app):
            pass

    assert str(error.value) == "The controlled-environment directory is unavailable."
    assert str(configured_client_environment["controlled_environment"]) not in str(
        error.value
    )


def test_lifespan_rejects_uncreatable_output_directory(
    configured_client_environment,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    output_file = tmp_path / "output-file"
    output_file.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("OUTPUT_DIRECTORY", str(output_file))
    get_settings.cache_clear()

    with pytest.raises(ConfigurationError) as error:
        with TestClient(app):
            pass

    assert str(error.value) == "The output directory could not be created."
    assert str(tmp_path) not in str(error.value)


@pytest.mark.parametrize(
    ("exception_type", "expected_status", "expected_code", "expected_message"),
    [
        (
            ConfigurationError,
            500,
            "configuration_error",
            "Application configuration could not be loaded.",
        ),
        (
            PolicyViolationError,
            403,
            "policy_violation",
            "The requested operation is not permitted.",
        ),
        (
            CollectorError,
            500,
            "collector_error",
            "Diagnostic collection could not be completed.",
        ),
        (
            SanitizationError,
            500,
            "sanitization_error",
            "Diagnostic evidence could not be processed.",
        ),
        (
            BundleCreationError,
            500,
            "bundle_creation_error",
            "The diagnostic bundle could not be created.",
        ),
        (
            AIProviderError,
            502,
            "ai_provider_error",
            "The analysis provider could not complete the request.",
        ),
        (
            SupportBundleError,
            500,
            "support_bundle_error",
            "The application could not complete the request.",
        ),
    ],
)
def test_custom_exception_responses_are_safe(
    exception_type: type[SupportBundleError],
    expected_status: int,
    expected_code: str,
    expected_message: str,
) -> None:
    test_app = FastAPI()
    register_exception_handlers(test_app)

    @test_app.get("/failure")
    def raise_application_error() -> None:
        raise exception_type("test-placeholder-key C:\\private\\internal")

    with TestClient(test_app) as client:
        response = client.get("/failure")

    assert response.status_code == expected_status
    assert response.json() == {
        "error": {
            "code": expected_code,
            "message": expected_message,
        }
    }
    assert "test-placeholder-key" not in response.text
    assert "C:\\private\\internal" not in response.text
