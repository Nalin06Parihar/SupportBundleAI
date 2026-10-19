"""Models describing collector results, artifacts, and sanitized evidence."""

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from app.models._common import (
    StrictModel,
    UTCDateTime,
    utc_now,
    validate_json_mapping,
    validate_relative_path,
    validate_sha256,
    validate_warnings,
)
from app.models.plan import CollectorName


class CollectionStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class ArtifactType(StrEnum):
    SYSTEM_INFORMATION = "system_information"
    RESOURCE_METRICS = "resource_metrics"
    PROCESS_LIST = "process_list"
    SERVICE_STATUS = "service_status"
    APPLICATION_LOG = "application_log"
    NETWORK_INFORMATION = "network_information"
    APPLICATION_CONFIGURATION = "application_configuration"
    SANITIZATION_REPORT = "sanitization_report"
    ANALYSIS = "analysis"
    ESCALATION_SUMMARY = "escalation_summary"
    MANIFEST = "manifest"
    DIAGNOSTIC_BUNDLE = "diagnostic_bundle"
    OTHER = "other"


class SensitivityStatus(StrEnum):
    NOT_SCANNED = "not_scanned"
    AUTOMATICALLY_SANITIZED = "automatically_sanitized"
    RESIDUAL_RISK_DETECTED = "residual_risk_detected"
    AWAITING_HUMAN_REVIEW = "awaiting_human_review"
    APPROVED_FOR_EXTERNAL_ANALYSIS = "approved_for_external_analysis"
    EXCLUDED_FROM_EXTERNAL_ANALYSIS = "excluded_from_external_analysis"
    APPROVED_FOR_BUNDLE = "approved_for_bundle"
    BLOCKED = "blocked"


class ArtifactMetadata(StrictModel):
    """Safe metadata for one locally stored artifact."""

    artifact_id: UUID = Field(default_factory=uuid4)
    workflow_id: UUID
    name: str = Field(min_length=1, max_length=200)
    artifact_type: ArtifactType
    relative_path: str = Field(min_length=1, max_length=500)
    media_type: str = Field(min_length=1, max_length=100)
    size_bytes: int = Field(ge=0)
    sanitized: bool
    sensitivity_status: SensitivityStatus
    sha256: str | None = None
    created_at: UTCDateTime = Field(default_factory=utc_now)

    @field_validator("relative_path")
    @classmethod
    def validate_path(cls, path: str) -> str:
        return validate_relative_path(path)

    @field_validator("sha256")
    @classmethod
    def validate_digest(cls, digest: str | None) -> str | None:
        return validate_sha256(digest)

    @model_validator(mode="after")
    def validate_sensitivity(self) -> "ArtifactMetadata":
        if self.sensitivity_status in {
            SensitivityStatus.APPROVED_FOR_EXTERNAL_ANALYSIS,
            SensitivityStatus.APPROVED_FOR_BUNDLE,
        } and not self.sanitized:
            raise ValueError("Only sanitized artifacts may be approved.")
        return self


class SafeCollectorError(StrictModel):
    """Safe, user-displayable collector error details."""

    code: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
    message: str = Field(
        min_length=1,
        max_length=500,
        description="Safe single-line message; never an exception dump or stack trace.",
    )

    @field_validator("message")
    @classmethod
    def reject_multiline_message(cls, message: str) -> str:
        if "\n" in message or "\r" in message:
            raise ValueError("Collector error messages must be a single safe line.")
        return message


class CollectorResult(StrictModel):
    """Structured outcome from a future approved local collector."""

    result_id: UUID = Field(default_factory=uuid4)
    workflow_id: UUID
    step_id: UUID
    collector: CollectorName
    status: CollectionStatus
    collected_at: UTCDateTime = Field(default_factory=utc_now)
    data: dict[str, object] = Field(default_factory=dict)
    artifacts: list[ArtifactMetadata] = Field(default_factory=list, max_length=100)
    warnings: list[str] = Field(default_factory=list, max_length=20)
    error: SafeCollectorError | None = None
    duration_ms: int = Field(ge=0)

    @field_validator("data")
    @classmethod
    def validate_data(cls, data: dict[str, object]) -> dict[str, object]:
        return validate_json_mapping(data)

    @field_validator("warnings")
    @classmethod
    def validate_result_warnings(cls, warnings: list[str]) -> list[str]:
        return validate_warnings(warnings)

    @model_validator(mode="after")
    def validate_error_status(self) -> "CollectorResult":
        if self.status == CollectionStatus.FAILED and self.error is None:
            raise ValueError("Failed collector results require safe error details.")
        if self.status == CollectionStatus.SUCCESS and self.error is not None:
            raise ValueError("Successful collector results cannot include a fatal error.")
        return self


