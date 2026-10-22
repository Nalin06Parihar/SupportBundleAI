"""Focused tests for deterministic local evidence sanitization."""

import json
from uuid import uuid4

from app.core.config import Settings
from app.database.models import ArtifactRecord
from app.models.evidence import (
    ArtifactMetadata,
    ArtifactType,
    RedactionCategory,
    SanitizationStatus,
    SensitivityStatus,
)
from app.models.issue import IssueRequest
from app.repositories.artifacts import ArtifactRepository
from app.repositories.workflows import WorkflowRepository
from app.scenarios.loader import ScenarioLoader
from app.security.sanitizer import EvidenceSanitizationService, sanitize_text


def test_sanitize_text_redacts_sensitive_patterns_idempotently() -> None:
    original = (
        "password=hunter2 Authorization: Bearer abcdefghijklmnop "
        "alice@example.test 10.2.3.4 2001:db8::1 C:\\Users\\alice\\config.json "
        "postgresql://user:secret@db.example.test/support"
    )

    sanitized, counts = sanitize_text(original)
    sanitized_again, repeated_counts = sanitize_text(sanitized)

    assert sanitized_again == sanitized
    assert not repeated_counts
    assert "hunter2" not in sanitized
    assert "abcdefghijklmnop" not in sanitized
    assert "alice@example.test" not in sanitized
    assert "10.2.3.4" not in sanitized
    assert "2001:db8::1" not in sanitized
    assert "C:\\Users\\alice" not in sanitized
    assert "user:secret" not in sanitized
    assert counts[RedactionCategory.CREDENTIAL] == 1
    assert counts[RedactionCategory.AUTHORIZATION_HEADER] == 1
    assert counts[RedactionCategory.EMAIL] == 1
    assert counts[RedactionCategory.IP_ADDRESS] == 2
    assert counts[RedactionCategory.USER_PATH] == 1
    assert counts[RedactionCategory.CONNECTION_STRING] == 1


def test_json_sanitization_preserves_source_and_stores_only_metadata(
    session_factory,
    tmp_path,
) -> None:
    source_root = tmp_path / "controlled"
    source_path = source_root / "logs" / "evidence.json"
    source_path.parent.mkdir(parents=True)
    original_data = {
        "service": {"password": "secret-value", "nested": {"api_token": "token-value"}},
        "customer_id": "customer-482",
        "status": "degraded",
    }
    original_bytes = json.dumps(original_data).encode()
    source_path.write_bytes(original_bytes)
    workflow_id = uuid4()
    issue = IssueRequest(
        title="Synthetic service issue",
        description="A service is degraded.",
        application_name="SampleApp",
        symptoms=["Degraded response"],
    )
    WorkflowRepository(session_factory).create(issue, workflow_id=workflow_id)
    repository = ArtifactRepository(session_factory)
    repository.register(
        ArtifactMetadata(
            workflow_id=workflow_id,
            name="evidence.json",
            artifact_type=ArtifactType.APPLICATION_CONFIGURATION,
            relative_path="logs/evidence.json",
            media_type="application/json",
            size_bytes=len(original_bytes),
            sanitized=False,
            sensitivity_status=SensitivityStatus.NOT_SCANNED,
        )
    )
    output_root = tmp_path / "output"
    sanitizer = EvidenceSanitizationService(
        repository,
        scenario_loader=ScenarioLoader(source_root),
        settings=Settings(
            _env_file=None,
            controlled_environment_directory=str(source_root),
            output_directory=str(output_root),
        ),
    )

    report, excerpts, sanitized_artifacts = sanitizer.sanitize_workflow(workflow_id)
    for artifact in sanitized_artifacts:
        repository.register_if_missing(artifact)
    repeated_report, _, repeated_artifacts = sanitizer.sanitize_workflow(workflow_id)
    for artifact in repeated_artifacts:
        repository.register_if_missing(artifact)

    assert report.status == SanitizationStatus.COMPLETED
    assert repeated_report.report_id == report.report_id
    assert repeated_artifacts[0].artifact_id == sanitized_artifacts[0].artifact_id
    assert report.total_redactions == 3
    assert any(
        flag.category == RedactionCategory.CUSTOMER_IDENTIFIER
        for flag in report.residual_risk_flags
    )
    assert len(excerpts) == 1
    assert "secret-value" not in excerpts[0].content
    assert "token-value" not in excerpts[0].content
    assert "customer-482" not in excerpts[0].content
    sanitized_path = output_root / sanitized_artifacts[0].relative_path
    assert sanitized_path.resolve().is_relative_to(output_root.resolve())
    assert source_path.read_bytes() == original_bytes
    assert sanitizer.verify_artifact(sanitized_artifacts[0])
    sanitized_path.write_text("tampered alice@example.test", encoding="utf-8")
    assert not sanitizer.verify_artifact(sanitized_artifacts[0])
    with session_factory() as session:
        records = list(session.query(ArtifactRecord).all())
    assert len(records) == 2
    assert all(
        raw not in repr(record)
        for record in records
        for raw in ("secret-value", "token-value", "customer-482")
    )


def test_unsupported_artifact_is_blocked_without_copying(
    session_factory,
    tmp_path,
) -> None:
    source_root = tmp_path / "controlled"
    source_path = source_root / "logs" / "evidence.bin"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"unrecognized binary content")
    workflow_id = uuid4()
    issue = IssueRequest(
        title="Synthetic service issue",
        description="A service is degraded.",
        application_name="SampleApp",
        symptoms=["Degraded response"],
    )
    WorkflowRepository(session_factory).create(issue, workflow_id=workflow_id)
    repository = ArtifactRepository(session_factory)
    repository.register(
        ArtifactMetadata(
            workflow_id=workflow_id,
            name="evidence.bin",
            artifact_type=ArtifactType.OTHER,
            relative_path="logs/evidence.bin",
            media_type="application/octet-stream",
            size_bytes=source_path.stat().st_size,
            sanitized=False,
            sensitivity_status=SensitivityStatus.NOT_SCANNED,
        )
    )
    output_root = tmp_path / "output"
    sanitizer = EvidenceSanitizationService(
        repository,
        scenario_loader=ScenarioLoader(source_root),
        settings=Settings(
            _env_file=None,
            controlled_environment_directory=str(source_root),
            output_directory=str(output_root),
        ),
    )

    report, excerpts, sanitized_artifacts = sanitizer.sanitize_workflow(workflow_id)

    assert report.status == SanitizationStatus.BLOCKED
    assert report.blocked_artifact_ids == [sanitized_artifacts[0].artifact_id]
    assert not excerpts
    assert not sanitized_artifacts[0].sanitized
    assert sanitized_artifacts[0].sensitivity_status == SensitivityStatus.BLOCKED
    assert not (output_root / "workflows" / str(workflow_id) / "sanitized").exists()
