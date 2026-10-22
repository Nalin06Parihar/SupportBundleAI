"""Contracts for evidence-backed analysis results."""

import re
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field, field_validator

from app.models._common import StrictModel, UTCDateTime, utc_now, validate_warnings
from app.models.evidence import MissingEvidence


class ConfidenceLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EvidenceReference(StrictModel):
    """Reference to an artifact and optionally one sanitized excerpt."""

    artifact_id: UUID
    excerpt_id: UUID | None = None
    safe_excerpt: str | None = Field(default=None, max_length=2000)


class EvidenceFinding(StrictModel):
    """A finding grounded in references to collected evidence."""

    finding_id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    category: str = Field(default="other",min_length=1, max_length=100)
    confidence: ConfidenceLevel
    evidence_references: list[EvidenceReference] = Field(min_length=1, max_length=50)
    unverified_assumptions: list[str] = Field(default_factory=list, max_length=20)
    suggested_next_step: str | None = Field(default=None, max_length=1000)

    @field_validator("unverified_assumptions")
    @classmethod
    def validate_assumptions(cls, assumptions: list[str]) -> list[str]:
        if any(not item or len(item) > 500 for item in assumptions):
            raise ValueError("Assumptions must contain between 1 and 500 characters.")
        return assumptions

    @field_validator("suggested_next_step")
    @classmethod
    def reject_obvious_commands(cls, suggestion: str | None) -> str | None:
        if suggestion is not None and re.search(
            r"(?im)^\s*(?:[$>]\s*)?"
            r"(?:powershell(?:\.exe)?|pwsh|cmd(?:\.exe)?|bash|sh|"
            r"curl|wget|invoke-expression|start-process|python(?:\.exe)?|"
            r"pip|rm|del|format|net|sc(?:\.exe)?)\b",
            suggestion,
        ):
            raise ValueError("Suggested next steps must not contain shell commands.")
        return suggestion


class EvidenceSufficiency(StrEnum):
    SUFFICIENT = "sufficient"
    PARTIALLY_SUFFICIENT = "partially_sufficient"
    INSUFFICIENT = "insufficient"


class AnalysisResult(StrictModel):
    """Structured analysis output based only on approved sanitized evidence."""

    analysis_id: UUID = Field(default_factory=uuid4)
    workflow_id: UUID
    issue_summary: str = Field(min_length=1, max_length=1000)
    findings: list[EvidenceFinding] = Field(default_factory=list, max_length=100)
    missing_evidence: list[MissingEvidence] = Field(default_factory=list, max_length=50)
    evidence_sufficiency: EvidenceSufficiency
    supplemental_plan_required: bool
    analyzed_at: UTCDateTime = Field(default_factory=utc_now)
    warnings: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("warnings")
    @classmethod
    def validate_analysis_warnings(cls, warnings: list[str]) -> list[str]:
        return validate_warnings(warnings)
