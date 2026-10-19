"""Validation tests for evidence-backed AI analysis contracts."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.analysis import (
    AnalysisResult,
    ConfidenceLevel,
    EvidenceFinding,
    EvidenceReference,
    EvidenceSufficiency,
)
from app.models.evidence import MissingEvidence
from app.models.plan import CollectorName


def make_finding(**overrides: object) -> EvidenceFinding:
    values: dict[str, object] = {
        "title": "Repeated startup failure",
        "description": "The available evidence shows a repeated startup failure.",
        "category": "application",
        "confidence": ConfidenceLevel.MEDIUM,
        "evidence_references": [
            EvidenceReference(artifact_id=uuid4(), safe_excerpt="Sanitized detail.")
        ],
    }
    values.update(overrides)
    return EvidenceFinding(**values)


def test_finding_requires_evidence_reference() -> None:
    with pytest.raises(ValidationError):
        make_finding(evidence_references=[])


def test_invalid_confidence_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_finding(confidence="certain")


def test_unexpected_analysis_output_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_finding(guaranteed_root_cause=True)


def test_insufficient_evidence_contract_can_include_missing_evidence() -> None:
    result = AnalysisResult(
        workflow_id=uuid4(),
        issue_summary="Startup issue under investigation",
        evidence_sufficiency=EvidenceSufficiency.INSUFFICIENT,
        supplemental_plan_required=True,
        missing_evidence=[
            MissingEvidence(
                collector=CollectorName.APPLICATION_LOGS,
                reason="Logs were unavailable.",
                impact="The failure details are not confirmed.",
                required=True,
            )
        ],
    )
    assert result.supplemental_plan_required


def test_obvious_shell_command_in_suggested_next_step_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_finding(suggested_next_step="PowerShell.exe -Command Get-Process")