class MissingEvidence(StrictModel):
    """Evidence that would be useful but was not collected or was unavailable."""

    collector: CollectorName
    reason: str = Field(min_length=1, max_length=500)
    impact: str = Field(min_length=1, max_length=500)
    required: bool


class RedactionCategory(StrEnum):
    EMAIL = "email"
    IP_ADDRESS = "ip_address"
    USER_PATH = "user_path"
    CREDENTIAL = "credential"
    API_KEY = "api_key"
    AUTHORIZATION_HEADER = "authorization_header"
    CONNECTION_STRING = "connection_string"
    PRIVATE_KEY = "private_key"
    CUSTOMER_IDENTIFIER = "customer_identifier"
    TENANT_IDENTIFIER = "tenant_identifier"
    INTERNAL_PROJECT_NAME = "internal_project_name"
    BUSINESS_CONTEXT = "business_context"
    OTHER = "other"


class RedactionCount(StrictModel):
    category: RedactionCategory
    count: int = Field(ge=0)


class ResidualRiskLevel(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKED = "blocked"


class ResidualRiskFlag(StrictModel):
    flag_id: UUID = Field(default_factory=uuid4)
    artifact_id: UUID
    category: RedactionCategory
    risk_level: ResidualRiskLevel
    safe_description: str = Field(min_length=1, max_length=500)
    excerpt_id: UUID | None = None
    requires_human_review: bool = True


class SanitizationStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"


class SanitizationReport(StrictModel):
    """Summary of local sanitization results without raw sensitive values."""

    report_id: UUID = Field(default_factory=uuid4)
    workflow_id: UUID
    status: SanitizationStatus
    files_processed: int = Field(ge=0)
    total_redactions: int = Field(ge=0)
    redactions_by_category: list[RedactionCount] = Field(max_length=20)
    residual_risk_flags: list[ResidualRiskFlag] = Field(
        default_factory=list,
        max_length=500,
    )
    approved_artifact_ids: list[UUID] = Field(default_factory=list, max_length=500)
    blocked_artifact_ids: list[UUID] = Field(default_factory=list, max_length=500)
    warnings: list[str] = Field(default_factory=list, max_length=20)
    created_at: UTCDateTime = Field(default_factory=utc_now)

    @field_validator("warnings")
    @classmethod
    def validate_report_warnings(cls, warnings: list[str]) -> list[str]:
        return validate_warnings(warnings)

    @model_validator(mode="after")
    def validate_report_consistency(self) -> "SanitizationReport":
        if self.total_redactions != sum(
            item.count for item in self.redactions_by_category
        ):
            raise ValueError("Redaction count must match category totals.")
        approved = set(self.approved_artifact_ids)
        blocked = set(self.blocked_artifact_ids)
        if approved & blocked:
            raise ValueError("Artifacts cannot be both approved and blocked.")
        if self.status == SanitizationStatus.BLOCKED and not (
            self.blocked_artifact_ids or self.warnings
        ):
            raise ValueError(
                "Blocked reports require blocked artifacts or a safe warning."
            )
        return self


class SanitizedEvidenceExcerpt(StrictModel):
    """Bounded candidate excerpt containing already-sanitized content."""

    excerpt_id: UUID = Field(default_factory=uuid4)
    artifact_id: UUID
    sequence_number: int = Field(ge=0)
    content: str = Field(min_length=1, max_length=10_000)
    redaction_count: int = Field(ge=0)
    residual_risk_level: ResidualRiskLevel
    approved_for_external_analysis: bool = False

    @model_validator(mode="after")
    def prevent_approved_blocked_excerpt(self) -> "SanitizedEvidenceExcerpt":
        if (
            self.approved_for_external_analysis
            and self.residual_risk_level == ResidualRiskLevel.BLOCKED
        ):
            raise ValueError("Blocked excerpts cannot be approved externally.")
        return self
