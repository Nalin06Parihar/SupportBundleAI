"""Validation tests for collector results and sanitized evidence metadata."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.evidence import (
    ArtifactMetadata,
    ArtifactType,
    CollectionStatus,
    CollectorResult,
    RedactionCategory,
    RedactionCount,
    ResidualRiskFlag,
    ResidualRiskLevel,
    SanitizationReport,
    SanitizationStatus,
    SanitizedEvidenceExcerpt,
    SensitivityStatus,
)
from app.models.plan import CollectorName


def make_artifact(**overrides: object) -> ArtifactMetadata:
    values: dict[str, object] = {
        "workflow_id": uuid4(),
        "name": "system.json",
        "artifact_type": ArtifactType.SYSTEM_INFORMATION,
        "relative_path": "evidence/system.json",
        "media_type": "application/json",
        "size_bytes": 10,
        "sanitized": False,
        "sensitivity_status": SensitivityStatus.NOT_SCANNED,
    }
    values.update(overrides)
    return ArtifactMetadata(**values)


def test_relative_artifact_path_is_accepted() -> None:
    assert make_artifact(relative_path="evidence\\system.json").relative_path == (
        "evidence/system.json"
    )


@pytest.mark.parametrize(
    "path",
    [
        "/private/file.log",
        "C:\\private\\file.log",
        "\\\\server\\share\\file.log",
        "evidence/../../private/file.log",
        "evidence\\..\\private\\file.log",
        "evidence/NUL.txt",
        "evidence/file\x00.log",
    ],
)
def test_unsafe_artifact_paths_are_rejected(path: str) -> None:
    with pytest.raises(ValidationError):
        make_artifact(relative_path=path)


def test_negative_file_size_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_artifact(size_bytes=-1)


def test_invalid_sha256_is_rejected_and_valid_digest_normalized() -> None:
    with pytest.raises(ValidationError):
        make_artifact(sha256="not-a-hash")

    artifact = make_artifact(sha256="A" * 64)
    assert artifact.sha256 == "a" * 64


def make_result(**overrides: object) -> CollectorResult:
    values: dict[str, object] = {
        "workflow_id": uuid4(),
        "step_id": uuid4(),
        "collector": CollectorName.SYSTEM_INFO,
        "status": CollectionStatus.SUCCESS,
        "duration_ms": 5,
        "data": {"platform": "synthetic"},
    }
    values.update(overrides)
    return CollectorResult(**values)


def test_failed_collector_result_requires_safe_error() -> None:
    with pytest.raises(ValidationError):
        make_result(status=CollectionStatus.FAILED)


def test_failed_collector_result_accepts_safe_error() -> None:
    result = make_result(
        status=CollectionStatus.FAILED,
        error={"code": "collector_unavailable", "message": "Collector unavailable."},
    )
    assert result.error is not None


def test_negative_collector_duration_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_result(duration_ms=-1)


def test_unknown_collector_is_rejected_in_result() -> None:
    with pytest.raises(ValidationError):
        make_result(collector="run_arbitrary_command")


def test_raw_exception_object_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_result(
            status=CollectionStatus.FAILED,
            error=ValueError("internal exception"),
        )


def test_non_json_collector_metadata_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_result(data={"object": object()})


def test_valid_sanitization_report_is_accepted() -> None:
    report = SanitizationReport(
        workflow_id=uuid4(),
        status=SanitizationStatus.COMPLETED,
        files_processed=1,
        total_redactions=2,
        redactions_by_category=[
            RedactionCount(category=RedactionCategory.EMAIL, count=2)
        ],
    )
    assert report.total_redactions == 2


def test_redaction_totals_must_match_categories() -> None:
    with pytest.raises(ValidationError):
        SanitizationReport(
            workflow_id=uuid4(),
            status=SanitizationStatus.COMPLETED,
            files_processed=1,
            total_redactions=1,
            redactions_by_category=[
                RedactionCount(category=RedactionCategory.EMAIL, count=2)
            ],
        )


def test_artifact_cannot_be_both_approved_and_blocked() -> None:
    artifact_id = uuid4()
    with pytest.raises(ValidationError):
        SanitizationReport(
            workflow_id=uuid4(),
            status=SanitizationStatus.PARTIAL,
            files_processed=1,
            total_redactions=0,
            redactions_by_category=[],
            approved_artifact_ids=[artifact_id],
            blocked_artifact_ids=[artifact_id],
        )


def test_residual_risk_flag_stores_safe_description_not_sensitive_value() -> None:
    flag = ResidualRiskFlag(
        artifact_id=uuid4(),
        category=RedactionCategory.CUSTOMER_IDENTIFIER,
        risk_level=ResidualRiskLevel.MEDIUM,
        safe_description="Possible customer identifier detected.",
    )
    assert "sensitive value" not in flag.safe_description


def test_excerpt_defaults_to_not_approved_for_external_analysis() -> None:
    excerpt = SanitizedEvidenceExcerpt(
        artifact_id=uuid4(),
        sequence_number=0,
        content="Sanitized excerpt.",
        redaction_count=1,
        residual_risk_level=ResidualRiskLevel.LOW,
    )
    assert excerpt.approved_for_external_analysis is False
